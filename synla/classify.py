"""Works out which project and which kind of work a segment was, and how sure it is.

Two sources of evidence:
  1. Rules: keywords per project from config.json, plus a few built-in clues for kinds of work
     (a title with "urgent" or "failed" points to firefighting, "any update?" to chasing).
  2. Learning: every time the user approves or corrects a block, each feature in it gets a vote for that answer.

Each feature votes for labels; a feature's vote is split across the labels it has been seen with, so a clue that
always means the same thing counts fully and a clue seen everywhere ("app:excel") counts little.
Confidence is how clearly the winner beats the runner-up, damped when there is little evidence overall.
The model is deliberately simple so the reasons can be shown to the user: "file:budget 2027 → Budget".

Privacy: what is learned is stored as one-way codes (a keyed hash of each clue), never as readable file names or
email subjects. The reasons shown on the review page come from the window in front of you, not from memory.
"""
import math
from collections import defaultdict

from .context import features
from .model import Guess, UNKNOWN

# How much a feature type is worth. A file name or an email subject is a strong clue; the app name is weak.
TYPE_WEIGHT = {"win": 4.0, "file": 3.0, "subject": 3.0, "id": 3.0, "record": 3.0, "folder": 2.5, "is": 1.5, "rec": 1.5,
               "word": 1.0, "app": 0.6, "ext": 0.3}
# How many answers a clue needs before it is trusted. The very same window again is trusted after one answer, so
# Synla does not ask about it twice; a word needs a few.
TRUST = {"win": 0.5, "id": 1.0, "record": 1.0}

# Built-in clues for the kind of work. Learning from the user can override any of these.
KIND_RULES = {          # a call is not a kind of work: Synla notes the meeting itself and asks what it was for
    "is:chat": ("helping", 1.5),
    "is:urgent": ("firefighting", 2.5),
    "is:asking": ("chasing", 2.5),
}
RULE_WEIGHT = 3.0
ASK_BELOW = 0.7      # blocks under this confidence are shown first and highlighted
# The kind of work is learned from the window itself and its shape (a call, a chat, an email thread), never from
# topic words: one answer that the "Data platform standup" was a meeting must not make every window with "data" in
# its title a meeting.
KIND_CLUES = ("win", "file", "subject", "is", "rec")
RETIRED = {"meeting"}   # older answers may say "meeting"; new guesses say what the meeting was for
# Being in a call says how the work was done, not what kind it was: one escalation call must not make every call
# firefighting. Clues learned from calls by earlier versions are left out too.
NOT_KIND_CLUES = {"is:meeting"}
# The shape of a window (a call, a chat, a reply) says what kind of work it is, not which project.
NOT_PROJECT_CLUES = ("is", "rec")


def counts(target: str, f: str) -> bool:
    typ = f.split(":", 1)[0]
    if target == "kind":
        return typ in KIND_CLUES and f not in NOT_KIND_CLUES
    return typ not in NOT_PROJECT_CLUES


def _plain(text: str) -> str:
    return text


class Classifier:
    def __init__(self, config: dict, memory: dict | None = None, hasher=None):
        self.config = config
        self.hasher = hasher or _plain
        # memory[target][feature][label] = votes, where target is "project" or "kind"
        self.memory = {t: defaultdict(lambda: defaultdict(float)) for t in ("project", "kind")}
        for t, feats in (memory or {}).items():
            if t not in self.memory:
                continue
            for f, labels in feats.items():
                for label, n in labels.items():
                    self.memory[t][f][label] = n
        self.project_keywords = {p: [k.lower() for k in v.get("keywords", [])]
                                 for p, v in config.get("projects", {}).items()}

    # ---- learning --------------------------------------------------------------------------------------------
    def code(self, f: str) -> str:
        """The stored form of a clue: its type in plain text (for weighting), the rest as a one-way code."""
        typ = f.split(":", 1)[0]
        return f"{typ}:{self.hasher(f)}"

    def learn(self, app: str, title: str, project: str, kind: str, weight: float = 1.0, call: bool = False):
        """weight -1 takes back an earlier answer, when you change your mind."""
        for f in features(app, title, call):
            c = self.code(f)
            for target, label in (("project", project), ("kind", kind)):
                if not counts(target, f):
                    continue
                votes = self.memory[target][c]
                votes[label] = votes.get(label, 0.0) + weight
                if votes[label] <= 0:
                    del votes[label]
                if not votes:
                    del self.memory[target][c]

    def dump(self) -> dict:
        return {t: {f: dict(labels) for f, labels in feats.items()} for t, feats in self.memory.items()}

    # ---- guessing --------------------------------------------------------------------------------------------
    def _votes(self, target: str, feats: list) -> tuple[dict, dict]:
        votes, why = defaultdict(float), defaultdict(list)
        for f in feats:
            if not counts(target, f):
                continue
            w = TYPE_WEIGHT.get(f.split(":", 1)[0], 1.0)
            seen = self.memory[target].get(self.code(f))
            if seen:
                total = sum(seen.values())
                for label, n in seen.items():
                    share = n / total
                    # more examples make a clue more trustworthy, with diminishing returns
                    v = w * share * (1 - math.exp(-total / TRUST.get(f.split(":", 1)[0], 2.0)))
                    votes[label] += v
                    why[label].append((v, f))
            if target == "project":
                val = f.split(":", 1)[1] if ":" in f else f
                if f.split(":", 1)[0] in ("file", "subject", "folder", "word", "id", "record"):
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

    def guess(self, app: str, title: str, call: bool = False) -> Guess:
        feats = features(app, title, call)
        pv, pwhy = self._votes("project", feats)
        kv, kwhy = self._votes("kind", feats)
        kv = {k: v for k, v in kv.items() if k not in RETIRED}
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
            "folder": f"folder {val[:30]}", "app": f"app {val}", "word": f'word "{val[:30]}"',
            "id": f'number "{val[:30]}"', "record": f'record "{val[:30]}"', "rec": f"a {val} record"}.get(kind, f[:30])
