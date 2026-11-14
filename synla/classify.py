"""Works out which project and which kind of work a segment was, and how sure it is.

Two sources of evidence:
  1. Rules: keywords per project from config.json, plus a few built-in clues for kinds of work
     (a title with "urgent" or "failed" points to firefighting, "any update?" to chasing).
  2. Learning: every time the user approves or corrects a block, each feature in it gets a vote for that answer.

Each feature votes for labels; a feature's vote is split across the labels it has been seen with, so a clue that
always means the same thing counts fully and a clue seen everywhere ("app:excel") counts little.
Confidence is how clearly the winner beats the runner-up, damped when there is little evidence overall.
The model is deliberately simple so the reasons can be shown to the user: "file:budget 2027 → Budget".
"""
import math
from collections import defaultdict

from .context import features
from .model import Guess, UNKNOWN

# How much a feature type is worth. A file name or an email subject is a strong clue; the app name is weak.
TYPE_WEIGHT = {"win": 4.0, "file": 3.0, "subject": 3.0, "folder": 2.5, "is": 1.5, "word": 1.0, "app": 0.6, "ext": 0.3}

# Built-in clues for the kind of work. Learning from the user can override any of these.
KIND_RULES = {
    "is:meeting": ("meeting", 3.0),
    "is:chat": ("helping", 1.5),
    "is:urgent": ("firefighting", 2.5),
    "is:asking": ("chasing", 2.5),
}
RULE_WEIGHT = 3.0
ASK_BELOW = 0.7      # blocks under this confidence are shown first and highlighted


class Classifier:
    def __init__(self, config: dict, memory: dict | None = None):
        self.config = config
        # memory[target][feature][label] = votes, where target is "project" or "kind"
        self.memory = {t: defaultdict(lambda: defaultdict(float)) for t in ("project", "kind")}
        for t, feats in (memory or {}).items():
            for f, labels in feats.items():
                for label, n in labels.items():
                    self.memory[t][f][label] = n
        self.project_keywords = {p: [k.lower() for k in v.get("keywords", [])]
                                 for p, v in config.get("projects", {}).items()}

    # ---- learning --------------------------------------------------------------------------------------------
    def learn(self, app: str, title: str, project: str, kind: str, weight: float = 1.0):
        for f in features(app, title):
            self.memory["project"][f][project] += weight
            self.memory["kind"][f][kind] += weight

    def dump(self) -> dict:
        return {t: {f: dict(labels) for f, labels in feats.items()} for t, feats in self.memory.items()}

    # ---- guessing --------------------------------------------------------------------------------------------
    def _votes(self, target: str, feats: list) -> tuple[dict, dict]:
        votes, why = defaultdict(float), defaultdict(list)
        for f in feats:
            w = TYPE_WEIGHT.get(f.split(":", 1)[0], 1.0)
            seen = self.memory[target].get(f)
            if seen:
                total = sum(seen.values())
                for label, n in seen.items():
                    share = n / total
                    # more examples make a clue more trustworthy, with diminishing returns
                    v = w * share * (1 - math.exp(-total / 2))
                    votes[label] += v
                    why[label].append((v, f))
            if target == "project":
                val = f.split(":", 1)[1] if ":" in f else f
                if f.split(":", 1)[0] in ("file", "subject", "folder", "word"):
                    for p, kws in self.project_keywords.items():
                        if any(k in val for k in kws):
                            votes[p] += RULE_WEIGHT
                            why[p].append((RULE_WEIGHT, f"rule:{f}"))
            elif f in KIND_RULES:
                label, v = KIND_RULES[f]
                votes[label] += v
                why[label].append((v, f"rule:{f}"))
        return votes, why

    @staticmethod
    def _pick(votes: dict) -> tuple[str, float]:
        if not votes:
            return UNKNOWN, 0.0
        ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
        label, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        share = top / (top + second)              # how clearly the winner beats the runner-up
        strength = 1 - math.exp(-top / 2.5)       # little evidence -> low confidence even if unanimous
        return label, round(share * strength, 3)

    def guess(self, app: str, title: str) -> Guess:
        feats = features(app, title)
        pv, pwhy = self._votes("project", feats)
        kv, kwhy = self._votes("kind", feats)
        project, pc = self._pick(pv)
        kind, kc = self._pick(kv)
        if kind == UNKNOWN and project != UNKNOWN:
            kind, kc = "project", pc                # a known project and no other clue: ordinary project work
        reasons = sorted(pwhy.get(project, []) + kwhy.get(kind, []), reverse=True)
        nice = list(dict.fromkeys(explain(r) for _, r in reasons))[:3]
        return Guess(project, kind, min(pc, kc), nice)


CLUES = {"is:asking": "reads like a question", "is:urgent": "sounds urgent", "is:chat": "a chat",
         "is:meeting": "a meeting", "is:reply": "an email reply"}


def explain(f: str) -> str:
    """Turn a feature into words the user can check: 'rule:word:platform' -> 'keyword "platform"'."""
    rule = f.startswith("rule:")
    f = f.removeprefix("rule:")
    kind, _, val = f.partition(":")
    if kind == "is":
        return CLUES.get(f, val)
    if rule:
        return f'keyword in "{val[:30]}"'
    return {"win": "same window as before", "file": f"file {val[:30]}", "subject": "same email thread",
            "folder": f"folder {val[:30]}", "app": f"app {val}", "word": f'word "{val[:30]}"'}.get(kind, f[:30])
