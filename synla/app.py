"""Ties it together: reads activity, applies what Synla has learned and what you have answered."""
import hashlib
import secrets
import time as clock
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from . import holidays, protect, recorder, report, roles, workload
from .blocks import _subtract, blocks, segments
from .context import glue, is_private, thing
from .classify import ASK_BELOW, Classifier
from .blocks import NOISE
from .context import source_key
from .model import INVISIBLE, KINDS, MANUAL_APP, MANUAL_TITLE, NOT_WORK, UNKNOWN, Segment, reserved
from .record import FORMAT, RecordBook, renamed
from .source import AW_URL, ActivityWatch, Auto, JsonFile, SynlaRecorder, day_range, on_this_computer
from .store import utc_key

LEARN_MIN = 1.0          # minutes; a window you only glanced at is not learned from
DELETED = "Window name deleted"
AUTO_SEAL_AFTER = 7      # days after a week ends, an unsealed week is sealed as it stands
CACHE_SECONDS = 20       # a page asks for the same days several times; read them once
QUESTIONS_PER_DAY = 5    # Synla asks about at most this many windows a day, unless you ask for more
ASK_MIN = 5.0            # minutes; smaller unsure windows keep Synla's best guess instead of becoming a question
FOLD_AFTER = 28         # days: a choice you have not used for this long moves under More
SHOW_PROJECTS = 5       # projects shown as buttons; the rest are one search away
GAP_MIN = 30.0           # minutes away from the computer inside your workday before Synla asks what it was
OFF = {"vacation": "Vacation", "sick": "Sick", "sickchild": "Sick child", "timeoff": "Time off"}
SPOT_MIN = 10.0          # minutes; the daily spot check is about a window Synla sorted on its own for this long
OFF_ASK_DAYS = 14        # an empty workday is asked about ("Were you off?") for two weeks, then left alone
WIDE = timedelta(hours=14)   # a day abroad can start up to 14 hours before or after a day at home
LUNCH = (time(10, 30), time(13, 0), 45.0)   # away starting in this window and no longer than this: a break, not asked
LIKE_BREAK = 30          # minutes: away starting this close to two earlier "Not work" answers is not asked either


def _minutes(t: datetime) -> int:
    return int(t.utcoffset().total_seconds() // 60)


def _clock(utc: str, tz: int | None) -> datetime:
    """A stored moment on the clock it was lived on (minutes ahead of UTC), or on this computer's clock."""
    t = datetime.fromisoformat(utc)
    return t.astimezone(timezone(timedelta(minutes=tz))) if tz is not None else t.astimezone()


def _on_clock(d: date, t: time, tz=None) -> datetime:
    """Wall clock time t on day d: on this computer's clock, unless the day was lived on another one (abroad)."""
    local = datetime.combine(d, t).astimezone()
    return local if tz is None or local.utcoffset() == tz.utcoffset(None) else datetime.combine(d, t, tz)


def _own_day(s: Segment, d: date) -> list:
    """The part of a stretch that falls on day d, on the clock it was recorded on."""
    tz = s.start.tzinfo
    a, b = datetime.combine(d, time(0), tz), datetime.combine(d + timedelta(days=1), time(0), tz)
    lo, hi = max(s.start, a), min(s.end, b)
    if (hi - lo).total_seconds() < 1:
        return []
    return [Segment(lo.astimezone(tz), hi.astimezone(tz), s.app, s.title, s.call)]


def _cut(segs: list, holes: list) -> list:
    """Take holes out of stretches, keeping each stretch's own clock."""
    return [Segment(a.astimezone(s.start.tzinfo), b.astimezone(s.start.tzinfo), s.app, s.title, s.call)
            for s in segs for a, b in _subtract(s.start, s.end, holes) if (b - a).total_seconds() >= 1]


class Problem(ValueError):
    """Something Synla cannot do, said in words for you. The pages show the text as it is."""


class Synla:
    def __init__(self, store, notes: bool = True):
        self.store = store
        self.notes = notes        # False in the recorder, so only the pages write what keeps coming back
        self.record = RecordBook(store)
        self.home = None          # the day the Today page shows; None is today (the demo sets its own week)
        self.last_undo = None     # token for taking back the latest change
        self._undo = None
        self.reload()

    def reload(self):
        self._cache = {}
        self._guesses = {}
        self.config = self.store.config()
        self.classifier = Classifier(self.config, self.store.memory(), self.store.hasher())
        src = self.config.get("source", "auto")
        url = self.config.get("activityWatchUrl", AW_URL)
        if not on_this_computer(url):
            self.store.damaged["activityWatchUrl"] = ("activityWatchUrl in config.json points to another computer. "
                                                      f"Synla only reads ActivityWatch on this computer, at {AW_URL}.")
            url = AW_URL
        self.source = {"auto": lambda: Auto(self.store, url), "synla": lambda: SynlaRecorder(self.store),
                       "activitywatch": lambda: ActivityWatch(url)}.get(src, lambda: JsonFile(src))()

    @property
    def projects(self) -> list:
        """Every project in your list, archived ones too, so their hours keep a name."""
        return list(self.config.get("projects", {}))

    @property
    def active(self) -> list:
        """The projects Synla offers and guesses: your list without the archived ones."""
        return [p for p, v in self.config.get("projects", {}).items() if not (isinstance(v, dict) and v.get("archived"))]

    def named(self, project: str) -> str:
        """A project's name today: a week sealed before a rename has the old name."""
        return (self.config.get("renamed") or {}).get(project, project)

    # ---- the first time --------------------------------------------------------------------------------------
    def needs_setup(self) -> bool:
        """The first time: only the starting project, no role, and setup not done. Someone who set Synla up by hand
        in config.json is never sent there."""
        c = self.config
        return (not c.get("setupDone") and not c.get("role") and set(self.projects) <= {"Internal"}
                and c.get("source", "auto") in ("auto", "synla", "activitywatch"))

    def setup(self, role: str, projects: list, start: str, end: str, weekly: float, calls: bool):
        """Saves the setup page: your role, projects with their words, your workday and the microphone check."""
        if role not in roles.ROLES:
            raise Problem("Pick the work that fits you best.")
        if not projects:
            raise Problem("Add at least one project.")
        try:
            if time.fromisoformat(start) >= time.fromisoformat(end):
                raise Problem("The workday must end after it starts.")
        except ValueError:
            raise Problem("Write the times as hours and minutes, for example 08:00.") from None
        if not 1 <= weekly <= 80:
            raise Problem("A normal week is between 1 and 80 hours.")
        c = self.store.config()
        given = {}
        used = {v.get("project") for v in self.store.decisions().values() if isinstance(v, dict)}
        used |= {e.get("project") for e in self.store.manual() if isinstance(e, dict)}
        for item in projects:                  # (name, words) or (name, words, code from an imported list)
            name, words, code = (tuple(item) + ("",))[:3]
            if reserved(name):
                raise Problem("Synla uses that name itself. Pick another.")
            if name.lower() in {p.lower() for p in given}:
                raise Problem(f"{name} is in the list twice.")
            old = c.get("projects", {}).get(name)
            given[name] = {k: v for k, v in (old if isinstance(old, dict) else {}).items() if k != "archived"}
            given[name]["keywords"] = words
            if code:
                given[name]["code"] = code
        for p, v in c.get("projects", {}).items():      # a project with hours is archived, never dropped
            if p not in given and p in used:
                given[p] = dict(v if isinstance(v, dict) else {}, archived=True)
        c.update(role=role, projects=given, workday={"start": start, "end": end}, weeklyHours=weekly,
                 detectCalls=bool(calls), setupDone=True)
        self.store.save_config(c)
        self.reload()

    # ---- your projects ---------------------------------------------------------------------------------------
    def save_projects(self, rows: list):
        """The Projects page, in your order: name, words, code and archived for each. A new name for an existing
        project is a rename: your answers, added time, what Synla learned and the log follow it. Sealed weeks keep
        the name they had, and are not shown as changed because of it."""
        c = self.store.config()
        now = c.get("projects", {})
        lower = {p.lower(): p for p in now}
        out, renames, used = {}, {}, set()
        for r in rows:
            name, old = r["name"], r.get("old")
            if not name:
                raise Problem("Every project needs a name.")
            if reserved(name):
                raise Problem("Synla uses that name itself. Pick another.")
            if name.lower() in used:
                raise Problem(f"{name} is in the list twice.")
            used.add(name.lower())
            if old is not None and old not in now:
                raise Problem("The list changed in the meantime. Reload the page and try again.")
            other = lower.get(name.lower())
            if other is not None and other != old:
                raise Problem(f"There is already a project called {other}.")
            entry = dict(now.get(old) or {}) if old is not None else {}
            if not isinstance(entry, dict):
                entry = {}
            entry["keywords"] = r.get("words", [])
            for k in ("code", "archived"):
                if r.get(k):
                    entry[k] = r[k]
                else:
                    entry.pop(k, None)
            out[name] = entry
            if old is not None and old != name:
                renames[old] = name
        out.update({p: v for p, v in now.items() if p not in out and p not in renames})
        c["projects"] = out
        if renames:
            self._rename(renames)
            c["renamed"] = renamed(c.get("renamed") or {}, renames)
        for p in out:                              # a name in use again no longer stands for an old project
            if p not in renames.values():
                (c.get("renamed") or {}).pop(p, None)
        self.store.save_config(c)
        self.reload()

    def _rename(self, renames: dict):
        """Gives old names their new name in every file that holds a project name, the record book excepted."""
        r = lambda p: renames.get(p, p)
        dec = self.store.decisions()
        for v in dec.values():
            if isinstance(v, dict) and "project" in v:
                v["project"] = r(v["project"])
        manual = [dict(e, project=r(e.get("project"))) for e in self.store.manual()]
        mem = self.store.memory()
        for code, labels in (mem.get("project") or {}).items():
            for old, new in renames.items():
                if old in labels:
                    labels[new] = labels.get(new, 0.0) + labels.pop(old)
        log = self.store.corrections()
        for e in log:
            for k in ("guess", "answer", "was"):
                if isinstance(e.get(k), list) and e[k]:
                    e[k] = [r(e[k][0])] + e[k][1:]
        self.store.save_decisions(dec)
        self.store.save_manual(manual)
        self.store.save_memory({k: v for k, v in mem.items() if k != "version"})
        self.store.rewrite_log(log)
        self._undo = self.last_undo = None      # an undo from before the rename would bring the old names back

    @property
    def source_name(self) -> str:
        return getattr(self.source, "used", None) or self.source.name

    @property
    def workday(self) -> tuple:
        w = self.config.get("workday", {})
        return w.get("start", "07:00"), w.get("end", "17:00")

    # ---- reading ---------------------------------------------------------------------------------------------
    def day(self, d: date) -> list:
        hit = self._cache.get(d)
        if hit and clock.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        bl = self._read_day(d)
        self._cache[d] = (clock.monotonic(), bl)
        self._note_repeats(d, bl)
        return bl

    # ---- what keeps coming back ------------------------------------------------------------------------------
    def repeat_code(self, label: str) -> str:
        return self.store.hasher()("src:" + source_key(label))[:16]

    def _note_repeats(self, d: date, bl: list):
        """Unplanned work by source for this day, kept as one-way codes with kind and minutes. Window names are deleted
        after 14 days, but these codes let Synla see for as long as your answers are kept that the same failure keeps
        coming back. A code can't be turned back into the name."""
        if not self.notes or not any(s.title != DELETED and s.app != MANUAL_APP for b in bl for s in b.segments):
            return                                     # names deleted: keep what was noted while they were there
        day = {}
        for b in bl:
            if b.guess.kind in roles.unplanned(self.config) and b.main.app != MANUAL_APP and b.main.title != DELETED:
                c = self.repeat_code(report.label(b))
                kind, minutes = day.get(c, [b.guess.kind, 0.0])
                day[c] = [kind, round(minutes + b.minutes, 1)]
        r = self.store.repeats()
        if r.get(d.isoformat(), {}) != day:
            if day:
                r[d.isoformat()] = day
            else:
                r.pop(d.isoformat(), None)
            self.store.save_repeats(r)

    def comes_back(self, label: str, until: date) -> dict | None:
        """How often this source of unplanned work came back: days with 5 minutes or more, hours, and since when."""
        c = self.repeat_code(label)
        hits = sorted((day, v[c][1]) for day, v in self.store.repeats().items()
                      if c in v and v[c][1] >= 5 and day <= until.isoformat())
        if len(hits) < 2:
            return None
        return {"days": len(hits), "hours": sum(m for _, m in hits) / 60, "since": date.fromisoformat(hits[0][0])}

    def _changed(self):
        self._cache = {}
        self._guesses = {}

    def guess(self, s: Segment):
        """Synla's guess for one window on its own (a block's guess is the weakest of its windows)."""
        k = (s.app, s.title, s.call)
        if k not in self._guesses:
            self._guesses[k] = self.classifier.guess(s.app, s.title, s.call)
        return self._guesses[k]

    def unsure(self, b, s: Segment) -> bool:
        """Does Synla need you for this stretch? Decided per window, so one unclear window does not make a whole
        block unclear. A glance shorter than a minute goes with its block."""
        if b.confirmed or s.app == MANUAL_APP or s.title == DELETED:
            return False
        # a glance, or Explorer, a dialog or Task Manager, goes with its block
        g = self.guess(s) if s.minutes >= NOISE and not glue(s.app, s.title) else b.guess
        return g.confidence < ASK_BELOW or g.project == UNKNOWN

    def _read_day(self, d: date) -> list:
        """A day is midnight to midnight where you were: each stretch keeps the clock it was recorded on, so summer
        time and a trip abroad do not move work into the evening or into the next day."""
        start, end = day_range(d)
        win, afk = self.source.events(start - WIDE, end + WIDE)
        privacy = self.config.get("privacy", {})
        segs = [x for s in segments(win, afk, lambda a, t: is_private(a, t, privacy)) for x in _own_day(s, d)]
        paused = [(a, b, None) for a, b in self.store.pauses() if a < end + WIDE and start - WIDE < b]
        if paused:                        # paused means paused, also when another program kept recording
            segs = _cut(segs, paused)
        decisions = dict(self.store.decisions())
        added = self._manual(d)
        if added:                         # time you added wins over whatever the computer recorded then
            segs = _cut(segs, [(m.start, m.end, None) for m, _ in added])
        if not segs:                      # window names deleted: rebuild the day from your answers alone
            segs = self._from_decisions(decisions, d)
        for m, e in added:
            decisions[m.id] = {"project": e["project"], "kind": e["kind"], "how": "manual"}
        return blocks(sorted(segs + [m for m, _ in added], key=lambda s: s.start), self.classifier, decisions)

    def _manual(self, d: date) -> list:
        out = []
        for e in self.store.manual():
            s = _clock(e["start"], e.get("tz"))
            if s.date() == d:
                out.append((Segment(s, _clock(e["end"], e.get("tz")), MANUAL_APP, MANUAL_TITLE,
                                    bool(e.get("meeting"))), e))
        return out

    @staticmethod
    def _from_decisions(decisions, d: date) -> list:
        out = []
        for k, v in decisions.items():
            s = _clock(k, v.get("tz"))
            if s.date() == d and v.get("end"):
                out.append(Segment(s, _clock(v["end"], v.get("tz")), "", DELETED))
        return sorted(out, key=lambda x: x.start)

    def day_clock(self, d: date):
        """The clock a day was lived on: the time zone of its first stretch. None means this computer's clock."""
        segs = [s for b in self.day(d) for s in b.segments]
        return segs[0].start.tzinfo if segs else None

    def week(self, any_day: date) -> dict:
        """The week's work, day by day. Time you called Not work is left out of everything that counts hours."""
        monday = any_day - timedelta(days=any_day.weekday())
        out = {}
        for i in range(7):
            d = monday + timedelta(days=i)
            bl = [b for b in self.day(d) if b.guess.project != NOT_WORK]
            if bl:
                out[d] = bl
        return out

    def summary(self, any_day: date) -> dict:
        return report.summary(self.week(any_day), self.workday, roles.unplanned(self.config))

    def unsorted_windows(self, bl: list, limit: int = 8) -> list:
        """The windows behind most of the unsorted time today, biggest first. One answer covers all of them."""
        agg = {}
        for b in bl:
            if b.confirmed:
                continue
            for s in b.segments:
                if s.title == DELETED or s.app == MANUAL_APP or glue(s.app, s.title):
                    continue                     # Synla never asks what Task Manager or a dialog was
                unsure = self.unsure(b, s)
                a = agg.setdefault(s.key, {"key": s.key, "app": s.app, "title": s.title, "unsure": 0.0,
                                           "minutes": 0.0, "ids": [], "blocks": set(), "call": False,
                                           "first": s.start, "last": s.end, "spans": []})
                a["spans"].append((s.start, s.end))
                a["minutes"] += s.minutes
                a["unsure"] += s.minutes if unsure else 0.0
                a["ids"].append(s.id)
                a["blocks"].add(b.id)
                a["call"] = a["call"] or s.call
                a["first"], a["last"] = min(a["first"], s.start), max(a["last"], s.end)
        self._by_site(agg)
        ranked = sorted((a for a in agg.values() if a["unsure"] >= 1), key=lambda a: -a["unsure"])
        return ranked[:limit]

    @staticmethod
    def _by_site(agg: dict):
        """Many small pages of one site, such as emails in Gmail, become one question once they add up: one answer
        covers them all. A page big enough to ask about on its own is still asked about on its own."""
        groups = defaultdict(list)
        for a in agg.values():
            what = thing(a["app"], a["title"])
            if a["unsure"] < ASK_MIN and what.startswith(("site:", "app:")):
                groups[what].append(a)
        for what, members in groups.items():
            if len(members) < 2 or sum(m["unsure"] for m in members) < ASK_MIN:
                continue
            main = max(members, key=lambda m: m["minutes"])
            for m in members:
                del agg[m["key"]]
            agg["thing:" + what] = {
                "key": "thing:" + what, "app": main["app"], "title": main["title"], "windows": len(members),
                "unsure": sum(m["unsure"] for m in members), "minutes": sum(m["minutes"] for m in members),
                "ids": [i for m in members for i in m["ids"]], "blocks": set().union(*(m["blocks"] for m in members)),
                "call": any(m["call"] for m in members), "first": min(m["first"] for m in members),
                "last": max(m["last"] for m in members), "spans": [s for m in members for s in m["spans"]]}

    def today(self) -> date:
        return self.home or date.today()

    # ---- the choices you see ---------------------------------------------------------------------------------
    def usage(self) -> dict:
        """What you picked yourself over the last four weeks, in minutes per project and per kind of work, and when
        you first answered at all."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=FOLD_AFTER)
        projects, kinds, first = defaultdict(float), defaultdict(float), None
        rows = [(k, v.get("end"), v.get("project"), v.get("kind")) for k, v in self.store.decisions().items()]
        rows += [(e["start"], e["end"], e["project"], e["kind"]) for e in self.store.manual()]
        for start, end, project, kind in rows:
            t = datetime.fromisoformat(start)
            first = t if first is None or t < first else first
            if t >= cutoff:
                m = (datetime.fromisoformat(end) - t).total_seconds() / 60 if end else 1.0
                projects[project] += m
                kinds[kind] += m
        return {"projects": projects, "kinds": kinds, "since": first}

    def project_choices(self, guess: str = "") -> tuple:
        """The projects shown as buttons: the five you used most lately, in your own list's order so the keys stay
        put, plus Synla's guess if it is another one. The rest are under More."""
        names, use = self.active, self.usage()["projects"]
        top = sorted((p for p in names if use.get(p)), key=lambda p: -use[p])[:SHOW_PROJECTS]
        top += [p for p in names if p not in top][:SHOW_PROJECTS - len(top)]
        shown = [p for p in names if p in top]
        if guess in names and guess not in shown:
            shown.append(guess)
        return shown, sorted((p for p in names if p not in shown), key=str.lower)

    def kind_choices(self, guess: str = "") -> tuple:
        """The kinds of work shown as buttons: your role's, in its order. After four weeks, the ones you never use move
        under More, except planned work and the unplanned kinds, which are what Synla measures."""
        u = self.usage()
        shown = list(roles.role(self.config)["kinds"])
        if u["since"] and datetime.now(timezone.utc) - u["since"] >= timedelta(days=FOLD_AFTER):
            shown = [k for k in shown if k == "project" or k in INVISIBLE or u["kinds"].get(k)]
        if guess in KINDS and guess not in shown and guess != "meeting":
            shown.append(guess)
        return shown, [k for k in KINDS if k not in shown and k != "meeting"]

    def last_day(self) -> date:
        """The last day the pages let you go to: today, or the end of the demo week."""
        t = date.today()
        return max(t, self.home + timedelta(days=6 - self.home.weekday())) if self.home else t

    # ---- questions -------------------------------------------------------------------------------------------
    def window_code(self, key: str) -> str:
        """A one-way code for a window, so a skipped question can be remembered without storing its name."""
        return self.store.hasher()("ask:" + key)[:16]

    def questions(self, d: date) -> dict:
        """What Synla asks you about this day: the windows behind most of the unsure time, biggest first.
        Only windows of ASK_MIN minutes or more, at most QUESTIONS_PER_DAY a day; the rest keeps Synla's best guess.
        Nothing on a red day or a day off. An empty workday gets one question: were you off?"""
        state = self.store.day_state(d)
        skipped = set(state.get("skipped", []))
        if self.free(d):
            return {"items": [], "waiting": 0, "answered": int(state.get("answered", 0)), "skipped": len(skipped),
                    "spot": None}
        if self._ask_off(d) and "off:" + d.isoformat() not in skipped:
            return {"items": [{"type": "offday", "code": "off:" + d.isoformat(), "size": 0.0}], "waiting": 0,
                    "answered": int(state.get("answered", 0)), "skipped": len(skipped), "spot": None}
        items = []
        for w in self.unsorted_windows(self.day(d), limit=100):
            if w["unsure"] < ASK_MIN:
                continue
            code = self.window_code(w["key"])
            if code in skipped:
                continue
            items.append(dict(w, type="window", size=w["unsure"], code=code,
                              guess=self.classifier.guess(w["app"], w["title"], w["call"])))
        items += [dict(g, size=g["minutes"]) for g in self.gaps(d) if g["code"] not in skipped]
        items.sort(key=lambda it: -it["size"])
        answered = int(state.get("answered", 0))
        room = len(items) if state.get("more") else max(0, QUESTIONS_PER_DAY - answered)
        return {"items": items[:room], "waiting": max(0, len(items) - room), "answered": answered,
                "skipped": len(skipped), "spot": self.spot_window(d)}     # the spot check comes after the questions

    def spot_window(self, d: date) -> dict | None:
        """One window a day that Synla sorted on its own, picked at random, so you can see how often Synla is right
        when it does not ask. Questions are about unsure time, so they alone would make Synla look worse than it is."""
        state = self.store.day_state(d)
        if not self.config.get("spotChecks", True) or state.get("spot") or self.free(d):
            return None
        agg = {}
        for b in self.day(d):
            if b.confirmed:
                continue
            for s in b.segments:
                if s.title == DELETED or s.app == MANUAL_APP or self.unsure(b, s):
                    continue
                a = agg.setdefault(s.key, {"key": s.key, "app": s.app, "title": s.title, "unsure": 0.0, "minutes": 0.0,
                                           "ids": [], "blocks": set(), "call": False, "first": s.start, "last": s.end,
                                           "spans": []})
                a["spans"].append((s.start, s.end))
                a["minutes"] += s.minutes
                a["ids"].append(s.id)
                a["blocks"].add(b.id)
                a["call"] = a["call"] or s.call
                a["first"], a["last"] = min(a["first"], s.start), max(a["last"], s.end)
        pool = sorted((a for a in agg.values() if a["minutes"] >= SPOT_MIN), key=lambda a: a["key"])
        if not pool:
            return None
        n = int(hashlib.sha256((d.isoformat() + str(len(pool))).encode()).hexdigest(), 16)
        w = pool[n % len(pool)]
        return dict(w, type="window", spot=True, size=0.0, code="spot:" + self.window_code(w["key"]),
                    guess=self.classifier.guess(w["app"], w["title"], w["call"]))

    # ---- red days and days off --------------------------------------------------------------------------------
    def red_day(self, d: date) -> str | None:
        return holidays.red_day(self.config, d)

    def day_off(self, d: date) -> str | None:
        v = self.store.day_state(d).get("off")
        return v if v in OFF else None

    def free(self, d: date) -> str | None:
        """Why Synla asks nothing about a day: the day off you marked, or the red day. None on a normal day."""
        off = self.day_off(d)
        return OFF[off] if off else self.red_day(d)

    def set_day_off(self, d: date, kind: str | None):
        """Vacation, sick, sick child or time off for a whole day; None takes it back. Only the kind is kept."""
        if kind and kind not in OFF:
            raise Problem("Pick vacation, sick, sick child or time off.")
        snap = self._snapshot()
        state = self.store.day_state(d)
        if kind:
            state["off"] = kind
        else:
            state.pop("off", None)
        self.store.save_day_state(d, state)
        self._changed()
        self._done(snap)

    def _ask_off(self, d: date) -> bool:
        """An empty past workday in a week with work on other days: probably a day off, so Synla asks once."""
        today = self.today()
        if d.weekday() >= 5 or not (0 < (today - d).days <= OFF_ASK_DAYS) or self.day(d):
            return False
        if self.config.get("source", "auto") in ("auto", "synla", "activitywatch") and d.isoformat() < self.store.gaps_since():
            return False
        monday = d - timedelta(days=d.weekday())
        return any(self.day(monday + timedelta(days=i)) for i in range(7) if monday + timedelta(days=i) != d)

    def normal_hours(self, monday: date) -> float:
        """Your normal week, less red days and days off."""
        weekly = float(self.config.get("weeklyHours", 37.5))
        return weekly * sum(1 for i in range(5) if not self.free(monday + timedelta(days=i))) / 5

    # ---- time away from the computer -------------------------------------------------------------------------
    def gaps(self, d: date) -> list:
        """Time away worth asking about: 30 minutes or more on a weekday, starting and ending inside your workday, so
        Synla never asks about mornings, evenings or leaving early. Never paused time, never a likely break, never days
        before this version, never when turned off. "Not work" is always an answer, and Synla never asks why."""
        if not self.config.get("askAboutGaps", True) or d.weekday() >= 5 or self.free(d):
            return []
        if self.config.get("source", "auto") in ("auto", "synla", "activitywatch") and \
                d.isoformat() < self.store.gaps_since():
            return []
        segs = sorted((s for b in self.day(d) for s in b.segments), key=lambda s: s.start)
        if len(segs) < 2:
            return []
        tz = segs[0].start.tzinfo
        w0, w1 = (datetime.combine(d, time.fromisoformat(x), tz) for x in self.workday)
        pauses = self.store.pauses()
        state = self.store.day_state(d)
        handled = [(datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b, _ in state.get("away", [])]
        out, last = [], segs[0].end
        for s in segs[1:]:
            a, b = last, s.start
            minutes = (b - a).total_seconds() / 60
            if minutes >= GAP_MIN and w0 <= a and b <= w1 \
                    and not any(p0 < b and a < p1 for p0, p1 in pauses) \
                    and not any(h0 < b and a < h1 for h0, h1 in handled) \
                    and not self.likely_break(a, minutes):
                out.append({"type": "gap", "start": a, "end": b, "minutes": minutes, "code": "gap:" + utc_key(a)})
            last = max(last, s.end)
        return out

    def likely_break(self, start: datetime, minutes: float) -> bool:
        """Lunch, or a break like ones you already called "Not work" twice."""
        t = start.time()                  # the clock where you were
        if LUNCH[0] <= t <= LUNCH[1] and minutes <= LUNCH[2]:
            return True
        at = t.hour * 60 + t.minute
        alike = 0
        for state in self.store.days().values():
            for a, b, what in state.get("away", []):
                if what != "break":
                    continue
                s0, s1 = datetime.fromisoformat(a).astimezone(), datetime.fromisoformat(b).astimezone()
                if abs(s0.hour * 60 + s0.minute - at) <= LIKE_BREAK and minutes <= (s1 - s0).total_seconds() / 60 + 15:
                    alike += 1
        return alike >= 2

    def _open_gap(self, d: date, code: str) -> dict:
        g = next((g for g in self.gaps(d) if g["code"] == code), None)
        if not g:
            raise Problem("That time away has already been answered.")
        return g

    def _away(self, d: date, g: dict, what: str):
        state = self.store.day_state(d)
        state.setdefault("away", []).append([utc_key(g["start"]), utc_key(g["end"]), what])
        state["answered"] = int(state.get("answered", 0)) + 1
        self.store.save_day_state(d, state)

    def answer_gap(self, d: date, code: str, start_hm: str, end_hm: str, project: str, kind: str,
                   meeting: bool = True) -> str:
        """It was work: the time is added like any time away from the computer. You can shorten it first."""
        g = self._open_gap(d, code)
        snap = self._snapshot()
        start = self.add_time(d, start_hm, end_hm, project, kind, meeting, tz=g["start"].tzinfo)
        self._away(d, g, "work")
        self._done(snap)
        return start

    def gap_not_work(self, d: date, code: str):
        """Not work. Only the time is kept, so Synla can stop asking about breaks like it."""
        g = self._open_gap(d, code)
        snap = self._snapshot()
        self._away(d, g, "break")
        self._done(snap)

    def skip(self, d: date, code: str):
        """Not now: this window keeps Synla's best guess and is not asked about again this day."""
        snap = self._snapshot()
        state = self.store.day_state(d)
        state["skipped"] = sorted(set(state.get("skipped", [])) | {code})
        if code.startswith("spot:"):
            state["spot"] = True
        self.store.save_day_state(d, state)
        self._done(snap)

    def ask_more(self, d: date):
        """Lift today's limit on questions."""
        state = self.store.day_state(d)
        state["more"] = True
        self.store.save_day_state(d, state)

    def day_numbers(self, d: date) -> dict:
        """Minutes for the Today page: sorted by you, sorted by Synla on its own, and still unsure."""
        out = {"total": 0.0, "yours": 0.0, "sure": 0.0, "unsure": 0.0, "notwork": 0.0}
        for b in self.day(d):
            if b.guess.project == NOT_WORK and (b.confirmed or not self.unsure(b, b.main)):
                out["notwork"] += b.minutes
                continue
            for s in b.segments:
                out["total"] += s.minutes
                out["yours" if b.confirmed else "unsure" if self.unsure(b, s) else "sure"] += s.minutes
        return out

    # ---- answering -------------------------------------------------------------------------------------------
    def answer(self, d: date, ids, project: str, kind: str, how: str = "block", asked: bool = False) -> int:
        """Your answer for a set of stretches (a block, every stretch of one window, or part of a block).
        Saved for the timesheet, and learned from for next time. Returns how many stretches it covered.
        asked: the answer came from one of the day's questions, so it counts against the day's limit."""
        snap = self._snapshot()
        n = self._apply(d, [(set(ids), project, kind)], how)
        if asked or how == "spot":
            state = self.store.day_state(d)
            state["answered"] = int(state.get("answered", 0)) + (1 if asked else 0)
            if how == "spot":
                state["spot"] = True          # one spot check a day
            self.store.save_day_state(d, state)
        self._done(snap)
        return n

    def answer_block(self, d: date, block_id: str, project: str, kind: str) -> int:
        block = next((b for b in self.day(d) if b.id == block_id), None)
        if block is None:
            raise KeyError(block_id)
        return self.answer(d, [s.id for s in block.segments], project, kind, "block")

    def approve_confident(self, d: date) -> int:
        """Accept every guess Synla is sure about, in one click. These are marked as approved in bulk, so they never
        count as proof that Synla was right."""
        groups = []
        for b in self.day(d):
            ids = {s.id for s in b.segments if not self.unsure(b, s)}
            if not b.confirmed and ids and b.guess.project != UNKNOWN:
                groups.append((ids, b.guess.project, b.guess.kind))
        snap = self._snapshot()
        self._apply(d, groups, "bulk")
        self._done(snap)
        return len(groups)

    # ---- undo ------------------------------------------------------------------------------------------------
    def _snapshot(self) -> dict:
        return {"decisions": self.store.decisions(), "memory": self.store.memory(),
                "log": len(self.store.corrections()), "days": self.store.days(), "manual": self.store.manual()}

    def _done(self, snap: dict):
        self.last_undo = secrets.token_hex(8)
        self._undo = (self.last_undo, snap)

    def undo(self, token: str):
        """Takes back the latest answer, approval or skip: your answers, what Synla learned from it, and the log."""
        if not self._undo or not secrets.compare_digest(self._undo[0].encode(), str(token or "").encode()):
            raise Problem("There is nothing to undo.")
        snap = self._undo[1]
        self._undo = self.last_undo = None
        self.store.save_decisions(snap["decisions"])
        self.store.save_memory({k: v for k, v in snap["memory"].items() if k != "version"})
        self.store.truncate_log(snap["log"])
        self.store.save_days(snap["days"])
        self.store.save_manual(snap["manual"])
        self.reload()

    def _apply(self, d: date, groups: list, how: str) -> int:
        bl = self.day(d)
        dec = self.store.decisions()
        covered = 0
        for ids, project, kind in groups:
            windows = defaultdict(lambda: {"minutes": 0.0, "seg": None, "taught_before": set(), "segs": []})
            touched = {}
            for b in bl:
                for s in b.segments:
                    if s.id not in ids or s.app == MANUAL_APP:     # time you added is changed by removing it
                        continue
                    old = dec.get(s.id)
                    w = windows[s.key]
                    w["minutes"] += s.minutes
                    w["seg"] = s
                    w["segs"].append(s)
                    if old and old.get("taught"):
                        w["taught_before"].add((old["project"], old["kind"]))
                    t = touched.setdefault(b.id, {"block": b, "minutes": 0.0, "was": None, "wasHow": None})
                    t["minutes"] += s.minutes
                    if old:
                        t["was"], t["wasHow"] = [old["project"], old["kind"]], old.get("how")
            for w in windows.values():
                s = w["seg"]
                if s.title == DELETED:
                    teach = False
                else:
                    for op, ok in w["taught_before"]:              # take back what an earlier answer taught
                        self.classifier.learn(s.app, s.title, op, ok, weight=-1, call=s.call)
                    teach = w["minutes"] >= LEARN_MIN
                    if teach:
                        self.classifier.learn(s.app, s.title, project, kind, call=s.call)
                for seg in w["segs"]:
                    dec[seg.id] = {"end": utc_key(seg.end), "project": project, "kind": kind, "how": how,
                                   "taught": teach, "tz": _minutes(seg.start)}
                    covered += 1
            batch = secrets.token_hex(4)              # one answer can touch several blocks
            for t in touched.values():
                g = t["block"].guess
                self.store.log({"block": t["block"].id, "minutes": round(t["minutes"], 1), "how": how, "batch": batch,
                                "guess": [g.project, g.kind], "confidence": g.confidence, "answer": [project, kind],
                                # no kind given: only the project can be right or wrong
                                "right": g.project == project and (not kind or project == NOT_WORK or g.kind == kind),
                                "was": t["was"], "wasHow": t["wasHow"]})
        self.store.save_decisions(dec)
        self.store.save_memory(self.classifier.dump())
        self._changed()
        return covered

    def add_project(self, name: str, keywords: list | None = None) -> str:
        """Adds a project, or brings back the one with that name (in any case) if you had it already. Returns its
        name as it is in your list."""
        c = self.store.config()
        projects = c.setdefault("projects", {})
        same = next((p for p in projects if p.lower() == name.lower()), None)
        if same is not None:
            if isinstance(projects[same], dict) and projects[same].pop("archived", None):
                self.store.save_config(c)
                self.reload()
            return same
        projects[name] = {"keywords": keywords or []}
        (c.get("renamed") or {}).pop(name, None)
        self.store.save_config(c)
        self.reload()
        return name

    # ---- time away from the computer -------------------------------------------------------------------------
    def add_time(self, d: date, start_hm: str, end_hm: str, project: str, kind: str, meeting: bool = False,
                 tz=None):
        """Time the computer could not see: a site visit, a meeting in person, a phone call. No notes are stored.
        The times are on the clock of that day, also when the day was spent abroad."""
        tz = tz or self.day_clock(d)
        try:
            s, e = (_on_clock(d, time.fromisoformat(x), tz) for x in (start_hm, end_hm))
        except (TypeError, ValueError):
            raise Problem("Write the times as hours and minutes, for example 13:30.") from None
        if e <= s:
            raise Problem("The end must be after the start.")
        if not project or kind not in KINDS:
            raise Problem("Pick both a project and a kind of work.")
        entries = self.store.manual()
        for x in entries:
            xs, xe = datetime.fromisoformat(x["start"]), datetime.fromisoformat(x["end"])
            if s < xe and xs < e:
                raise Problem("This overlaps time you already added.")
        entries.append({"start": utc_key(s), "end": utc_key(e), "project": project, "kind": kind, "tz": _minutes(s)}
                       | ({"meeting": True} if meeting else {}))
        self.store.save_manual(entries)
        self._changed()
        return utc_key(s)

    def remove_time(self, start_id: str):
        self.store.save_manual([e for e in self.store.manual() if e["start"] != start_id])
        self._changed()

    # ---- workload --------------------------------------------------------------------------------------------
    def workload(self, any_day: date) -> dict:
        days = self.week(any_day)
        return workload.assess(workload.measure(days, report.summary(days, self.workday, roles.unplanned(self.config))),
                               self.config)

    # ---- record book -----------------------------------------------------------------------------------------
    def statement(self, monday: date) -> dict:
        """This week's record as it stands now: stretches of time with project, kind and who decided it."""
        days = self.week(monday)
        s = report.summary(days, self.workday, roles.unplanned(self.config))
        projects, kinds = defaultdict(float), defaultdict(float)
        unconfirmed = 0.0
        out_days = []
        for d, bl in sorted(days.items()):
            stretches = []
            for b in bl:
                by = "added" if b.main.app == MANUAL_APP else ("you" if b.confirmed else "guess")
                p = b.guess.project if b.guess.project != UNKNOWN else "Unsorted"
                k = b.guess.kind if b.guess.kind in KINDS else "none" if b.confirmed else "unsorted"
                stretches.append({"start": b.start.isoformat(timespec="minutes"),
                                  "end": b.end.isoformat(timespec="minutes"),
                                  "minutes": round(b.minutes, 1), "project": p, "kind": k, "by": by})
                projects[p] += b.minutes
                kinds[k] += b.minutes
                if by == "guess":
                    unconfirmed += b.minutes
            out_days.append({"date": d.isoformat(), "hours": round(sum(b.minutes for b in bl) / 60, 2),
                             "outsideHours": round(s["per_day"][d]["after"], 2), "stretches": stretches})
        off = {(monday + timedelta(days=i)).isoformat(): self.day_off(monday + timedelta(days=i)) for i in range(7)}
        off = {k: v for k, v in off.items() if v}
        return {"record": FORMAT, "week": monday.isoformat(), "days": out_days, **({"off": off} if off else {}),
                "projects": {p: round(m / 60, 2) for p, m in sorted(projects.items())},
                "kinds": {k: round(m / 60, 2) for k, m in sorted(kinds.items())},
                "totalHours": round(s["hours"], 2), "outsideHours": round(s["after_hours"], 2),
                "unconfirmedHours": round(unconfirmed / 60, 2), "workload": workload.measure(days, s)}

    def has_window_names(self, monday: date) -> bool:
        """True while the week still has its full recording, so its record can be checked against it."""
        return any(s.title != DELETED for bl in self.week(monday).values() for b in bl for s in b.segments)

    def seal(self, monday: date, by: str = "you") -> dict:
        monday -= timedelta(days=monday.weekday())        # a record is always for a whole week, Monday to Sunday
        st = self.statement(monday)
        if not st["days"]:
            raise Problem("There is nothing recorded this week.")
        return self.record.seal(st, by)

    def week_status(self, monday: date) -> dict:
        """sealed, changed since sealing, or not sealed; and whether this week can still be (re)sealed."""
        last = self.record.latest(monday)
        live = self.has_window_names(monday)
        if not last:
            return {"state": "open", "seal": None, "live": live}
        if live and self.record.changed(monday, self.statement(monday), self.config.get("renamed")):
            return {"state": "changed", "seal": last, "live": live}
        return {"state": "sealed", "seal": last, "live": live}

    def auto_seal(self, today: date | None = None) -> list:
        """Seals weeks that ended at least a week ago and were never sealed, so no week is lost when the details
        are deleted. These records say they were sealed automatically."""
        today = today or date.today()
        this_monday = today - timedelta(days=today.weekday())
        sealed = self.record.latest_per_week()
        done = []
        for i in range(1, int(self.config.get("keepDays", 90)) // 7 + 1):
            monday = this_monday - timedelta(weeks=i)
            if monday + timedelta(days=6) > today - timedelta(days=AUTO_SEAL_AFTER) or monday.isoformat() in sealed:
                continue
            st = self.statement(monday)
            if st["days"] and self.record.seal(st, by="auto", only_new=True):
                done.append(monday)
        return done

    def weeks(self, mondays: list) -> list:
        """One row per week for the year report and the trend: from the record book, or live for open weeks."""
        sealed = self.record.latest_per_week()
        oldest_live = date.today() - timedelta(days=int(self.config.get("keepDays", 90)) + 7)
        rows = []
        for monday in mondays:
            seal = sealed.get(monday.isoformat())
            status = None
            if seal:
                st = seal["data"]
            elif monday < oldest_live:        # nothing left to read for weeks this old
                continue
            else:
                st = self.statement(monday)
                if not st["days"]:
                    continue
                status = "open"
            a = workload.assess(st.get("workload", {}), self.config)
            rows.append({"monday": monday, "seal": seal, "status": status or "sealed", "hours": st["totalHours"],
                         "outside": st["outsideHours"], "over": max(0.0, st["totalHours"] - self.normal_hours(monday)),
                         "unconfirmed": st["unconfirmedHours"], "projects": st["projects"],
                         "unplanned": st.get("workload", {}).get("unplanned", 0.0), "level": a["level"]})
        return rows

    def year(self, y: int) -> list:
        d = date(y, 1, 1)
        first = d + timedelta(days=(7 - d.weekday()) % 7)
        mondays = [first + timedelta(weeks=i) for i in range(53) if (first + timedelta(weeks=i)).year == y]
        return self.weeks([m for m in mondays if m <= date.today()])

    def trend(self, any_day: date, n: int = 8) -> list:
        monday = any_day - timedelta(days=any_day.weekday())
        return self.weeks([monday - timedelta(weeks=i) for i in range(n - 1, -1, -1)])

    # ---- quality ---------------------------------------------------------------------------------------------
    def accuracy(self) -> dict:
        """How often Synla's guess was right, counted only on blocks you actually looked at and answered the first
        time. Approvals in bulk are left out: you didn't check them, so they prove nothing. When you later change a
        bulk approval, that is counted as a miss."""
        log = self.store.corrections()
        yours = [e for e in log if not e.get("was") and e.get("how") != "bulk"]
        bulk = [e for e in log if e.get("how") == "bulk"]
        changed = [e for e in log if e.get("wasHow") == "bulk" and e.get("was") != e.get("answer")]
        spot = {}
        for e in yours:                           # a spot check is right when every block it touched was right
            if e.get("how") == "spot":
                k = e.get("batch") or e.get("at")
                spot[k] = spot.get(k, True) and bool(e["right"])
        asked = [e for e in yours if e.get("how") != "spot"]
        out = {"answers": len(yours), "bulk": len(bulk), "bulk_changed": len(changed),
               "spot": len(spot), "spot_right": sum(spot.values()),
               "asked": len(asked), "asked_right": sum(bool(e["right"]) for e in asked)}
        if yours:
            recent = yours[-50:]
            out["right_share"] = sum(e["right"] for e in yours) / len(yours)
            out["recent_right_share"] = sum(e["right"] for e in recent) / len(recent)
        return out

    # ---- your data -------------------------------------------------------------------------------------------
    def forget_old(self):
        """Seal what is about to be lost, then delete what has passed its time."""
        steps = (("sealing old weeks", self.auto_seal),
                 ("deleting old answers", lambda: self.store.forget(int(self.config.get("keepDays", 90)))),
                 ("deleting old window names", lambda: (self.store.dir / "activity.db").exists() and
                  recorder.prune(self.store, float(self.config.get("keepWindowDays", 14)))),
                 ("deleting old records", lambda: self.record.prune(float(self.config.get("keepRecordYears", 5)))))
        for what, step in steps:      # one step that fails must not stop the others, or Synla from starting
            try:
                step()
            except Exception as e:
                self.store.error(what, e)
        self._changed()

    def data_info(self) -> dict:
        dec = self.store.decisions()
        mem = self.store.memory()
        rec = recorder.stats(self.store)
        seals = self.record.seals()
        return {
            "folder": str(self.store.dir),
            "encrypted": protect.ENCRYPTED,
            "source": self.config.get("source", "auto"),
            "recorder_rows": rec.get("rows", 0),
            "recorder_first": rec.get("first"),
            "recorder_last": recorder.last_seen(self.store),
            "recorder_alive": recorder.alive(self.store),
            "paused_until": self.store.paused_until(),
            "answers": len(dec),
            "answers_first": min((datetime.fromisoformat(k) for k in dec), default=None),
            "log_entries": len(self.store.corrections()),
            "added": len(self.store.manual()),
            "away": sum(len(v.get("away", [])) for v in self.store.days().values()),
            "pauses": len(self.store.pauses()),
            "repeats": sum(len(v) for v in self.store.repeats().values()),
            "askAboutGaps": bool(self.config.get("askAboutGaps", True)),
            "spotChecks": bool(self.config.get("spotChecks", True)),
            "clues": sum(len(v) for k, v in mem.items() if k in ("project", "kind")),
            "seals": len(seals),
            "seals_first": seals[0]["week"] if seals else None,
            "keepDays": int(self.config.get("keepDays", 90)),
            "keepWindowDays": int(self.config.get("keepWindowDays", 14)),
            "keepRecordYears": float(self.config.get("keepRecordYears", 5)),
            "privacy": self.config.get("privacy", {}),
            "detectCalls": bool(self.config.get("detectCalls", False)),
        }
