"""Everything Synla keeps lives in one folder on your own computer. Nothing is sent anywhere.

Windows: %LOCALAPPDATA%\\Synla. Local AppData stays on this computer: it is not part of a roaming profile and is
not synced to OneDrive, so the data does not end up on company servers.
Mac and Linux: ~/.synla. Set SYNLA_HOME to use another folder.
"""
import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import protect

DEFAULT_CONFIG = {
    "projects": {"Internal": {"keywords": []}},
    "workday": {"start": "07:00", "end": "17:00"},
    "roundMinutes": 15,
    "keepDays": 90,            # your answers and the answer log
    "keepWindowDays": 14,      # window names; after this only project, kind and time remain
    "keepRecordYears": 5,      # the record book: sealed weeks with times, projects and kinds, no window names
    "weeklyHours": 37.5,       # your normal week, to show hours above it
    "recordEmail": "",         # your private address, for sending each week's fingerprint to yourself
    "askAboutGaps": True,      # ask what longer time away from the computer was; "Not work" is always an answer
    "indicators": {},          # your own yellow and red limits, e.g. {"switches": [20, 40]}
    "source": "auto",          # auto, synla, activitywatch, or a path to a JSON file (the demo)
    "activityWatchUrl": "http://localhost:5600",
    "idleMinutes": 3,          # no keyboard or mouse for this long counts as away, unless you are in a call
    "privacy": {
        # windows from these apps, or with these words in the title, are recorded as "Private" with no name
        "skipApps": ["keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "lastpass.exe"],
        "skipTitleWords": ["InPrivate", "Incognito", "Private Browsing", "Privat nettlesing", "nettbank",
                           "Helsenorge", "nav.no", "Sykmelding", "Sykemelding", "Legevakt", "Psykolog", "apotek",
                           "Fagforbundet", "fagforening", "Tekna", "Negotia", "Akademikerne", "Parat.com"],
    },
}


def default_dir() -> Path:
    if os.environ.get("SYNLA_HOME"):
        return Path(os.environ["SYNLA_HOME"])
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Synla"
    return Path.home() / ".synla"


def utc_key(t: datetime) -> str:
    """Answers are keyed by the start of a stretch in UTC, so summer time and travel do not lose them."""
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, folder: str | os.PathLike | None = None):
        self.dir = Path(folder) if folder else default_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            try:
                os.chmod(self.dir, 0o700)          # only your account can open the folder
            except OSError:
                pass

    def _read(self, name, default):
        p = self.dir / name
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def _write(self, name, data):
        p = self.dir / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    # config.json: your projects, keywords and privacy settings. Edit by hand or from the review page.
    def config(self) -> dict:
        c = self._read("config.json", None)
        if c is None:
            c = json.loads(json.dumps(DEFAULT_CONFIG))
            self._write("config.json", c)
        merged = {**DEFAULT_CONFIG, **c}
        own = c.get("privacy", {}) if isinstance(c.get("privacy"), dict) else {}
        merged["privacy"] = {**DEFAULT_CONFIG["privacy"], **own}
        for k in ("skipApps", "skipTitleWords"):      # updates only add to the private list, never take from yours
            merged["privacy"][k] = list(dict.fromkeys(DEFAULT_CONFIG["privacy"][k] + list(own.get(k, []))))
        return merged

    def save_config(self, c: dict):
        self._write("config.json", c)

    # key: a random secret that turns learned clues into one-way codes. Encrypted with DPAPI on Windows.
    def key(self) -> bytes:
        p = self.dir / "key.bin"
        if p.exists():
            return protect.unprotect(p.read_bytes())
        k = secrets.token_bytes(32)
        p.write_bytes(protect.protect(k))
        return k

    def hasher(self):
        k = self.key()
        return lambda text: hmac.new(k, text.encode("utf-8"), hashlib.sha256).hexdigest()[:24]

    # memory.json: what Synla has learned, stored as one-way codes, never as readable names
    def memory(self) -> dict:
        m = self._read("memory.json", {})
        return m if m.get("version") == 2 else {}

    def save_memory(self, m: dict):
        self._write("memory.json", {"version": 2, **m})

    # decisions.json: your answer for each stretch of time, keyed by its start (UTC), with its end.
    # The timesheet uses these first, and they remain after window names are deleted.
    def decisions(self) -> dict:
        return self._read("decisions.json", {})

    def save_decisions(self, d: dict):
        self._write("decisions.json", d)

    # corrections.jsonl: a log of every answer, so you can see how often Synla was wrong
    def log(self, entry: dict):
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
        with open(self.dir / "corrections.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def corrections(self) -> list:
        p = self.dir / "corrections.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def truncate_log(self, n: int):
        """Keep the first n entries: used when you undo an answer, so it does not count as one."""
        log = self.corrections()
        if len(log) > n:
            tmp = self.dir / "corrections.jsonl.tmp"
            tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in log[:n]), encoding="utf-8")
            os.replace(tmp, self.dir / "corrections.jsonl")

    # days.json: per day, how many questions you answered, which you skipped (as one-way codes), and whether
    # you asked for more than the daily limit
    def days(self) -> dict:
        return self._read("days.json", {})

    def save_days(self, d: dict):
        self._write("days.json", d)

    def day_state(self, day) -> dict:
        return dict(self.days().get(day.isoformat(), {}))

    def save_day_state(self, day, state: dict):
        d = self.days()
        d[day.isoformat()] = state
        self.save_days(d)

    # repeats.json: per day, unplanned work by source as one-way codes with kind and minutes, so Synla can tell that
    # the same failure keeps coming back after the window names are deleted
    def repeats(self) -> dict:
        return self._read("repeats.json", {})

    def save_repeats(self, r: dict):
        self._write("repeats.json", r)

    # reminded.json: the last day the end-of-day reminder was shown (written by the recorder only)
    def reminded(self) -> str | None:
        return self._read("reminded.json", {}).get("day")

    def save_reminded(self, day):
        self._write("reminded.json", {"day": day.isoformat()})

    # manual.json: time away from the computer that you added yourself (start, end, project, kind; no notes)
    def manual(self) -> list:
        return self._read("manual.json", [])

    def save_manual(self, entries: list):
        self._write("manual.json", sorted(entries, key=lambda e: e["start"]))

    # pause.json: when recording is paused until
    def paused_until(self) -> datetime | None:
        v = self._read("pause.json", {}).get("until")
        t = datetime.fromisoformat(v) if v else None
        return t if t and t > datetime.now(timezone.utc) else None

    def pause(self, minutes: float | None):
        """Pause for some minutes, or resume with None. Each pause is kept as a start and an end, so Synla never asks
        about paused time and never shows what another program recorded then."""
        now = datetime.now(timezone.utc)
        p = self._read("pause.json", {})
        history = [list(h) for h in p.get("history", [])]
        active = self.paused_until() is not None and history
        if minutes:
            until = now + timedelta(minutes=minutes)
            if active:
                history[-1][1] = until.isoformat()
            else:
                history.append([now.isoformat(), until.isoformat()])
        else:
            until = None
            if active:
                history[-1][1] = now.isoformat()
        self._write("pause.json", {"until": until.isoformat() if until else None, "history": history})

    def pauses(self) -> list:
        return [(datetime.fromisoformat(a), datetime.fromisoformat(b))
                for a, b in self._read("pause.json", {}).get("history", [])]

    def gaps_since(self) -> str:
        """The first day Synla could ask about time away. Days before that are never asked about, because pauses
        before then were not kept."""
        v = self._read("since.json", {}).get("gaps")
        if not v:
            v = datetime.now().date().isoformat()
            self._write("since.json", {"gaps": v})
        return v

    # ---- retention ---------------------------------------------------------------------------------------------
    def forget(self, keep_days: int):
        """Delete answers and log entries older than keep_days. Window names are pruned by the recorder."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        d = self.decisions()
        keep = {k: v for k, v in d.items() if datetime.fromisoformat(k) >= cutoff}
        if len(keep) != len(d):
            self.save_decisions(keep)
        m = self.manual()
        recent_manual = [e for e in m if datetime.fromisoformat(e["start"]) >= cutoff]
        if len(recent_manual) != len(m):
            self.save_manual(recent_manual)
        days = self.days()
        keep_days_state = {k: v for k, v in days.items() if k >= cutoff.date().isoformat()}
        if len(keep_days_state) != len(days):
            self.save_days(keep_days_state)
        r = self.repeats()
        keep_repeats = {k: v for k, v in r.items() if k >= cutoff.date().isoformat()}
        if len(keep_repeats) != len(r):
            self.save_repeats(keep_repeats)
        p = self._read("pause.json", {})
        history = [h for h in p.get("history", []) if datetime.fromisoformat(h[1]) >= cutoff]
        if len(history) != len(p.get("history", [])):
            self._write("pause.json", dict(p, history=history))
        log = self.corrections()
        recent = [e for e in log if datetime.fromisoformat(e["at"]) >= cutoff]
        if len(recent) != len(log):
            tmp = self.dir / "corrections.jsonl.tmp"
            tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in recent), encoding="utf-8")
            os.replace(tmp, self.dir / "corrections.jsonl")

    def delete_everything(self, record_book: bool = False):
        """Removes Synla's data. The record book only when asked. Your settings (config.json) stay."""
        names = ["decisions.json", "corrections.jsonl", "memory.json", "key.bin", "pause.json", "manual.json",
                 "days.json", "reminded.json", "since.json", "repeats.json", "activity.db", "activity.db-wal", "activity.db-shm"]
        if record_book:
            names += ["record.db", "record.db-wal", "record.db-shm"]
        for name in names:
            try:
                (self.dir / name).unlink()
            except FileNotFoundError:
                pass
