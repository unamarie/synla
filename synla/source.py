"""Where the raw activity comes from: a running ActivityWatch, or a JSON file (the demo week, or an export)."""
import json
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timedelta


class ActivityWatch:
    """Reads from ActivityWatch's local API. It only listens on localhost, so this never leaves your machine."""

    def __init__(self, url="http://localhost:5600"):
        self.url = url.rstrip("/") + "/api/0"

    def _get(self, path, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        with urllib.request.urlopen(self.url + path + q, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def _bucket(self, prefix):
        buckets = self._get("/buckets/")
        host = socket.gethostname()
        ids = [b for b in buckets if b.startswith(prefix)]
        mine = [b for b in ids if b.endswith(host)]
        if not (mine or ids):
            raise RuntimeError(f"No {prefix} bucket in ActivityWatch. Is the watcher running?")
        return (mine or ids)[0]

    def events(self, start: datetime, end: datetime):
        p = dict(start=start.isoformat(), end=end.isoformat(), limit=-1)
        win = self._get(f"/buckets/{urllib.parse.quote(self._bucket('aw-watcher-window'))}/events", **p)
        afk = self._get(f"/buckets/{urllib.parse.quote(self._bucket('aw-watcher-afk'))}/events", **p)
        return win, afk


class JsonFile:
    """A file shaped like {"window": [...events], "afk": [...events]}, as written by `synla demo`."""

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
