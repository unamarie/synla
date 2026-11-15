"""Command line.

  python -m synla                 open Synla's pages in your browser
  python -m synla record          start Synla's own recorder (Windows); keep it running in the background
  python -m synla autostart on    start the recorder when you sign in to Windows (off to stop)
  python -m synla remind          show what the end of day reminder looks like
  python -m synla pause 60        pause recording for 60 minutes (resume to start again)
  python -m synla week            print this week's summary
  python -m synla export          write this week's timesheet as CSV
  python -m synla demo            try it with a made-up week, kept in its own folder
  python -m synla seal            seal last week in the record book (--date for another week)
  python -m synla verify          check the record book; add a fingerprint or a downloaded record to check that
"""
import argparse
import json
import sys
import urllib.request
import webbrowser
from datetime import date, timedelta

from . import demo, record, report
from .app import Synla
from .server import csv_text, serve
from .store import Store

COMMANDS = ["review", "open", "record", "autostart", "remind", "pause", "resume", "week", "export", "demo", "seal",
            "verify"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="synla", description="Logs your hours and shows where the unplanned work goes.")
    ap.add_argument("command", nargs="?", default="review", choices=COMMANDS)
    ap.add_argument("value", nargs="?", help="on/off for autostart, minutes for pause, a fingerprint or file for verify")
    ap.add_argument("--date", type=date.fromisoformat, help="any day in the week you want (YYYY-MM-DD)")
    ap.add_argument("--port", type=int, default=5700)
    ap.add_argument("--out", help="file for export (default: timesheet-<date>.csv)")
    ap.add_argument("--home", help="where Synla keeps its files")
    a = ap.parse_args(argv)

    if a.command == "demo":
        store = Store(a.home or (Store().dir / "demo"))
        path = store.dir / "demo-week.json"
        monday = demo.write(path)
        store.save_config(dict(demo.DEMO_CONFIG, source=str(path)))
        print(f"Made a fictional week starting {monday:%A %d %B} in {store.dir}")
        app = Synla(store)
        app.home = monday.date()
        serve(app, a.port)
        return

    store = Store(a.home)

    if a.command == "record":
        if sys.platform != "win32":
            sys.exit("Synla's own recorder works on Windows. On Mac and Linux, run ActivityWatch instead; "
                     "Synla reads from it automatically.")
        from . import recorder
        handle = recorder.single_instance()
        if handle is None:
            sys.exit("The recorder is already running.")
        print("Recording. Close this window or press Ctrl+C to stop. "
              "To run it in the background from sign-in: python -m synla autostart on")
        from . import notify
        if notify.reminder_time(store.config()):
            recorder.register_reminder(True)      # so the end of day reminder can show and open Synla
        try:
            recorder.run(store)
        except KeyboardInterrupt:
            pass
        return

    if a.command == "autostart":
        if sys.platform != "win32":
            sys.exit("Autostart is for Windows.")
        from . import recorder
        on = (a.value or "on").lower() != "off"
        recorder.autostart(on)
        print("The recorder now starts when you sign in to Windows, with a reminder when Synla has questions."
              if on else "Autostart and the reminder are off.")
        return

    if a.command == "remind":
        if sys.platform != "win32":
            sys.exit("The reminder is a Windows notification.")
        from . import notify, recorder
        recorder.register_reminder(True)
        ok = notify.show("2 quick questions about today", "This is how Synla's reminder looks. Click it to open Synla.",
                         wait=True)
        print("Shown." if ok else "Windows did not show it. Check that notifications are on for Synla in Settings.")
        return

    if a.command in ("pause", "resume"):
        minutes = float(a.value or 60) if a.command == "pause" else None
        store.pause(minutes)
        print(f"Recording paused for {minutes:g} minutes." if minutes else "Recording resumed.")
        return

    if a.command == "verify":
        return verify(store, a.value)

    if a.command == "open":
        return open_page(store, a.port)

    app = Synla(store)
    if a.command == "seal":
        d = a.date or (date.today() - timedelta(days=7))
        monday = d - timedelta(days=d.weekday())
        s = app.seal(monday)
        print(f"Sealed the week of {monday:%d %B %Y}, version {s['version']}.\nFingerprint: {s['fingerprint']}")
        return
    app.forget_old()
    d = a.date or date.today()
    if a.command == "review":
        serve(app, a.port)
    elif a.command == "week":
        print(report.text(app.summary(d)))
    elif a.command == "export":
        out = a.out or f"timesheet-{d}.csv"
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(csv_text(app, d))
        print("wrote", out)


def open_page(store, port):
    """What the reminder opens: Synla's pages, reusing them if they already run. They stop after 30 idle minutes."""
    try:
        p = json.loads((store.dir / "server.json").read_text(encoding="utf-8"))["port"]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if opener.open(f"http://127.0.0.1:{p}/ping", timeout=2).read() == b"synla":
            webbrowser.open(f"http://127.0.0.1:{p}/")
            return
    except (OSError, ValueError, KeyError):
        pass
    app = Synla(store)
    app.forget_old()
    serve(app, port, idle_minutes=30)


def verify(store, value=None):
    """Checks the whole record book, and optionally one fingerprint or one downloaded record file."""
    import json
    import os
    book = record.RecordBook(store)
    v = book.verify()
    if v["ok"]:
        print(f"The record book checks out: {v['seals']} sealed records, none changed after sealing.")
    else:
        print("The record book does NOT check out:")
        for p in v["problems"]:
            print("  " + p)
    if not value:
        return
    if os.path.exists(value):
        doc = json.loads(open(value, encoding="utf-8").read())
        fp = record.fingerprint(doc["previous"], record.canonical(doc["record"]))
        ok = fp == doc["fingerprint"]
        print(f"{value}: {'the record matches its fingerprint' if ok else 'the record does NOT match its fingerprint'}.")
        value = doc["fingerprint"] if ok else None
    if value:
        hits = [s for s in book.seals() if s["fingerprint"].startswith(value.strip().lower())] if len(value.strip()) >= 8 else []
        for s in hits:
            print(f"Fingerprint found: week of {s['week']}, version {s['version']}, sealed {s['sealedAt']}.")
        if not hits:
            print("That fingerprint is not in this record book.")


if __name__ == "__main__":
    main()
