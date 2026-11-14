"""Command line.

  python -m synla demo      try it with a made-up week (no ActivityWatch needed)
  python -m synla           open the review page (reads ActivityWatch on this computer)
  python -m synla week      print this week's summary
  python -m synla export    write this week's timesheet as CSV
"""
import argparse
from datetime import date

from . import demo, report
from .app import Synla
from .server import csv_text, serve
from .store import Store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="synla", description="Logs your hours and shows where the unplanned work goes.")
    ap.add_argument("command", nargs="?", default="review", choices=["review", "demo", "week", "export", "live"])
    ap.add_argument("--date", type=date.fromisoformat, help="any day in the week you want (YYYY-MM-DD)")
    ap.add_argument("--port", type=int, default=5700)
    ap.add_argument("--out", help="file for export (default: timesheet-<date>.csv)")
    ap.add_argument("--home", help="where Synla keeps its files (default: ~/.synla)")
    a = ap.parse_args(argv)

    if a.command == "demo":
        store = Store(a.home or (Store(a.home).dir / "demo"))
        path = store.dir / "demo-week.json"
        monday = demo.write(path)
        cfg = dict(demo.DEMO_CONFIG, source=str(path))
        store.save_config(cfg)
        print(f"Made a fictional week starting {monday:%A %d %B} in {store.dir}")
        serve(Synla(store), a.port, start=monday.date())
        return

    store = Store(a.home)
    if a.command == "live":                   # switch back from the demo to ActivityWatch
        c = store.config()
        c["source"] = "activitywatch"
        store.save_config(c)
        a.command = "review"
    app = Synla(store)
    app.forget_old()
    d = a.date or date.today()
    if a.command == "review":
        serve(app, a.port)
    elif a.command == "week":
        print(report.text(app.summary(d)))
    elif a.command == "export":
        out = a.out or f"timesheet-{d}.csv"
        open(out, "w", encoding="utf-8", newline="").write(csv_text(app, d))
        print("wrote", out)


if __name__ == "__main__":
    main()
