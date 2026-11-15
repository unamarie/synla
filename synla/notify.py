"""The end of day reminder: a Windows notification when Synla has questions about your day. Click it to answer.

Shown once a day on weekdays, at the time set in config.json: "reminder": "auto" is 45 minutes before your workday
ends, "15:30" is a time of your own, "off" turns it off. Only when there is something to ask, you are at the
computer, and you are not in a call. Windows shows it on this computer; nothing is sent anywhere.

Clicking it opens the link synla:open, which starts Synla's page (python -m synla open). That link does one thing
only, so a web page that tries to open it can do no more than open your own Synla.
"""
import base64
import subprocess
import sys
from datetime import datetime, time, timedelta
from xml.sax.saxutils import escape

AUMID = "Synla"              # the name Windows shows on the notification
PROTOCOL = "synla"
BEFORE_END = timedelta(minutes=45)
AWAY = 120                   # seconds without input: wait until you are back


def reminder_time(config: dict) -> time | None:
    v = str(config.get("reminder", "auto")).strip().lower()
    if v in ("off", "no", "false", "none", ""):
        return None
    if v == "auto":
        h, m = config.get("workday", {}).get("end", "17:00").split(":")
        return (datetime(2000, 1, 1, int(h), int(m)) - BEFORE_END).time()
    h, m = v.split(":")
    return time(int(h), int(m))


def due(now: datetime, config: dict, reminded: str | None) -> bool:
    """Time for today's reminder, and it has not been shown yet today."""
    at = reminder_time(config)
    if at is None or now.weekday() >= 5 or reminded == now.date().isoformat():
        return False
    return now.time() >= at


def words(n: int) -> tuple[str, str]:
    return (f"{n} quick question{'s' if n != 1 else ''} about today",
            "Takes less than a minute. Click to answer.")


def toast_xml(title: str, body: str) -> str:
    return (f'<toast activationType="protocol" launch="{PROTOCOL}:open">'
            f'<visual><binding template="ToastGeneric"><text>{escape(title)}</text><text>{escape(body)}</text>'
            f'</binding></visual><actions>'
            f'<action content="Answer now" activationType="protocol" arguments="{PROTOCOL}:open"/>'
            f'<action content="Later" activationType="system" arguments="dismiss"/></actions></toast>')


def command(xml: str) -> list:
    """Windows PowerShell can show a notification without any extra software."""
    q = xml.replace("'", "''")
    script = ("$null=[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime];"
              "$null=[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime];"
              f"$x=New-Object Windows.Data.Xml.Dom.XmlDocument;$x.LoadXml('{q}');"
              f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{AUMID}')"
              ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()]


def show(title: str, body: str, wait: bool = False) -> bool:
    """Shows the notification. Without wait it returns at once, so recording never stops for it."""
    if sys.platform != "win32":
        return False
    try:
        cmd, flags = command(toast_xml(title, body)), 0x08000000           # no console window
        if not wait:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            return True
        return subprocess.run(cmd, capture_output=True, timeout=30, creationflags=flags).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def maybe_remind(store, probe, config: dict, now: datetime, show=show) -> int | None:
    """Called by the recorder about once a minute. Returns the number of questions when it was time, else None."""
    if not due(now, config, store.reminded()) or store.paused_until():
        return None
    try:
        if probe.idle_seconds() > AWAY:
            return None
        if config.get("detectCalls", False) and probe.mic_in_use():
            return None                                  # never in the middle of a call
    except OSError:
        return None
    store.save_reminded(now.date())                 # once a day, even if something below goes wrong
    from .app import Synla
    n = len(Synla(store).questions(now.date())["items"])
    if n:
        show(*words(n))
    return n
