"""Ties it together: reads activity, applies what Synla has learned and what you have answered."""
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import protect, recorder, report
from .blocks import blocks, segments
from .classify import ASK_BELOW, Classifier
from .model import UNKNOWN, Segment
from .source import ActivityWatch, Auto, JsonFile, SynlaRecorder, day_range
from .store import utc_key

LEARN_MIN = 1.0          # minutes; a window you only glanced at is not learned from
DELETED = "Window name deleted"


class Synla:
    def __init__(self, store):
        self.store = store
        self.reload()

    def reload(self):
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

    # ---- reading ---------------------------------------------------------------------------------------------
    def day(self, d: date) -> list:
        start, end = day_range(d)
        win, afk = self.source.events(start, end)
        segs = segments(win, afk)
        decisions = self.store.decisions()
        if not segs:                      # window names deleted: rebuild the day from your answers alone
            segs = self._from_decisions(decisions, start, end)
        return blocks(segs, self.classifier, decisions)

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
        w = self.config.get("workday", {})
        return report.summary(self.week(any_day), (w.get("start", "07:00"), w.get("end", "17:00")))

    def unsorted_windows(self, bl: list, limit: int = 8) -> list:
        """The windows behind most of the unsorted time today, biggest first. One answer covers all of them."""
        agg = {}
        for b in bl:
            if b.confirmed:
                continue
            unsure = b.guess.confidence < ASK_BELOW or b.guess.project == UNKNOWN
            for s in b.segments:
                if s.title == DELETED:
                    continue
                a = agg.setdefault(s.key, {"app": s.app, "title": s.title, "unsure": 0.0, "minutes": 0.0,
                                           "ids": [], "blocks": set()})
                a["minutes"] += s.minutes
                a["unsure"] += s.minutes if unsure else 0.0
                a["ids"].append(s.id)
                a["blocks"].add(b.id)
        ranked = sorted((a for a in agg.values() if a["unsure"] >= 1), key=lambda a: -a["unsure"])
        return ranked[:limit]

    # ---- answering -------------------------------------------------------------------------------------------
    def answer(self, d: date, ids, project: str, kind: str, how: str = "block") -> int:
        """Your answer for a set of stretches (a block, every stretch of one window, or part of a block).
        Saved for the timesheet, and learned from for next time. Returns how many stretches it covered."""
        return self._apply(d, [(set(ids), project, kind)], how)

    def answer_block(self, d: date, block_id: str, project: str, kind: str) -> int:
        block = next((b for b in self.day(d) if b.id == block_id), None)
        if block is None:
            raise KeyError(block_id)
        return self.answer(d, [s.id for s in block.segments], project, kind, "block")

    def approve_confident(self, d: date) -> int:
        """Accept every guess Synla is sure about, in one click. These are marked as approved in bulk, so they never
        count as proof that Synla was right."""
        groups = [({s.id for s in b.segments}, b.guess.project, b.guess.kind) for b in self.day(d)
                  if not b.confirmed and b.guess.confidence >= ASK_BELOW and b.guess.project != UNKNOWN]
        self._apply(d, groups, "bulk")
        return len(groups)

    def _apply(self, d: date, groups: list, how: str) -> int:
        bl = self.day(d)
        dec = self.store.decisions()
        covered = 0
        for ids, project, kind in groups:
            windows = defaultdict(lambda: {"minutes": 0.0, "seg": None, "taught_before": set(), "segs": []})
            touched = {}
            for b in bl:
                for s in b.segments:
                    if s.id not in ids:
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
        return covered

    def add_project(self, name: str, keywords: list | None = None):
        c = self.store.config()
        c.setdefault("projects", {})[name] = {"keywords": keywords or []}
        self.store.save_config(c)
        self.reload()

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
        self.store.forget(int(self.config.get("keepDays", 90)))
        if (self.store.dir / "activity.db").exists():
            recorder.prune(self.store, float(self.config.get("keepWindowDays", 30)))

    def data_info(self) -> dict:
        dec = self.store.decisions()
        mem = self.store.memory()
        rec = recorder.stats(self.store)
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
            "clues": sum(len(v) for k, v in mem.items() if k in ("project", "kind")),
            "keepDays": int(self.config.get("keepDays", 90)),
            "keepWindowDays": int(self.config.get("keepWindowDays", 30)),
            "privacy": self.config.get("privacy", {}),
            "detectCalls": bool(self.config.get("detectCalls", True)),
        }
