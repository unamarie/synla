"""Where the raw activity comes from.

auto (default): Synla's own recorder if it has data for the day, otherwise ActivityWatch if it is running.
synla:          only Synla's own recorder (Windows).
activitywatch:  only ActivityWatch (Windows, Mac, Linux; its browser extension adds web addresses).
a file path:    a JSON file shaped like ActivityWatch events, used by the demo week.
"""
import json
import socket
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import protect

# localhost only, and never through a proxy, so nothing about your activity passes another machine
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))
AW_URL = "http://127.0.0.1:5600"
AW_RETRY = 300          # seconds: after ActivityWatch was not there, Synla does not knock again for a while
UNREADABLE = "Window name cannot be read on this account"


def on_this_computer(url: str) -> bool:
    """True for an address on this computer. Synla never reads activity from another one."""
    try:
        u = urllib.parse.urlsplit(str(url))
        return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1")
    except ValueError:
        return False


class ActivityWatch:
    """Reads from ActivityWatch's local API. ActivityWatch keeps its own history; Synla cannot delete it."""
    name = "ActivityWatch"

    def __init__(self, url=AW_URL):
        url = url if on_this_computer(url) else AW_URL
        # localhost would try IPv6 first: on Windows each refused try costs two seconds
        url = url.replace("://localhost", "://127.0.0.1", 1)
        self.url = url.rstrip("/") + "/api/0"
        u = urllib.parse.urlsplit(url)
        self.address = (u.hostname, u.port or 80)

    def reachable(self) -> bool:
        """A quick knock on the door. On Windows a refused connection takes seconds, so this gives up after half a
        second: ActivityWatch on this computer answers in a millisecond."""
        try:
            with socket.create_connection(self.address, timeout=0.5):
                return True
        except OSError:
            return False

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
        if not self.reachable():
            return False
        try:
            self._get("/info")
            return True
        except Exception:
            return False

    def events(self, start: datetime, end: datetime):
        if not self.reachable():
            raise ConnectionRefusedError("ActivityWatch is not running on this computer.")
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
        rows = db.execute("SELECT start, end, kind, app, title, tz FROM activity WHERE start < ? AND end > ? "
                          "ORDER BY start", (end.timestamp(), start.timestamp())).fetchall()
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
                try:
                    cache[b] = protect.dec(b)
                except (OSError, ValueError):      # encrypted for another Windows account, or damaged
                    cache[b] = UNREADABLE
                    self.store.damaged["activity.db"] = ("Some window names in activity.db cannot be read on this "
                                                         "Windows account, for example after the folder was copied "
                                                         "from another account. Synla shows them as unreadable.")
            return cache[b]

        win = []
        for s, e, kind, app, title, tz in self._rows(start, end):
            s, e = max(s, start.timestamp()), min(e, end.timestamp())
            if e - s < 1:
                continue
            data = {"app": "Private", "title": ""} if kind == "private" else {"app": plain(app), "title": plain(title)}
            data["call"] = kind == "call"
            win.append({"timestamp": datetime.fromtimestamp(s, timezone.utc).isoformat(), "duration": e - s,
                        "data": data} | ({"tz": tz} if tz is not None else {}))
        return win, []          # the recorder never stores away time, so there is nothing to cut out


class Auto:
    """Synla's recorder first, ActivityWatch second. `used` tells which one answered."""

    def __init__(self, store, aw_url):
        self.recorder = SynlaRecorder(store)
        self.aw = ActivityWatch(aw_url)
        self.used = None
        self.name = "Synla recorder or ActivityWatch"
        self.away_until = 0.0              # ActivityWatch was not there: do not knock again before this

    def events(self, start: datetime, end: datetime):
        if self.recorder.has_data(start, end):
            self.used = self.recorder.name
            return self.recorder.events(start, end)
        self.used = None
        if time.monotonic() < self.away_until:
            return [], []
        try:
            win, afk = self.aw.events(start, end)
            self.used = self.aw.name
            return win, afk
        except Exception:                  # not installed or not running: most people
            self.away_until = time.monotonic() + AW_RETRY
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
    """Midnight to midnight on this computer's clock. When summer time starts or ends, the day is 23 or 25 hours."""
    nxt = day + timedelta(days=1)
    return datetime(day.year, day.month, day.day).astimezone(), datetime(nxt.year, nxt.month, nxt.day).astimezone()


def offset_now(t: float | None = None) -> int:
    """Minutes ahead of UTC on this computer's clock at time t: 60 in Norwegian winter, 120 in summer."""
    return int(datetime.fromtimestamp(t if t is not None else time.time()).astimezone().utcoffset().total_seconds() // 60)
