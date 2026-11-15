"""Synla's own recorder for Windows: which window is in front, and whether you are there.

Start it with `python -m synla record` (or let it start with Windows: `python -m synla autostart on`).
Every five seconds it notes the app and the title of the window in front, nothing else.

It never records keystrokes, screen content, the clipboard, web addresses, file contents or sound.
You count as away after a few minutes without keyboard or mouse, unless you are in a call: Windows keeps a record of
which app is using the microphone right now (the same record behind the microphone icon in the taskbar), and Synla
asks only whether one is. Turn this off with "detectCalls": false in config.json.

Windows from apps or with titles on your private list are stored as "Private", with no name.
Window names are encrypted (see protect.py) and deleted after keepWindowDays.
"""
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import protect

POLL = 5.0                      # seconds between samples
GAP = POLL * 3                  # a longer gap (sleep, crash) starts a new row
PRUNE_EVERY = 3600              # seconds
SEAL_EVERY = 6 * 3600           # seconds between checks for weeks that should be sealed
REMIND_EVERY = 60               # seconds between checks for the end of day reminder

SCHEMA = """CREATE TABLE IF NOT EXISTS activity (
    start REAL NOT NULL,        -- seconds since 1970, UTC
    end REAL NOT NULL,
    kind TEXT NOT NULL,         -- active, call or private
    app BLOB,                   -- encrypted on Windows
    title BLOB                  -- encrypted on Windows
);
CREATE INDEX IF NOT EXISTS activitystart ON activity (start);"""


def connect(store) -> sqlite3.Connection:
    db = sqlite3.connect(store.dir / "activity.db", timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def prune(store, keep_days: float):
    """Window names older than keep_days are deleted for good."""
    db = connect(store)
    with db:
        db.execute("DELETE FROM activity WHERE end < ?", (time.time() - keep_days * 86400,))
    db.close()


# ---- what Windows can tell us -----------------------------------------------------------------------------------
class WindowsProbe:
    """Asks Windows three things: the window in front, seconds since the last key or mouse input, and whether
    any app is using the microphone. Nothing else."""

    MIC = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

    def __init__(self):
        import ctypes
        import winreg
        from ctypes import wintypes
        self.ctypes, self.winreg, self.wt = ctypes, winreg, wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        self.LASTINPUTINFO = LASTINPUTINFO

        u = self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        k = self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        u.GetLastInputInfo.restype = wintypes.BOOL
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.GetTickCount.restype = wintypes.DWORD

    def foreground(self) -> tuple[str, str] | None:
        c, wt = self.ctypes, self.wt
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        n = self.user32.GetWindowTextLengthW(hwnd)
        buf = c.create_unicode_buffer(max(n, 0) + 1)
        self.user32.GetWindowTextW(hwnd, buf, max(n, 0) + 1)
        pid = wt.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, c.byref(pid))
        app = ""
        h = self.kernel32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            try:
                size = wt.DWORD(1024)
                path = c.create_unicode_buffer(1024)
                if self.kernel32.QueryFullProcessImageNameW(h, 0, path, c.byref(size)):
                    app = os.path.basename(path.value)
            finally:
                self.kernel32.CloseHandle(h)
        return app, buf.value

    def idle_seconds(self) -> float:
        lii = self.LASTINPUTINFO()
        lii.cbSize = self.ctypes.sizeof(self.LASTINPUTINFO)
        if not self.user32.GetLastInputInfo(self.ctypes.byref(lii)):
            return 0.0
        return ((self.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0

    def mic_in_use(self) -> bool:
        """True if any app is using the microphone now. Windows marks that with LastUsedTimeStop = 0."""
        reg = self.winreg

        def walk(path) -> bool:
            try:
                key = reg.OpenKey(reg.HKEY_CURRENT_USER, path)
            except OSError:
                return False
            with key:
                i = 0
                while True:
                    try:
                        sub = reg.EnumKey(key, i)
                    except OSError:
                        return False
                    i += 1
                    if sub == "NonPackaged":
                        if walk(path + "\\" + sub):
                            return True
                        continue
                    try:
                        with reg.OpenKey(key, sub) as k:
                            start = reg.QueryValueEx(k, "LastUsedTimeStart")[0]
                            stop = reg.QueryValueEx(k, "LastUsedTimeStop")[0]
                        if start and stop == 0:
                            return True
                    except OSError:
                        continue
        return walk(self.MIC)


# ---- deciding what to store ---------------------------------------------------------------------------------------
LOCK_APPS = {"lockapp.exe", "logonui.exe"}


def is_private(app: str, title: str, privacy: dict) -> bool:
    a, t = (app or "").lower(), (title or "").lower()
    return a in {x.lower() for x in privacy.get("skipApps", [])} or \
        any(w.lower() in t for w in privacy.get("skipTitleWords", []) if w)


def sample(probe, config: dict) -> tuple[str, str, str] | None:
    """One look at the computer. Returns (kind, app, title), or None when you are away or the screen is locked."""
    from .blocks import CALL_MAX_IDLE, looks_like_call
    fg = probe.foreground()
    if not fg:
        return None
    app, title = fg
    if app.lower() in LOCK_APPS:
        return None
    call = looks_like_call(app, title) or (config.get("detectCalls", True) and probe.mic_in_use())
    idle = probe.idle_seconds()
    if idle >= CALL_MAX_IDLE.total_seconds() or (idle >= float(config.get("idleMinutes", 3)) * 60 and not call):
        return None
    if is_private(app, title, config.get("privacy", {})):
        return "private", "", ""
    return ("call" if call else "active"), app, title


@dataclass
class _Row:
    rowid: int
    kind: str
    app: str
    title: str
    start: float
    end: float


def run(store, probe=None, clock=time.time, sleep=time.sleep, polls: int | None = None, seal_weeks: bool = True,
        remind: bool = True):
    """The recording loop. polls limits the number of samples (used by tests); None runs until stopped."""
    probe = probe or WindowsProbe()
    db = connect(store)
    last: _Row | None = None
    last_prune = last_seal = last_remind = 0.0
    n = 0
    try:
        while polls is None or n < polls:
            n += 1
            now = clock()
            config = store.config()
            if now - last_prune > PRUNE_EVERY:
                with db:
                    db.execute("DELETE FROM activity WHERE end < ?",
                               (now - float(config.get("keepWindowDays", 30)) * 86400,))
                last_prune = now
            if seal_weeks and now - last_seal > SEAL_EVERY:
                last_seal = now
                try:                       # weeks that ended a week ago are sealed even if you never open Synla
                    from .app import Synla
                    Synla(store).auto_seal()
                except Exception:
                    pass
            if remind and now - last_remind > REMIND_EVERY:
                last_remind = now
                try:                       # a notification when there are questions about today (notify.py)
                    from . import notify
                    notify.maybe_remind(store, probe, config, datetime.fromtimestamp(now).astimezone())
                except Exception:
                    pass
            state = None
            paused = store.paused_until()
            if not paused:
                try:
                    state = sample(probe, config)
                except OSError:
                    state = None
            with db:
                if state is None:
                    if last and not paused and last.kind != "call":
                        # you left at your last input, not when the idle limit was reached (in a call, no input is normal)
                        idle = 0.0
                        try:
                            idle = probe.idle_seconds()
                        except OSError:
                            pass
                        end = max(last.start, min(last.end, now - idle))
                        db.execute("UPDATE activity SET end = ? WHERE rowid = ?", (end, last.rowid))
                    last = None
                else:
                    kind, app, title = state
                    continuous = last is not None and now - last.end <= GAP
                    if continuous and (last.kind, last.app, last.title) == (kind, app, title):
                        db.execute("UPDATE activity SET end = ? WHERE rowid = ?", (now, last.rowid))
                        last.end = now
                    else:
                        start = last.end if continuous else now
                        cur = db.execute("INSERT INTO activity VALUES (?, ?, ?, ?, ?)",
                                         (start, now, kind, protect.enc(app), protect.enc(title)))
                        last = _Row(cur.lastrowid, kind, app, title, start, now)
            sleep(POLL)
    finally:
        db.close()


def single_instance():
    """Returns a handle while this is the only recorder running, or None if another one already is."""
    if sys.platform != "win32":
        return True
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    h = k.CreateMutexW(None, False, "Local\\SynlaRecorder")
    if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
        return None
    return h


def last_seen(store) -> datetime | None:
    p = store.dir / "activity.db"
    if not p.exists():
        return None
    db = connect(store)
    row = db.execute("SELECT MAX(end) FROM activity").fetchone()
    db.close()
    return datetime.fromtimestamp(row[0], timezone.utc) if row and row[0] else None


def stats(store) -> dict:
    p = store.dir / "activity.db"
    if not p.exists():
        return {"rows": 0}
    db = connect(store)
    rows, first = db.execute("SELECT COUNT(*), MIN(start) FROM activity").fetchone()
    db.close()
    return {"rows": rows, "first": datetime.fromtimestamp(first, timezone.utc) if first else None}


# ---- start with Windows -------------------------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command(command: str = "record") -> str:
    """The command Windows runs: pythonw (no window) running Synla from this folder."""
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    exe = w if os.path.exists(w) else exe
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"import sys;sys.path.insert(0,r'{root}');from synla.__main__ import main;main(['{command}'])"
    return f'"{exe}" -c "{code}"'


# The reminder needs two things in your own part of the registry (no admin rights): a name for its notifications,
# and the link synla:open that a click on the notification opens.
LINK_KEY = r"Software\Classes\synla"
NAME_KEY = r"Software\Classes\AppUserModelId\Synla"


def register_reminder(on: bool) -> bool:
    try:
        _register(on)
        return True
    except OSError:
        return False


def _register(on: bool):
    import winreg
    hk = winreg.HKEY_CURRENT_USER
    if on:
        with winreg.CreateKey(hk, LINK_KEY) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Synla")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(hk, LINK_KEY + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, autostart_command("open"))
        with winreg.CreateKey(hk, NAME_KEY) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "Synla")
        return
    for key in (LINK_KEY + r"\shell\open\command", LINK_KEY + r"\shell\open", LINK_KEY + r"\shell", LINK_KEY, NAME_KEY):
        try:
            winreg.DeleteKey(hk, key)
        except FileNotFoundError:
            pass


def autostart(on: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Synla", 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, "Synla")
            except FileNotFoundError:
                pass
    register_reminder(on)
