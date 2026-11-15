"""Red days: public holidays, worked out on this computer with no internet. Norway for now.

Set "holidays" in config.json to "" to turn them off. Synla never asks about a red day, and a week with one has a
shorter normal week.
"""
from datetime import date, timedelta


def easter(year: int) -> date:
    """Easter Sunday in the Gregorian calendar (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = h + l - 7 * m + 114
    return date(year, n // 31, n % 31 + 1)


def norway(year: int) -> dict:
    e = easter(year)
    return {date(year, 1, 1): "New Year's Day", e - timedelta(days=3): "Maundy Thursday",
            e - timedelta(days=2): "Good Friday", e: "Easter Sunday", e + timedelta(days=1): "Easter Monday",
            date(year, 5, 1): "Labour Day", date(year, 5, 17): "Constitution Day",
            e + timedelta(days=39): "Ascension Day", e + timedelta(days=49): "Whit Sunday",
            e + timedelta(days=50): "Whit Monday", date(year, 12, 25): "Christmas Day", date(year, 12, 26): "Boxing Day"}


COUNTRIES = {"NO": norway}


def red_day(config: dict, d: date) -> str | None:
    """The holiday's name if d is a red day where you work, else None."""
    f = COUNTRIES.get(str(config.get("holidays", "NO")).upper())
    return f(d.year).get(d) if f else None
