"""Everything Synla keeps lives in one folder on your own computer. Nothing is sent anywhere.

Windows: %LOCALAPPDATA%\\Synla. Local AppData stays on this computer: it is not part of a roaming profile and is
not synced to OneDrive, so the data does not end up on company servers.
Mac and Linux: ~/.synla. Set SYNLA_HOME to use another folder.

Every file is written whole or not at all: first to a temporary file that is flushed to disk, then swapped in. The
version before is kept as name.bak. If a file cannot be read (a typo in config.json, a disk problem), Synla keeps the
damaged file as name.damaged-<time>, carries on with the last good copy, and says so on its pages.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time as clock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import protect

REPLACE_TRIES = 10
ERRORS_MAX = 50_000        # bytes of errors.log kept
COPY = re.compile(r"(\.bak|\.damaged-[\d-]+|(\.\d+)*\.tmp)$")   # the endings of copies Synla makes of a file


def replace(src, dst):
    """os.replace, tried again for a moment: on Windows a virus scanner or the search indexer can hold a file open."""
    for i in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == REPLACE_TRIES - 1:
                raise
            clock.sleep(0.05 * (i + 1))


def read_text(path: Path) -> str:
    """Reads a text file, trying again for a moment while a virus scanner or the indexer holds it."""
    for i in range(REPLACE_TRIES):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if i == REPLACE_TRIES - 1:
                raise
            clock.sleep(0.05 * (i + 1))


def write_whole(path: Path, text: str):
    """Writes a file whole or not at all. The temporary file has its own name per process and thread, so two
    writers never write into the same one."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    replace(tmp, path)


def create_once(path: Path, data: bytes) -> bytes:
    """Creates a file unless another process just did, and returns what it holds. The recorder and the pages can start
    at the same moment the first time; this way they agree on one key instead of each keeping its own."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    try:
        os.link(tmp, path)                 # fails if the file is there already
    except FileExistsError:
        pass
    except OSError:                        # a drive without links
        if not path.exists():
            replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return path.read_bytes()


def unlink(path: Path):
    """Deletes a file, trying again for a moment while another program holds it."""
    for i in range(REPLACE_TRIES):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if i == REPLACE_TRIES - 1:
                raise
            clock.sleep(0.05 * (i + 1))


def wipe(path: Path, tables: tuple):
    """Empties a database in place, overwriting what it held. On Windows the recorder keeps activity.db open, so the
    file itself cannot be deleted while it runs, but its contents can."""
    if not path.exists():
        return
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("PRAGMA secure_delete=ON")
        with db:
            for t in tables:
                db.execute(f"DELETE FROM {t}")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass
    finally:
        db.close()

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
    "activityWatchUrl": "http://127.0.0.1:5600",
    "idleMinutes": 3,          # no keyboard or mouse for this long counts as away, unless you are in a call
    "privacy": {
        # windows from these apps, or with these words in the title, are recorded as "Private" with no name
        "skipApps": ["keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "lastpass.exe"],
        "skipTitleWords": ["InPrivate", "Incognito", "Private Browsing", "Privat nettlesing", "nettbank",
                           "Helsenorge", "nav.no", "Sykmelding", "Sykemelding", "Legevakt", "Psykolog", "apotek",
                           "Fagforbundet", "fagforening", "Tekna", "Negotia", "Akademikerne", "Parat.com", "NITO",
                           "Econa", "Fellesforbundet", "Handel og Kontor", "Utdanningsforbundet", "Juristforbundet",
                           "Samfunnsviterne", "lo.no", "Arbeidstilsynet", "varslingssak"],
    },
}


def standard_dir() -> Path:
    """Where Synla keeps its files when nothing else is said."""
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Synla"
    return Path.home() / ".synla"


def default_dir() -> Path:
    if os.environ.get("SYNLA_HOME"):
        return Path(os.environ["SYNLA_HOME"])
    return standard_dir()


def kept(when, cutoff: datetime) -> bool:
    """True if a time is after the cutoff. A time that cannot be read is not kept: Synla cannot place it anyway."""
    try:
        return datetime.fromisoformat(str(when)) >= cutoff
    except (TypeError, ValueError):
        return False


def utc_key(t: datetime) -> str:
    """Answers are keyed by the start of a stretch in UTC, so summer time and travel do not lose them."""
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, folder: str | os.PathLike | None = None):
        self.dir = Path(folder) if folder else default_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.damaged = {}                          # file name: what went wrong, shown on Synla's pages
        if sys.platform != "win32":
            try:
                os.chmod(self.dir, 0o700)          # only your account can open the folder
            except OSError:
                pass
        self._sweep()

    def other_dir(self) -> str | None:
        """This folder if it is not the usual one, so what Synla starts on its own (the recorder, at sign-in, the
        reminder's link) uses the same folder as the pages. None for the usual folder."""
        try:
            return None if self.dir.resolve() == standard_dir().resolve() else str(self.dir.resolve())
        except OSError:
            return str(self.dir)

    def _sweep(self):
        """Half-written files left by a crash or a power cut can hold your data: deleted after an hour."""
        try:
            for f in os.listdir(self.dir):
                p = self.dir / f
                if f.endswith(".tmp") and clock.time() - p.stat().st_mtime > 3600:
                    p.unlink()
        except OSError:
            pass

    def _read(self, name, default, shape=None):
        """A JSON file, or the last good copy if the file is damaged. shape: dict or list, what the file must hold."""
        shape = shape or (type(default) if default is not None else None)
        p = self.dir / name
        bak = p.with_name(p.name + ".bak")
        if not p.exists():
            return self._load(bak, default, shape) if bak.exists() else default
        try:
            data = json.loads(read_text(p))
            if shape and not isinstance(data, shape):
                raise ValueError("it does not hold what Synla expects")
            return data
        except ValueError as e:                    # also JSONDecodeError and UnicodeDecodeError
            if name not in self.damaged:
                where = ("it is not text" if isinstance(e, UnicodeDecodeError) else
                         f"line {e.lineno}" if hasattr(e, "lineno") else str(e))
                kept = p.with_name(f"{p.name}.damaged-{datetime.now():%Y%m%d-%H%M%S}")
                try:
                    shutil.copyfile(p, kept)
                except OSError:
                    kept = None
                self.damaged[name] = (f"{name} could not be read ({where}). Synla is using "
                                      f"{'the last good copy' if bak.exists() else 'empty settings'}"
                                      f"{f', and kept your file as {kept.name}' if kept else ''}. "
                                      f"Your next change writes a good file again.")
                self.error(f"reading {name}", e)
            return self._load(bak, default, shape)

    @staticmethod
    def _load(path: Path, default, shape=None):
        try:
            data = json.loads(read_text(path))
            return data if not shape or isinstance(data, shape) else default
        except (OSError, ValueError):
            return default

    def _write(self, name, data):
        p = self.dir / name
        if p.exists() and name not in self.damaged:      # keep the version before, as long as it was good
            try:
                shutil.copyfile(p, p.with_name(p.name + ".bak"))
            except OSError:
                pass
        write_whole(p, json.dumps(data, ensure_ascii=False, indent=1))
        self.damaged.pop(name, None)

    def error(self, where: str, exc: BaseException):
        """Notes an unexpected error in errors.log in this folder, for finding the cause. Kept short."""
        import traceback
        p = self.dir / "errors.log"
        try:
            old = p.read_text(encoding="utf-8")[-ERRORS_MAX:] if p.exists() else ""
            text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            write_whole(p, old + f"{datetime.now():%Y-%m-%d %H:%M:%S} {where}\n{text}\n")
        except OSError:
            pass

    # config.json: your projects, keywords and privacy settings. Edit by hand or from the review page.
    def config(self) -> dict:
        c = self._read("config.json", None, dict)
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
        return self._secret("key.bin", lambda: secrets.token_bytes(32),
                            "Synla made a new key, so it learns your projects again from your answers.")

    def page_key(self) -> str:
        """The key to Synla's pages. Synla hands it to your browser when it opens them; another account on this
        computer, or a website, does not have it."""
        return self._secret("page.key", lambda: secrets.token_urlsafe(32).encode("ascii"),
                            "Open Synla again from its reminder or with python -m synla.").decode("ascii")

    def _secret(self, name: str, make, then: str) -> bytes:
        """A secret kept in a file, encrypted for your Windows account. If it cannot be read (for example after the
        folder was copied from another account), it is kept aside and replaced."""
        p = self.dir / name
        if not p.exists():
            create_once(p, protect.protect(make()))
        try:
            k = protect.unprotect(p.read_bytes())
            if len(k) >= 32:
                return k
            raise ValueError("it is too short")
        except (OSError, ValueError) as e:
            try:
                replace(p, p.with_name(f"{p.name}.damaged-{datetime.now():%Y%m%d-%H%M%S}"))
            except OSError:
                pass
            self.damaged[name] = f"{name} could not be read on this Windows account. {then}"
            self.error(f"reading {name}", e)
            return protect.unprotect(create_once(p, protect.protect(make())))

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
        out = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass                                # a line cut off by a crash: skip it, keep the rest
        return [e for e in out if isinstance(e, dict)]

    def rewrite_log(self, entries: list):
        write_whole(self.dir / "corrections.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))

    def truncate_log(self, n: int):
        """Keep the first n entries: used when you undo an answer, so it does not count as one."""
        log = self.corrections()
        if len(log) > n:
            write_whole(self.dir / "corrections.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in log[:n]))

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
        keep = {k: v for k, v in d.items() if kept(k, cutoff)}
        if len(keep) != len(d):
            self.save_decisions(keep)
        m = self.manual()
        recent_manual = [e for e in m if isinstance(e, dict) and kept(e.get("start"), cutoff)]
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
        history = [h for h in p.get("history", []) if isinstance(h, list) and len(h) == 2 and kept(h[1], cutoff)]
        if len(history) != len(p.get("history", [])):
            self._write("pause.json", dict(p, history=history))
        log = self.corrections()
        recent = [e for e in log if kept(e.get("at"), cutoff)]
        if len(recent) != len(log):
            write_whole(self.dir / "corrections.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in recent))

    def delete_everything(self, record_book: bool = False) -> list:
        """Removes Synla's data. The record book only when asked. Your settings (config.json) and the key to the
        pages stay. Returns the files another program held open, so they could not be deleted (usually none)."""
        names = ["decisions.json", "corrections.jsonl", "memory.json", "key.bin", "pause.json", "manual.json",
                 "days.json", "reminded.json", "since.json", "repeats.json", "activity.db", "activity.db-wal",
                 "activity.db-shm", "errors.log", "server.json"]
        wiped = {"activity.db", "activity.db-wal", "activity.db-shm"}
        wipe(self.dir / "activity.db", ("activity",))
        if record_book:
            names += ["record.db", "record.db-wal", "record.db-shm"]
            wiped |= {"record.db", "record.db-wal", "record.db-shm"}
            wipe(self.dir / "record.db", ("seals", "chain"))
        keep = {"config.json", "config.json.bak"} | {f for f in os.listdir(self.dir) if f.startswith("config.json.damaged")}
        for f in os.listdir(self.dir):             # copies too: the last good version, damaged files, half-written ones
            base = COPY.sub("", f)
            if f not in keep and f != base and (base in names or base.startswith("errors.log")):
                names.append(f)
        failed = []
        for name in dict.fromkeys(names):
            try:
                unlink(self.dir / name)
            except OSError:
                if name not in wiped:              # an emptied database that the recorder holds open is fine
                    failed.append(name)
        return failed
