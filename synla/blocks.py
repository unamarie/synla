"""From raw activity to segments, and from segments to blocks the user can review."""
import re
from datetime import datetime, timedelta

from .model import MANUAL_APP, Block, Guess, Segment

MERGE_GAP = timedelta(seconds=60)    # same window again within a minute: same segment
BLOCK_GAP = timedelta(minutes=5)     # same guess again within five minutes: same block
NOISE = 1.0                          # minutes; shorter uncertain segments (alt-tab, a glance) join the block before

# Calls. In a call you often don't touch the keyboard, so that time must not count as away.
# Teams meeting windows are named after the meeting, so for Teams any window counts, except its own sections
# (Chat, Activity, Calendar and so on). Other apps count when the title says meeting, call and so on.
CALL_APPS = {"ms-teams.exe", "teams.exe"}
NOT_CALL = re.compile(r"^(chat|activity|calendar|teams|files|apps|assignments|calls|onedrive|people|settings|search)\s*\|",
                      re.I)
CALL_TITLE = re.compile(r"\b(meeting|call|møte|samtale|huddle|webinar)\b|google meet|^meet - |zoom meeting", re.I)
CALL_MAX_IDLE = timedelta(hours=2)       # longer than this without input is away, even in a call window


# apps where a title like "meeting" or "call" means a call; in Word or Excel it is just a file name
CALL_CAPABLE = CALL_APPS | {"zoom.exe", "cpthost.exe", "webex.exe", "ciscocollabhost.exe", "slack.exe", "msedge.exe",
                            "chrome.exe", "firefox.exe", "brave.exe", "opera.exe"}


def looks_like_call(app: str, title: str) -> bool:
    a, t = (app or "").lower(), (title or "").strip()
    if a not in CALL_CAPABLE:
        return False
    if CALL_TITLE.search(t):
        return True
    return a in CALL_APPS and bool(t) and not NOT_CALL.search(t)


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
    """Window events minus idle time, merged when the same window continues. Calls keep their idle time."""
    idle = _intervals(afk_events, want="afk")
    out: list[Segment] = []
    for start, end, data in _intervals(window_events):
        app, title = data.get("app", ""), data.get("title", "")
        call = bool(data.get("call")) or looks_like_call(app, title)
        holes = [h for h in idle if h[1] - h[0] > CALL_MAX_IDLE] if call else idle
        for s, e in _subtract(start, end, holes):
            if (e - s).total_seconds() < 1:
                continue
            last = out[-1] if out else None
            if last and last.app == app and last.title == title and s - last.end <= MERGE_GAP:
                last.end = max(last.end, e)
                last.call = last.call or call
            else:
                if last and s < last.end:          # heartbeats can overlap by a second or two
                    s = last.end
                    if e <= s:
                        continue
                out.append(Segment(s, e, app, title, call))
    return out


def blocks(segs: list, classifier, decisions: dict | None = None) -> list:
    """Group segments with the same guess into blocks. `decisions` holds the user's answers, keyed by segment id."""
    decisions = decisions or {}
    out: list[Block] = []
    for seg in segs:
        d = decisions.get(seg.id)
        if d:
            g, confirmed = Guess(d["project"], d["kind"], 1.0, ["you said so"]), True
        else:
            g, confirmed = classifier.guess(seg.app, seg.title, seg.call), False
        last = out[-1] if out else None
        same = last and (last.guess.project, last.guess.kind) == (g.project, g.kind) and last.confirmed == confirmed \
            and (last.main.app == MANUAL_APP) == (seg.app == MANUAL_APP)     # time you added stays its own block
        close = last and seg.start - last.end <= BLOCK_GAP
        noise = last and close and not confirmed and not last.confirmed and seg.minutes < NOISE and g.confidence < 0.7
        if last and close and (same or noise):
            last.segments.append(seg)
            if same:
                last.guess.confidence = min(last.guess.confidence, g.confidence) if not confirmed else 1.0
                last.guess.reasons = list(dict.fromkeys(last.guess.reasons + g.reasons))[:4]
        else:
            out.append(Block(seg.start.strftime("%Y%m%dT%H%M%S"), [seg], g, confirmed))
    return out
