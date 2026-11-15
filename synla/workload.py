"""Leading indicators for workload: the conditions that come before overload, not the overload itself.

The same idea as a risk model for safety: instead of counting incidents after they happen, watch the conditions
that make them likely. Each indicator is green, yellow or red. The week is red when two indicators are red or the
score reaches 6, and yellow when one is red or the score reaches 3 (green 0, yellow 1, red 2 points each).

The limits are starting points. Change them under "indicators" in config.json after a few weeks of your own data.
"""
from datetime import timedelta

BREAK = timedelta(minutes=20)        # a pause this long counts as a break
LONG_DAY_HOURS = 10                  # first to last activity
NO_BREAK_MIN_HOURS = 6               # only days with this much activity can lack a break

# key: (name, what it measures, yellow from, red from, "up" if higher is worse, "down" if lower is worse, format)
INDICATORS = {
    "unplanned": ("Unplanned work", "share of active time spent helping, firefighting, chasing or fixing data",
                  0.20, 0.35, "up", "pct"),
    "focus": ("Focus time", "share of active time in project blocks of 45 minutes or more", 0.30, 0.15, "down", "pct"),
    "switches": ("Switches between windows", "per active hour", 15, 30, "up", "num"),
    "afterHours": ("Outside working hours", "hours this week, weekends included", 2, 5, "up", "h"),
    "noBreakDays": ("Days without a break", f"days with {NO_BREAK_MIN_HOURS}+ hours and no {BREAK.seconds // 60} minute pause",
                    1, 3, "up", "days"),
    "longDays": ("Long days", f"days over {LONG_DAY_HOURS} hours from first to last activity", 1, 2, "up", "days"),
    "meetings": ("Meetings", "share of active time", 0.30, 0.50, "up", "pct"),
}
LEVELS = ("green", "yellow", "red")


def measure(days: dict, summary: dict) -> dict:
    """The raw numbers for one week. days: {date: [blocks]}, summary: report.summary for the same week."""
    hours = summary["hours"] or 0.0
    no_break = long_days = 0
    for _, bl in days.items():
        segs = sorted((s for b in bl for s in b.segments), key=lambda s: s.start)
        if not segs:
            continue
        active = sum(s.minutes for s in segs) / 60
        span = (max(s.end for s in segs) - segs[0].start).total_seconds() / 3600
        gaps = [b.start - a.end for a, b in zip(segs, segs[1:])]
        if active >= NO_BREAK_MIN_HOURS and not any(g >= BREAK for g in gaps):
            no_break += 1
        if span > LONG_DAY_HOURS:
            long_days += 1
    meeting = summary.get("meeting_hours", summary["by_kind"].get("meeting", 0.0))   # calls and meetings in a room
    return {
        "unplanned": round(summary["invisible_share"], 4),
        "focus": round(summary["focus_hours"] / hours, 4) if hours else 0.0,
        "switches": round(summary["switches_per_hour"], 2),
        "afterHours": round(summary["after_hours"], 2),
        "noBreakDays": no_break,
        "longDays": long_days,
        "meetings": round(meeting / hours, 4) if hours else 0.0,
    }


def limits(config: dict) -> dict:
    """The yellow and red limits, with your own from config.json where you have set them."""
    own = config.get("indicators", {})
    return {k: tuple(own.get(k, (v[2], v[3]))) for k, v in INDICATORS.items()}


def level(key: str, value: float, lim: dict) -> int:
    yellow, red = lim[key]
    if INDICATORS[key][4] == "up":
        return 2 if value >= red else 1 if value >= yellow else 0
    return 2 if value <= red else 1 if value <= yellow else 0


def assess(values: dict, config: dict) -> dict:
    lim = limits(config)
    rows = []
    for k, (name, what, *_rest) in INDICATORS.items():
        if k not in values:
            continue
        rows.append({"key": k, "name": name, "what": what, "value": values[k], "level": level(k, values[k], lim),
                     "limits": lim[k], "format": INDICATORS[k][5], "direction": INDICATORS[k][4]})
    score = sum(r["level"] for r in rows)
    reds = sum(r["level"] == 2 for r in rows)
    week = 2 if reds >= 2 or score >= 6 else 1 if reds >= 1 or score >= 3 else 0
    return {"level": LEVELS[week], "score": score, "rows": rows}


def show(value, fmt: str) -> str:
    if fmt == "pct":
        return f"{value:.0%}"
    if fmt == "h":
        return f"{value:.1f} h"
    if fmt == "days":
        return f"{int(value)}"
    return f"{value:.0f}"



NUMBERS = ("no", "one", "two", "three", "four", "five", "six", "seven")


def _count(n: int, one: str, many: str) -> str:
    return f"{NUMBERS[n] if 0 <= n < len(NUMBERS) else n} {one if n == 1 else many}"


# How each indicator is put into words when it is yellow or red
SAY = {
    "unplanned": lambda v: f"a lot of unplanned work ({v:.0%})",
    "focus": lambda v: "little time for focused work",
    "switches": lambda v: "many switches between windows",
    "afterHours": lambda v: f"{v:.1f} h outside working hours",
    "noBreakDays": lambda v: _count(int(v), "day", "days") + " without a break",
    "longDays": lambda v: _count(int(v), "long day", "long days"),
    "meetings": lambda v: f"many meetings ({v:.0%})",
}


def sentence(assessed: dict) -> str:
    """The week in one sentence: the indicators that stand out, worst first. 'Two long days and little time for
    focused work.'"""
    rows = sorted((r for r in assessed["rows"] if r["level"] > 0), key=lambda r: -r["level"])
    if not rows:
        return "Nothing stands out."
    parts = [SAY[r["key"]](r["value"]) for r in rows[:3]]
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return text[0].upper() + text[1:] + "."
