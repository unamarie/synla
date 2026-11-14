"""Plain data types shared by the rest of Synla."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# What kind of work a block is. The last four are the "invisible" kinds: unplanned work nobody puts in a plan.
KINDS = {
    "project":     "Planned project work",
    "meeting":     "Meetings",
    "admin":       "Admin and internal",
    "helping":     "Helping others",
    "firefighting": "Firefighting",
    "chasing":     "Chasing information",
    "datafixing": "Fixing other people's data",
}
INVISIBLE = ("helping", "firefighting", "chasing", "datafixing")
UNKNOWN = "?"


@dataclass
class Segment:
    """A stretch of time with one app and one window title, with idle time removed."""
    start: datetime
    end: datetime
    app: str
    title: str

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    @property
    def key(self) -> str:
        return f"{self.app}␟{self.title}"


@dataclass
class Guess:
    project: str
    kind: str
    confidence: float            # 0-1; below the threshold Synla asks
    reasons: list = field(default_factory=list)   # the features that decided it, shown to the user


@dataclass
class Block:
    """One or more segments in a row with the same guess. This is what the user reviews."""
    id: str
    segments: list
    guess: Guess
    confirmed: bool = False      # True once the user has approved or corrected it

    @property
    def start(self) -> datetime:
        return self.segments[0].start

    @property
    def end(self) -> datetime:
        return self.segments[-1].end

    @property
    def minutes(self) -> float:
        return sum(s.minutes for s in self.segments)

    @property
    def main(self) -> Segment:
        """The segment that took the most time, used as the block's label."""
        return max(self.segments, key=lambda s: s.minutes)

    def span(self) -> timedelta:
        return self.end - self.start
