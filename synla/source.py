"""Where the raw activity comes from.

auto (default): Synla's own recorder if it has data for the day, otherwise ActivityWatch if it is running.
synla:          only Synla's own recorder (Windows).
activitywatch:  only ActivityWatch (Windows, Mac, Linux; its browser extension adds web addresses).
a file path:    a JSON file shaped like ActivityWatch events, used by the demo week.
"""
import json
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import protect

# localhost only, and never through a proxy, so nothing about your activity passes another machine
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ActivityWatch:
    """Reads from ActivityWatch's local API. ActivityWatch keeps its own history; Synla cannot delete it."""
    name = "ActivityWatch"

    def __init__(self, url="http://localhost:5600"):
        self.url = url.rstrip("/") + "/api/0"

    def _get(self, path, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        with _LOCAL.open(self.url + path + q, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def _bucket(self, prefix):
        buckets = self._get("/buckets/")
        host = socket.gethostname()
        ids = [b for b in buckets if b.startswith(prefix)]
        mine = [b for b in ids if b.endswith(host)]
        if not (mine or ids):
            raise RuntimeError(f"No {prefix} bucket in ActivityWatch. Is the watcher running?")
        return (mine or ids)[0]

    def running(self) -> bool:
        try:
            self._get("/info")
            return True
        except Exception:
            return False

    def events(self, start: datetime, end: datetime):
        p = dict(start=start.isoformat(), end=end.isoformat(), limit=-1)
        win = self._get(f"/buckets/{urllib.parse.quote(self._bucket('aw-watcher-window'))}/events", **p)
        afk = self._get(f"/buckets/{urllib.parse.quote(self._bucket('aw-watcher-afk'))}/events", **p)
        return win, afk


class SynlaRecorder:
    """Reads what Synla's own recorder stored, decrypting window names on the way."""
    name = "Synla recorder"

    def __init__(self, store):
        self.store = store

    def _rows(self, start: datetime, end: datetime):
        if not (self.store.dir / "activity.db").exists():
            return []
        from .recorder import connect
        db = connect(self.store)
        rows = db.execute("SELECT start, end, kind, app, title FROM activity WHERE start < ? AND end > ? ORDER BY start",
                          (end.timestamp(), start.timestamp())).fetchall()
        db.close()
        return rows

    def has_data(self, start: datetime, end: datetime) -> bool:
        return bool(self._rows(start, end))

    def events(self, start: datetime, end: datetime):
        cache = {}

        def plain(b):
            if b is None:
                return ""
            b = bytes(b)
            if b not in cache:
                cache[b] = protect.dec(b)
            return cache[b]

        win = []
        for s, e, kind, app, title in self._rows(start, end):
            s, e = max(s, start.timestamp()), min(e, end.timestamp())
            if e - s < 1:
                continue
            data = {"app": "Private", "title": ""} if kind == "private" else {"app": plain(app), "title": plain(title)}
            data["call"] = kind == "call"
            win.append({"timestamp": datetime.fromtimestamp(s, timezone.utc).isoformat(), "duration": e - s,
                        "data": data})
        return win, []          # the recorder never stores away time, so there is nothing to cut out


class Auto:
    """Synla's recorder first, ActivityWatch second. `used` tells which one answered."""

    def __init__(self, store, aw_url):
        self.recorder = SynlaRecorder(store)
        self.aw = ActivityWatch(aw_url)
        self.used = None
        self.name = "Synla recorder or ActivityWatch"

    def events(self, start: datetime, end: datetime):
        if self.recorder.has_data(start, end):
            self.used = self.recorder.name
            return self.recorder.events(start, end)
        try:
            win, afk = self.aw.events(start, end)
            self.used = self.aw.name
            return win, afk
        except Exception:
            self.used = None
            return [], []


class JsonFile:
    """A file shaped like {"window": [...events], "afk": [...events]}, as written by `synla demo`."""
    name = "demo file"

    def __init__(self, path):
        self.data = json.loads(open(path, encoding="utf-8").read())

    def events(self, start: datetime, end: datetime):
        def inside(e):
            t = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
            return start <= t < end
        return [e for e in self.data["window"] if inside(e)], [e for e in self.data["afk"] if inside(e)]


def day_range(day):
    start = datetime(day.year, day.month, day.day).astimezone()
    return start, start + timedelta(days=1)
