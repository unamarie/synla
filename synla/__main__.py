"""Command line.

  python -m synla                 open the review page
  python -m synla record          start Synla's own recorder (Windows); keep it running in the background
  python -m synla autostart on    start the recorder when you sign in to Windows (off to stop)
  python -m synla pause 60        pause recording for 60 minutes (resume to start again)
  python -m synla week            print this week's summary
  python -m synla export          write this week's timesheet as CSV
  python -m synla demo            try it with a made-up week, kept in its own folder
"""
import argparse
import sys
from datetime import date

from . import demo, report
from .app import Synla
from .server import csv_text, serve
from .store import Store

COMMANDS = ["review", "record", "autostart", "pause", "resume", "week", "export", "demo"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="synla", description="Logs your hours and shows where the unplanned work goes.")
    ap.add_argument("command", nargs="?", default="review", choices=COMMANDS)
    ap.add_argument("value", nargs="?", help="on/off for autostart, minutes for pause")
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
        serve(Synla(store), a.port, start=monday.date())
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
        print("The recorder now starts when you sign in to Windows." if on else "Autostart is off.")
        return

    if a.command in ("pause", "resume"):
        minutes = float(a.value or 60) if a.command == "pause" else None
        store.pause(minutes)
        print(f"Recording paused for {minutes:g} minutes." if minutes else "Recording resumed.")
        return

    app = Synla(store)
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


if __name__ == "__main__":
    main()
