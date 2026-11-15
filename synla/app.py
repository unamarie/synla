"""Ties it together: reads activity, applies what Synla has learned and what you have answered."""
import secrets
import time as clock
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from . import protect, recorder, report, workload
from .blocks import _subtract, blocks, segments
from .classify import ASK_BELOW, Classifier
from .blocks import NOISE
from .model import KINDS, MANUAL_APP, MANUAL_TITLE, UNKNOWN, Segment
from .record import FORMAT, RecordBook
from .source import ActivityWatch, Auto, JsonFile, SynlaRecorder, day_range
from .store import utc_key

LEARN_MIN = 1.0          # minutes; a window you only glanced at is not learned from
DELETED = "Window name deleted"
AUTO_SEAL_AFTER = 7      # days after a week ends, an unsealed week is sealed as it stands
CACHE_SECONDS = 20       # a page asks for the same days several times; read them once
QUESTIONS_PER_DAY = 5    # Synla asks about at most this many windows a day, unless you ask for more
ASK_MIN = 5.0            # minutes; smaller unsure windows keep Synla's best guess instead of becoming a question


class Synla:
    def __init__(self, store):
        self.store = store
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
        url = self.config.get("activityWatchUrl", "http://localhost:5600")
        self.source = {"auto": lambda: Auto(self.store, url), "synla": lambda: SynlaRecorder(self.store),
                       "activitywatch": lambda: ActivityWatch(url)}.get(src, lambda: JsonFile(src))()

    @property
    def projects(self) -> list:
        return list(self.config.get("projects", {}))

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
        return bl

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
        g = self.guess(s) if s.minutes >= NOISE else b.guess
        return g.confidence < ASK_BELOW or g.project == UNKNOWN

    def _read_day(self, d: date) -> list:
        start, end = day_range(d)
        win, afk = self.source.events(start, end)
        segs = segments(win, afk)
        decisions = dict(self.store.decisions())
        added = self._manual(start, end)
        if added:                         # time you added wins over whatever the computer recorded then
            holes = [(m.start, m.end, None) for m, _ in added]
            segs = [Segment(a, b, s.app, s.title, s.call) for s in segs for a, b in _subtract(s.start, s.end, holes)
                    if (b - a).total_seconds() >= 1]
        if not segs:                      # window names deleted: rebuild the day from your answers alone
            segs = self._from_decisions(decisions, start, end)
        for m, e in added:
            decisions[m.id] = {"project": e["project"], "kind": e["kind"], "how": "manual"}
        return blocks(sorted(segs + [m for m, _ in added], key=lambda s: s.start), self.classifier, decisions)

    def _manual(self, start, end) -> list:
        out = []
        for e in self.store.manual():
            s = datetime.fromisoformat(e["start"]).astimezone()
            if start <= s < end:
                out.append((Segment(s, datetime.fromisoformat(e["end"]).astimezone(), MANUAL_APP, MANUAL_TITLE), e))
        return out

    @staticmethod
    def _from_decisions(decisions, start, end) -> list:
        out = []
        for k, v in decisions.items():
            s = datetime.fromisoformat(k).astimezone()
            if start <= s < end and v.get("end"):
                out.append(Segment(s, datetime.fromisoformat(v["end"]).astimezone(), "", DELETED))
        return sorted(out, key=lambda x: x.start)

    def week(self, any_day: date) -> dict:
        monday = any_day - timedelta(days=any_day.weekday())
        out = {}
        for i in range(7):
            d = monday + timedelta(days=i)
            bl = self.day(d)
            if bl:
                out[d] = bl
        return out

    def summary(self, any_day: date) -> dict:
        return report.summary(self.week(any_day), self.workday)

    def unsorted_windows(self, bl: list, limit: int = 8) -> list:
        """The windows behind most of the unsorted time today, biggest first. One answer covers all of them."""
        agg = {}
        for b in bl:
            if b.confirmed:
                continue
            for s in b.segments:
                if s.title == DELETED or s.app == MANUAL_APP:
                    continue
                unsure = self.unsure(b, s)
                a = agg.setdefault(s.key, {"key": s.key, "app": s.app, "title": s.title, "unsure": 0.0,
                                           "minutes": 0.0, "ids": [], "blocks": set(), "call": False,
                                           "first": s.start, "last": s.end})
                a["minutes"] += s.minutes
                a["unsure"] += s.minutes if unsure else 0.0
                a["ids"].append(s.id)
                a["blocks"].add(b.id)
                a["call"] = a["call"] or s.call
                a["first"], a["last"] = min(a["first"], s.start), max(a["last"], s.end)
        ranked = sorted((a for a in agg.values() if a["unsure"] >= 1), key=lambda a: -a["unsure"])
        return ranked[:limit]

    def today(self) -> date:
        return self.home or date.today()

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
        Only windows of ASK_MIN minutes or more, at most QUESTIONS_PER_DAY a day; the rest keeps Synla's best guess."""
        state = self.store.day_state(d)
        skipped = set(state.get("skipped", []))
        items = []
        for w in self.unsorted_windows(self.day(d), limit=100):
            if w["unsure"] < ASK_MIN:
                continue
            code = self.window_code(w["key"])
            if code in skipped:
                continue
            items.append(dict(w, code=code, guess=self.classifier.guess(w["app"], w["title"], w["call"])))
        answered = int(state.get("answered", 0))
        room = len(items) if state.get("more") else max(0, QUESTIONS_PER_DAY - answered)
        return {"items": items[:room], "waiting": max(0, len(items) - room), "answered": answered,
                "skipped": len(skipped)}

    def skip(self, d: date, code: str):
        """Not now: this window keeps Synla's best guess and is not asked about again this day."""
        snap = self._snapshot()
        state = self.store.day_state(d)
        state["skipped"] = sorted(set(state.get("skipped", [])) | {code})
        self.store.save_day_state(d, state)
        self._done(snap)

    def ask_more(self, d: date):
        """Lift today's limit on questions."""
        state = self.store.day_state(d)
        state["more"] = True
        self.store.save_day_state(d, state)

    def day_numbers(self, d: date) -> dict:
        """Minutes for the Today page: sorted by you, sorted by Synla on its own, and still unsure."""
        out = {"total": 0.0, "yours": 0.0, "sure": 0.0, "unsure": 0.0}
        for b in self.day(d):
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
        if asked:
            state = self.store.day_state(d)
            state["answered"] = int(state.get("answered", 0)) + 1
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
                "log": len(self.store.corrections()), "days": self.store.days()}

    def _done(self, snap: dict):
        self.last_undo = secrets.token_hex(8)
        self._undo = (self.last_undo, snap)

    def undo(self, token: str):
        """Takes back the latest answer, approval or skip: your answers, what Synla learned from it, and the log."""
        if not self._undo or not secrets.compare_digest(self._undo[0], token or ""):
            raise ValueError("There is nothing to undo.")
        snap = self._undo[1]
        self._undo = self.last_undo = None
        self.store.save_decisions(snap["decisions"])
        self.store.save_memory({k: v for k, v in snap["memory"].items() if k != "version"})
        self.store.truncate_log(snap["log"])
        self.store.save_days(snap["days"])
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
                                   "taught": teach}
                    covered += 1
            for t in touched.values():
                g = t["block"].guess
                self.store.log({"block": t["block"].id, "minutes": round(t["minutes"], 1), "how": how,
                                "guess": [g.project, g.kind], "confidence": g.confidence,
                                "answer": [project, kind], "right": [g.project, g.kind] == [project, kind],
                                "was": t["was"], "wasHow": t["wasHow"]})
        self.store.save_decisions(dec)
        self.store.save_memory(self.classifier.dump())
        self._changed()
        return covered

    def add_project(self, name: str, keywords: list | None = None):
        c = self.store.config()
        c.setdefault("projects", {})[name] = {"keywords": keywords or []}
        self.store.save_config(c)
        self.reload()

    # ---- time away from the computer -------------------------------------------------------------------------
    def add_time(self, d: date, start_hm: str, end_hm: str, project: str, kind: str):
        """Time the computer could not see: a site visit, a meeting in person, a phone call. No notes are stored."""
        s = datetime.combine(d, time.fromisoformat(start_hm)).astimezone()
        e = datetime.combine(d, time.fromisoformat(end_hm)).astimezone()
        if e <= s:
            raise ValueError("The end must be after the start.")
        if not project or kind not in KINDS:
            raise ValueError("Pick both a project and a kind of work.")
        entries = self.store.manual()
        for x in entries:
            xs, xe = datetime.fromisoformat(x["start"]), datetime.fromisoformat(x["end"])
            if s < xe and xs < e:
                raise ValueError("This overlaps time you already added.")
        entries.append({"start": utc_key(s), "end": utc_key(e), "project": project, "kind": kind})
        self.store.save_manual(entries)
        self._changed()
        return utc_key(s)

    def remove_time(self, start_id: str):
        self.store.save_manual([e for e in self.store.manual() if e["start"] != start_id])
        self._changed()

    # ---- workload --------------------------------------------------------------------------------------------
    def workload(self, any_day: date) -> dict:
        days = self.week(any_day)
        return workload.assess(workload.measure(days, report.summary(days, self.workday)), self.config)

    # ---- record book -----------------------------------------------------------------------------------------
    def statement(self, monday: date) -> dict:
        """This week's record as it stands now: stretches of time with project, kind and who decided it."""
        days = self.week(monday)
        s = report.summary(days, self.workday)
        projects, kinds = defaultdict(float), defaultdict(float)
        unconfirmed = 0.0
        out_days = []
        for d, bl in sorted(days.items()):
            stretches = []
            for b in bl:
                by = "added" if b.main.app == MANUAL_APP else ("you" if b.confirmed else "guess")
                p = b.guess.project if b.guess.project != UNKNOWN else "Unsorted"
                k = b.guess.kind if b.guess.kind in KINDS else "unsorted"
                stretches.append({"start": b.start.isoformat(timespec="minutes"),
                                  "end": b.end.isoformat(timespec="minutes"),
                                  "minutes": round(b.minutes, 1), "project": p, "kind": k, "by": by})
                projects[p] += b.minutes
                kinds[k] += b.minutes
                if by == "guess":
                    unconfirmed += b.minutes
            out_days.append({"date": d.isoformat(), "hours": round(sum(b.minutes for b in bl) / 60, 2),
                             "outsideHours": round(s["per_day"][d]["after"], 2), "stretches": stretches})
        return {"record": FORMAT, "week": monday.isoformat(), "days": out_days,
                "projects": {p: round(m / 60, 2) for p, m in sorted(projects.items())},
                "kinds": {k: round(m / 60, 2) for k, m in sorted(kinds.items())},
                "totalHours": round(s["hours"], 2), "outsideHours": round(s["after_hours"], 2),
                "unconfirmedHours": round(unconfirmed / 60, 2), "workload": workload.measure(days, s)}

    def has_window_names(self, monday: date) -> bool:
        """True while the week still has its full recording, so its record can be checked against it."""
        return any(s.title != DELETED for bl in self.week(monday).values() for b in bl for s in b.segments)

    def seal(self, monday: date, by: str = "you") -> dict:
        st = self.statement(monday)
        if not st["days"]:
            raise ValueError("There is nothing recorded this week.")
        return self.record.seal(st, by)

    def week_status(self, monday: date) -> dict:
        """sealed, changed since sealing, or not sealed; and whether this week can still be (re)sealed."""
        last = self.record.latest(monday)
        live = self.has_window_names(monday)
        if not last:
            return {"state": "open", "seal": None, "live": live}
        if live and self.record.changed(monday, self.statement(monday)):
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
            if st["days"]:
                self.record.seal(st, by="auto")
                done.append(monday)
        return done

    def weeks(self, mondays: list) -> list:
        """One row per week for the year report and the trend: from the record book, or live for open weeks."""
        sealed = self.record.latest_per_week()
        weekly = float(self.config.get("weeklyHours", 37.5))
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
                         "outside": st["outsideHours"], "over": max(0.0, st["totalHours"] - weekly),
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
        out = {"answers": len(yours), "bulk": len(bulk), "bulk_changed": len(changed)}
        if yours:
            recent = yours[-50:]
            out["right_share"] = sum(e["right"] for e in yours) / len(yours)
            out["recent_right_share"] = sum(e["right"] for e in recent) / len(recent)
        return out

    # ---- your data -------------------------------------------------------------------------------------------
    def forget_old(self):
        """Seal what is about to be lost, then delete what has passed its time."""
        try:
            self.auto_seal()
        except Exception:
            pass
        self.store.forget(int(self.config.get("keepDays", 90)))
        if (self.store.dir / "activity.db").exists():
            recorder.prune(self.store, float(self.config.get("keepWindowDays", 30)))
        self.record.prune(float(self.config.get("keepRecordYears", 5)))
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
            "paused_until": self.store.paused_until(),
            "answers": len(dec),
            "answers_first": min((datetime.fromisoformat(k) for k in dec), default=None),
            "log_entries": len(self.store.corrections()),
            "added": len(self.store.manual()),
            "clues": sum(len(v) for k, v in mem.items() if k in ("project", "kind")),
            "seals": len(seals),
            "seals_first": seals[0]["week"] if seals else None,
            "keepDays": int(self.config.get("keepDays", 90)),
            "keepWindowDays": int(self.config.get("keepWindowDays", 30)),
            "keepRecordYears": float(self.config.get("keepRecordYears", 5)),
            "privacy": self.config.get("privacy", {}),
            "detectCalls": bool(self.config.get("detectCalls", True)),
        }
