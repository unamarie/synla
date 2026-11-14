"""From raw ActivityWatch events to segments, and from segments to blocks the user can review."""
from datetime import datetime, timedelta

from .model import Block, Guess, Segment

MERGE_GAP = timedelta(seconds=60)    # same window again within a minute: same segment
BLOCK_GAP = timedelta(minutes=5)     # same guess again within five minutes: same block
NOISE = 1.0                          # minutes; shorter uncertain segments (alt-tab, a glance) join the block before


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()


def _intervals(events, want=None):
    out = []
    for e in events:
        if want and e.get("data", {}).get("status") != want:
            continue
        start = parse_ts(e["timestamp"])
        out.append((start, start + timedelta(seconds=float(e["duration"])), e.get("data", {})))
    return sorted(out, key=lambda x: x[0])


def _subtract(start, end, holes):
    """Cut the idle intervals out of [start, end)."""
    pieces = [(start, end)]
    for hs, he, _ in holes:
        if he <= start or hs >= end:
            continue
        nxt = []
        for s, e in pieces:
            if he <= s or hs >= e:
                nxt.append((s, e))
                continue
            if s < hs:
                nxt.append((s, hs))
            if he < e:
                nxt.append((he, e))
        pieces = nxt
    return pieces


def segments(window_events: list, afk_events: list) -> list:
    """Window events minus idle time, merged when the same window continues."""
    idle = _intervals(afk_events, want="afk")
    out: list[Segment] = []
    for start, end, data in _intervals(window_events):
        app, title = data.get("app", ""), data.get("title", "")
        for s, e in _subtract(start, end, idle):
            if (e - s).total_seconds() < 1:
                continue
            last = out[-1] if out else None
            if last and last.app == app and last.title == title and s - last.end <= MERGE_GAP:
                last.end = max(last.end, e)
            else:
                if last and s < last.end:          # ActivityWatch heartbeats can overlap by a second or two
                    s = last.end
                    if e <= s:
                        continue
                out.append(Segment(s, e, app, title))
    return out


def blocks(segs: list, classifier, decisions: dict | None = None) -> list:
    """Group segments with the same guess into blocks. `decisions` holds the user's answers, keyed by segment start."""
    decisions = decisions or {}
    out: list[Block] = []
    for seg in segs:
        d = decisions.get(seg.start.isoformat())
        if d:
            g, confirmed = Guess(d["project"], d["kind"], 1.0, ["you said so"]), True
        else:
            g, confirmed = classifier.guess(seg.app, seg.title), False
        last = out[-1] if out else None
        same = last and (last.guess.project, last.guess.kind) == (g.project, g.kind) and last.confirmed == confirmed
        close = last and seg.start - last.end <= BLOCK_GAP
        noise = last and close and not confirmed and seg.minutes < NOISE and g.confidence < 0.7
        if last and close and (same or noise):
            last.segments.append(seg)
            if same:
                last.guess.confidence = min(last.guess.confidence, g.confidence) if not confirmed else 1.0
                last.guess.reasons = list(dict.fromkeys(last.guess.reasons + g.reasons))[:4]
        else:
            out.append(Block(seg.start.strftime("%Y%m%dT%H%M%S"), [seg], g, confirmed))
    return out
