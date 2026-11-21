"""From raw activity to segments, and from segments to blocks the user can review."""
import re
from datetime import datetime, timedelta, timezone

from .context import mask, thing
from .model import MANUAL_APP, Block, Guess, Segment

MERGE_GAP = timedelta(seconds=60)    # same window again within a minute: same segment
BLOCK_GAP = timedelta(minutes=5)     # the same work again within five minutes: same block
NOISE = 1.0                          # minutes; a shorter window is a glance or a quick visit
SURE = 0.7                           # a guess at least this sure groups by its answer; an unsure one by what it is about

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


def parse_ts(s: str, tz: int | None = None) -> datetime:
    """A moment, shown in the time zone it was recorded in (minutes ahead of UTC), or this computer's if unknown."""
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t.astimezone(timezone(timedelta(minutes=tz))) if tz is not None else t.astimezone()


def _intervals(events, want=None):
    out = []
    for e in events:
        if want and e.get("data", {}).get("status") != want:
            continue
        start = parse_ts(e["timestamp"], e.get("tz"))
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


def segments(window_events: list, afk_events: list, private=None) -> list:
    """Window events minus idle time, merged when the same window continues. Calls keep their idle time.
    private(app, title): True for a window on your private list. It is shown as Private with no name, whatever
    recorded it: ActivityWatch knows nothing of your list, and words you add later apply to earlier days too."""
    idle = _intervals(afk_events, want="afk")
    out: list[Segment] = []
    for start, end, data in _intervals(window_events):
        app = data.get("app", "")
        title = mask(app, data.get("title", ""))          # names from ActivityWatch are hidden the same way
        if private and app != "Private" and private(app, title):
            app, title = "Private", ""
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
    """Groups segments into blocks, the stretches of work you review. `decisions` holds your answers by segment id.

    What you answered stays together by answer, and what Synla is sure of by its guess. What it is unsure of stays
    together by what it is about (the site, the file, the program), so an evening of jumping between notes, a game
    install and private email does not become one bundle. Explorer, dialogs and Task Manager go with the work around
    them. A visit of under a minute between two stretches of the same work is an interruption and stays inside;
    a visit of under a minute on the way to something else goes with that."""
    decisions = decisions or {}
    seen = []
    for seg in segs:
        d = decisions.get(seg.id)
        if d:
            g, confirmed = Guess(d["project"], d.get("kind") or "", 1.0, ["you said so"]), True
        else:
            g, confirmed = classifier.guess(seg.app, seg.title, seg.call), False
        if seg.app == MANUAL_APP:
            key = ("added", seg.id)                       # time you added stays its own block
        elif confirmed:
            key = ("you", g.project, g.kind)
        else:
            what = thing(seg.app, seg.title)
            key = None if not what else ("guess", g.project, g.kind) if g.confidence >= SURE and g.project != "?" \
                else ("about", what)                     # None: a window that only serves another one
        seen.append((seg, g, confirmed, key))

    out: list[Block] = []
    keys: list = []
    waiting = []                                          # Explorer and dialogs before the first stretch of work
    for i, (seg, g, confirmed, key) in enumerate(seen):
        last = out[-1] if out else None
        close = last is not None and seg.start - last.end <= BLOCK_GAP and keys[-1][0] != "added"
        if key is None:
            if close:
                last.segments.append(seg)
            else:
                waiting.append(seg)
            continue
        if close and keys[-1] == key:
            last.segments.append(seg)
            if key[0] == "guess":
                last.guess.confidence = min(last.guess.confidence, g.confidence)
                last.guess.reasons = list(dict.fromkeys(last.guess.reasons + g.reasons))[:4]
            elif key[0] == "about":
                last.guess.confidence = min(last.guess.confidence, g.confidence)
            continue
        if close and not confirmed and key[0] != "added" and seg.minutes < NOISE and _back_to(seen, i, keys[-1], seg):
            last.segments.append(seg)                      # an interruption: back to the same work right after
            continue
        out.append(Block(seg.start.strftime("%Y%m%dT%H%M%S"), waiting + [seg], g, confirmed))
        keys.append(key)
        waiting = []
    if waiting:
        if out and keys[-1][0] != "added":
            out[-1].segments.extend(waiting)
        else:
            out.append(Block(waiting[0].start.strftime("%Y%m%dT%H%M%S"), waiting,
                             classifier.guess(waiting[0].app, waiting[0].title, waiting[0].call), False))
            keys.append(("about", ""))
    return _visits(out, keys)


def _back_to(seen: list, i: int, key: tuple, seg) -> bool:
    """True if, after window i, you are back at the work with this key within a minute."""
    for nxt, _, _, k in seen[i + 1:]:
        if nxt.start - seg.end > timedelta(minutes=1):
            return False
        if k is None:
            continue                                     # a dialog on the way back
        return k == key
    return False


def _visits(out: list, keys: list) -> list:
    """A stretch of under a minute that Synla is unsure of, such as a search on the way to a site, joins the work
    right after it, or the work before it at the end of a run. Answered stretches and time you added stay as they are."""
    def small(i):
        return keys[i][0] == "about" and out[i].minutes < NOISE

    def open_to(i):
        return keys[i][0] in ("about", "guess")
    i = len(out) - 2
    while i >= 0:
        if small(i) and open_to(i + 1) and out[i + 1].start - out[i].end <= BLOCK_GAP:
            out[i + 1].segments[:0] = out[i].segments
            out[i + 1].id = out[i].id
            del out[i], keys[i]
        i -= 1
    i = 1
    while i < len(out):
        if small(i) and open_to(i - 1) and out[i].start - out[i - 1].end <= BLOCK_GAP:
            out[i - 1].segments.extend(out[i].segments)
            del out[i], keys[i]
            continue
        i += 1
    return out
