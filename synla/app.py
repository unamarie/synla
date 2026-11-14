"""Ties it together: reads activity, applies what Synla has learned and what you have answered."""
from datetime import date, datetime, timedelta

from . import report
from .blocks import blocks, segments
from .classify import ASK_BELOW, Classifier
from .model import UNKNOWN
from .source import ActivityWatch, JsonFile, day_range


class Synla:
    def __init__(self, store):
        self.store = store
        self.reload()

    def reload(self):
        self.config = self.store.config()
        self.classifier = Classifier(self.config, self.store.memory())
        src = self.config.get("source", "activitywatch")
        self.source = ActivityWatch(self.config.get("activityWatchUrl", "http://localhost:5600")) \
            if src == "activitywatch" else JsonFile(src)

    @property
    def projects(self) -> list:
        return list(self.config.get("projects", {}))

    # ---- reading ---------------------------------------------------------------------------------------------
    def day(self, d: date) -> list:
        win, afk = self.source.events(*day_range(d))
        return blocks(segments(win, afk), self.classifier, self.store.decisions())

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

    # ---- answering -------------------------------------------------------------------------------------------
    def answer(self, d: date, block_id: str, project: str, kind: str):
        """Your answer for one block: saved for the timesheet, and learned from for next time."""
        block = next((b for b in self.day(d) if b.id == block_id), None)
        if block is None:
            raise KeyError(block_id)
        dec = self.store.decisions()
        learned = set()
        for s in block.segments:
            dec[s.start.isoformat()] = {"project": project, "kind": kind}
            if s.key not in learned:                       # one vote per distinct window, however long the block
                self.classifier.learn(s.app, s.title, project, kind)
                learned.add(s.key)
        self.store.save_decisions(dec)
        self.store.save_memory(self.classifier.dump())
        g = block.guess
        self.store.log({"block": block_id, "minutes": round(block.minutes, 1), "guess": [g.project, g.kind],
                        "confidence": g.confidence, "answer": [project, kind],
                        "right": g.project == project and g.kind == kind, "was_confirmed": block.confirmed})

    def approve_confident(self, d: date) -> int:
        """Accept every guess Synla is sure about, in one click. Returns how many blocks it approved."""
        n = 0
        for b in self.day(d):
            if not b.confirmed and b.guess.confidence >= ASK_BELOW and b.guess.project != UNKNOWN:
                self.answer(d, b.id, b.guess.project, b.guess.kind)
                n += 1
        return n

    def add_project(self, name: str, keywords: list | None = None):
        c = self.store.config()
        c.setdefault("projects", {})[name] = {"keywords": keywords or []}
        self.store.save_config(c)
        self.reload()

    def accuracy(self) -> dict:
        """How often the guess was right before you answered. This is Synla's own quality measure."""
        log = [e for e in self.store.corrections() if not e.get("was_confirmed")]
        if not log:
            return {"answers": 0}
        right = sum(e["right"] for e in log)
        recent = log[-50:]
        return {"answers": len(log), "right_share": right / len(log),
                "recent_right_share": sum(e["right"] for e in recent) / len(recent)}

    def forget_old(self):
        days = int(self.config.get("keepDays", 90))
        self.store.forget_before(datetime.now().astimezone() - timedelta(days=days))
