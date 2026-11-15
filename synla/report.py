"""Timesheet and the weekly "where did the time go" summary."""
import re
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from .context import strip_app
from .model import INVISIBLE, KINDS, UNKNOWN

FOCUS_MIN = 45
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"\+?\d[\d \-]{6,}\d")
LONGNUM = re.compile(r"\b\d{5,}\b")


def redact(text: str) -> str:
    """Remove the obvious personal details before anything is shown outside the review page or exported."""
    text = EMAIL.sub("[email]", text)
    text = PHONE.sub("[number]", text)
    return LONGNUM.sub("[number]", text)


def label(block) -> str:
    t = strip_app(block.main.title) or block.main.app
    return redact(t)[:70]


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def outside_minutes(seg, ws: time, we: time) -> float:
    """Minutes of a stretch that fall outside the working day; all of it on a weekend."""
    if seg.start.weekday() >= 5:
        return seg.minutes
    tz = seg.start.tzinfo
    w0 = datetime.combine(seg.start.date(), ws, tz)
    w1 = datetime.combine(seg.start.date(), we, tz)
    inside = max(0.0, (min(seg.end, w1) - max(seg.start, w0)).total_seconds() / 60)
    return max(0.0, seg.minutes - inside)


def timesheet(days: dict, round_minutes: int = 15) -> dict:
    """days: {date: [blocks]} -> {date: {project: hours}}, rounded to the nearest quarter hour per project."""
    out = {}
    for day, bl in sorted(days.items()):
        mins = defaultdict(float)
        for b in bl:
            mins[b.guess.project if b.guess.project != UNKNOWN else "Unsorted"] += b.minutes
        out[day] = {p: round(m / round_minutes) * round_minutes / 60 for p, m in sorted(mins.items())
                    if round(m / round_minutes) > 0}
    return out


def summary(days: dict, workday=("07:00", "17:00"), unplanned=INVISIBLE) -> dict:
    """unplanned: the kinds that count as unplanned work in your role (roles.unplanned)."""
    ws, we = _hm(workday[0]), _hm(workday[1])
    by_kind, by_project, invisible_ctx = Counter(), Counter(), Counter()
    meeting = urgent = pushed = 0.0
    total = after = 0.0
    switches = 0
    focus = []
    unsure = 0
    per_day = {}
    for day, bl in sorted(days.items()):
        day_inv = day_total = day_after = day_urgent = day_pushed = 0.0
        prev_key = None
        for b in bl:
            m = b.minutes
            total += m
            day_total += m
            by_kind[b.guess.kind] += m
            by_project[b.guess.project] += m
            if b.guess.kind in unplanned:
                day_inv += m
                invisible_ctx[(b.guess.kind, label(b))] += m
            if not b.confirmed and b.guess.confidence < 0.7:
                unsure += 1
            calls = sum(s.minutes for s in b.segments if s.call)
            meeting += m if b.guess.kind == "meeting" else calls      # older answers may say "meeting"
            if b.guess.kind == "firefighting":      # urgent work in working hours pushes planned work
                day_urgent += m
                day_pushed += sum(s.minutes - outside_minutes(s, ws, we) for s in b.segments)
            if b.guess.kind == "project" and m - calls >= FOCUS_MIN:  # a meeting is not focus time
                focus.append(m - calls)
            for s in b.segments:
                outside = outside_minutes(s, ws, we)
                after += outside
                day_after += outside
                if s.minutes >= 0.5:
                    if prev_key is not None and s.key != prev_key:
                        switches += 1
                    prev_key = s.key
        per_day[day] = {"hours": day_total / 60, "invisible_share": day_inv / day_total if day_total else 0,
                        "after": day_after / 60, "urgent": day_urgent / 60, "pushed": day_pushed / 60}
        urgent += day_urgent
        pushed += day_pushed

    hours = total / 60
    inv = sum(by_kind[k] for k in unplanned)
    return {
        "hours": hours,
        "by_kind": {k: by_kind[k] / 60 for k in KINDS if by_kind[k]} | ({"?": by_kind[UNKNOWN] / 60} if by_kind[UNKNOWN] else {}),
        "by_project": {p: m / 60 for p, m in by_project.most_common()},
        "invisible_hours": inv / 60,
        "meeting_hours": meeting / 60,
        "unplanned": list(unplanned),
        "urgent_hours": urgent / 60,
        "pushed_hours": pushed / 60,
        "invisible_share": inv / total if total else 0,
        "top_invisible": [{"kind": k, "what": w, "hours": m / 60} for (k, w), m in invisible_ctx.most_common(8)],
        "switches_per_hour": switches / hours if hours else 0,
        "focus_blocks": len(focus),
        "focus_hours": sum(focus) / 60,
        "after_hours": after / 60,
        "unsure_blocks": unsure,
        "per_day": per_day,
    }


def text(s: dict) -> str:
    """A short plain-text version of the summary, safe to paste into an email or a 1:1 agenda."""
    unplanned = s.get("unplanned", INVISIBLE)
    lines = [f"Active time: {s['hours']:.1f} h",
             f"Unplanned work ({', '.join(KINDS[k].lower() for k in unplanned)}): {s['invisible_hours']:.1f} h "
             f"({s['invisible_share']:.0%})"]
    for k in unplanned:
        if k in s["by_kind"]:
            lines.append(f"  - {KINDS[k]}: {s['by_kind'][k]:.1f} h")
    lines += [f"Focus blocks of {FOCUS_MIN}+ min: {s['focus_blocks']} ({s['focus_hours']:.1f} h)",
              f"Switches between windows: {s['switches_per_hour']:.0f} per hour",
              f"Outside working hours: {s['after_hours']:.1f} h"]
    if s.get("urgent_hours"):
        lines.append(f"Urgent work: {s['urgent_hours']:.1f} h, which pushed {s['pushed_hours']:.1f} h of planned work")
    return "\n".join(lines)
