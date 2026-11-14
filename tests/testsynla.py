import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from synla import demo, protect, recorder, report
from synla.app import DELETED, Synla
from synla.blocks import segments
from synla.classify import Classifier
from synla.context import features
from synla.server import ThreadingHTTPServer, make_handler
from synla.source import SynlaRecorder
from synla.store import Store

# What a user would answer for each demo window: (project, kind)
TRUTH = {
    "quality-gate.py": ("Data platform", "project"), "job-risk.py": ("Data platform", "project"),
    "Lakehouse Sales": ("Data platform", "project"), "Pipeline run history": ("Data platform", "project"),
    "Supplier audit checklist": ("Supplier audit", "project"), "Supplier audit report": ("Supplier audit", "project"),
    "Monthly KPI report": ("Monthly report", "project"), "Board pack": ("Monthly report", "project"),
    "Chat | Service lead": ("Internal", "helping"), "Chat | Sales team": ("Internal", "helping"),
    "Chat | New colleague": ("Internal", "helping"), "Where is the delivery report": ("Monthly report", "chasing"),
    "supplier certificates": ("Supplier audit", "chasing"), "Invoice export failed": ("Data platform", "firefighting"),
    "Integration error log": ("Data platform", "firefighting"), "Customer list FINAL": ("Internal", "datafixing"),
    "Product codes cleanup": ("Internal", "datafixing"), "Inbox - Outlook": ("Internal", "admin"),
    "Weekly status meeting": ("Internal", "meeting"), "standup": ("Data platform", "meeting"),
    "Timesheet - Intranet": ("Internal", "admin"),
}


def truth(title):
    return next(v for k, v in TRUTH.items() if k in title)


MONDAY = datetime(2026, 11, 2).astimezone()
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@pytest.fixture
def app(tmp_path):
    store = Store(tmp_path)
    demo.write(tmp_path / "w.json", MONDAY)
    store.save_config(dict(demo.DEMO_CONFIG, source=str(tmp_path / "w.json")))
    return Synla(store)


def answer_all(app, d):
    for b in app.day(d):
        app.answer_block(d, b.id, *truth(b.main.title))


def ev(t, minutes, **data):
    return {"timestamp": t.isoformat(), "duration": minutes * 60, "data": data}


# ---- clues and segments ------------------------------------------------------------------------------------------
def test_features_pick_out_the_clues():
    f = features("EXCEL.EXE", "Copy of Customer list FINAL (2).xlsx - Excel")
    assert "file:copy of customer list final (2)" in f and "ext:xlsx" in f and "app:excel" in f
    f = features("OUTLOOK.EXE", "RE: Any update on the supplier certificates? - Outlook")
    assert "subject:any update on the supplier certificates?" in f and "is:asking" in f and "is:reply" in f
    assert "folder:data-platform" in features("Code.exe", "gold.py - data-platform - Visual Studio Code")
    assert "is:meeting" in features("EXCEL.EXE", "Notes.xlsx - Excel", call=True)


def test_idle_time_is_cut_out():
    t = MONDAY.replace(hour=9)
    win = [ev(t, 60, app="a", title="x")]
    afk = [ev(t + timedelta(minutes=20), 10, status="afk")]
    assert [round(s.minutes) for s in segments(win, afk)] == [20, 30]


def test_meetings_are_not_cut_out_as_idle():
    t = MONDAY.replace(hour=10)
    afk = [ev(t + timedelta(minutes=10), 30, status="afk")]
    meeting = segments([ev(t, 60, app="ms-teams.exe", title="Q4 planning | Microsoft Teams")], afk)
    chat = segments([ev(t, 60, app="ms-teams.exe", title="Chat | Sales team | Microsoft Teams")], afk)
    assert round(sum(s.minutes for s in meeting)) == 60 and meeting[0].call
    assert round(sum(s.minutes for s in chat)) == 30
    long_away = [ev(t + timedelta(minutes=10), 150, status="afk")]       # over two hours: away, even in a call
    assert round(sum(s.minutes for s in segments([ev(t, 180, app="ms-teams.exe", title="Q4 | Microsoft Teams")],
                                                 long_away))) == 30


# ---- learning ------------------------------------------------------------------------------------------------------
def test_learns_from_one_answer():
    c = Classifier({"projects": {}})
    assert c.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel").project == "?"
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project")
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project")
    g = c.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel")
    assert (g.project, g.kind) == ("Budget", "project") and g.confidence >= 0.7
    assert any("budget 2027" in r for r in g.reasons)
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project", weight=-1)    # taking an answer back
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project", weight=-1)
    assert c.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel").project == "?"


def guessed_right(app, days):
    right = total = 0.0
    for d in days:
        for b in app.day(d):
            for s in b.segments:
                total += s.minutes
                right += s.minutes * ((b.guess.project, b.guess.kind) == truth(s.title))
    return right / total


def test_one_day_of_answers_makes_the_rest_of_the_week_better(app):
    rest = [(MONDAY + timedelta(days=i)).date() for i in range(1, 5)]
    before = guessed_right(app, rest)
    answer_all(app, MONDAY.date())
    after = guessed_right(app, rest)
    print(f"\nshare of time guessed right, Tue-Fri: before {before:.0%}, after Monday's answers {after:.0%}")
    assert after > before + 0.1 and after > 0.8


def test_a_glance_is_not_learned(tmp_path):
    t = MONDAY.replace(hour=9)
    data = {"window": [ev(t, 20, app="EXCEL.EXE", title="Budget 2027.xlsx - Excel"),
                       ev(t + timedelta(minutes=20), 0.5, app="ms-teams.exe", title="Chat | Sales team | Microsoft Teams"),
                       ev(t + timedelta(minutes=20.5), 20, app="EXCEL.EXE", title="Budget 2027.xlsx - Excel")], "afk": []}
    (tmp_path / "g.json").write_text(json.dumps(data))
    store = Store(tmp_path)
    store.save_config({"projects": {"Budget": {"keywords": []}}, "source": str(tmp_path / "g.json")})
    a = Synla(store)
    blocks = a.day(MONDAY.date())
    assert len(blocks) == 1 and len(blocks[0].segments) == 3          # the glance joined the Excel block
    a.answer_block(MONDAY.date(), blocks[0].id, "Budget", "project")
    assert a.classifier.guess("ms-teams.exe", "Chat | Sales team | Microsoft Teams").project != "Budget"
    assert a.classifier.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel").project == "Budget"


def test_answer_by_window_covers_the_whole_day(app):
    d = MONDAY.date()
    w = next(w for w in app.unsorted_windows(app.day(d)) if "Inbox" in w["title"])
    n = app.answer(d, w["ids"], "Internal", "admin", "window")
    assert n == len(w["ids"]) and n > 1
    assert all(b.confirmed for b in app.day(d) if b.main.title == "Inbox - Outlook")


# ---- quality measure -----------------------------------------------------------------------------------------------
def test_bulk_approvals_do_not_count_as_right_guesses(app):
    d = (MONDAY + timedelta(days=1)).date()
    answer_all(app, MONDAY.date())
    mine = app.accuracy()["answers"]
    assert app.approve_confident(d) > 0
    acc = app.accuracy()
    assert acc["answers"] == mine and acc["bulk"] > 0 and acc["bulk_changed"] == 0
    b = next(b for b in app.day(d) if b.confirmed)
    app.answer_block(d, b.id, "Internal", "firefighting")              # you change a bulk approval later
    assert app.accuracy()["bulk_changed"] == 1


def test_summary_finds_the_unplanned_work(app):
    for i in range(5):
        answer_all(app, (MONDAY + timedelta(days=i)).date())
    s = app.summary(MONDAY.date())
    assert 30 < s["hours"] < 50
    assert 0.05 < s["invisible_share"] < 0.5
    assert s["after_hours"] > 0.5                    # the evening catch-up on Monday and Thursday
    assert {r["kind"] for r in s["top_invisible"]} <= {"helping", "firefighting", "chasing", "datafixing"}
    ts = report.timesheet(app.week(MONDAY.date()))
    assert all(h * 4 == int(h * 4) for day in ts.values() for h in day.values())   # quarter hours


# ---- privacy -------------------------------------------------------------------------------------------------------
def test_redact():
    assert report.redact("Mail from ola@firma.no, call +47 915 00 000, order 1234567") == \
        "Mail from [email], call [number], order [number]"


def test_learned_clues_are_not_readable(app):
    answer_all(app, MONDAY.date())
    text = (app.store.dir / "memory.json").read_text(encoding="utf-8").lower()
    # only codes and your own project names; nothing from the window titles
    assert not any(w in text for w in ("certificates", "invoice", "customer list", "quality-gate", "delivery report"))
    assert app.data_info()["clues"] > 0


def test_old_answers_and_log_are_deleted(app):
    answer_all(app, MONDAY.date())
    assert app.store.decisions() and app.store.corrections()
    app.store.forget(keep_days=-1)                   # everything is older than "tomorrow"
    assert not app.store.decisions() and not app.store.corrections()


def test_delete_everything(app):
    answer_all(app, MONDAY.date())
    app.store.delete_everything()
    assert not app.store.decisions() and not app.store.memory() and not app.store.corrections()


# ---- the built-in recorder, driven by a fake Windows -----------------------------------------------------------------
class FakeWindows:
    """Plays a script of (app, title, idle seconds, microphone in use), one line per sample."""

    def __init__(self, script):
        self.script, self.i = script, 0

    def now(self):
        return self.script[min(self.i, len(self.script) - 1)]

    def foreground(self):
        app, title, _, _ = self.now()
        return None if app is None else (app, title)

    def idle_seconds(self):
        return self.now()[2]

    def mic_in_use(self):
        return self.now()[3]


def record(store, script, start):
    probe, t = FakeWindows(script), [start.timestamp()]

    def sleep(_):
        probe.i += 1
        t[0] += recorder.POLL
    recorder.run(store, probe, clock=lambda: t[0], sleep=sleep, polls=len(script))


EXCEL = ("EXCEL.EXE", "Budget 2027.xlsx - Excel", 0, False)


def test_recorder_keeps_calls_skips_private_windows_and_away_time(tmp_path):
    store = Store(tmp_path)
    store.save_config({"source": "synla"})
    script = [EXCEL] * 6 + \
        [("ms-teams.exe", "Q4 planning | Microsoft Teams", 400, True)] * 3 + \
        [("EXCEL.EXE", "Budget 2027.xlsx - Excel", 400, False)] * 2 + \
        [("keepass.exe", "Passwords - KeePass", 0, False), ("msedge.exe", "Bank [InPrivate] - Microsoft Edge", 0, False)] + \
        [EXCEL] * 2 + [(None, None, 0, False)]
    start = MONDAY.replace(hour=9)
    record(store, script, start)
    db = recorder.connect(store)
    raw = db.execute("SELECT kind, app, title FROM activity").fetchall()
    db.close()
    assert not any(b"Bank" in bytes(r[2] or b"") or b"KeePass" in bytes(r[2] or b"") for r in raw)
    win, _ = SynlaRecorder(store).events(start, start + timedelta(hours=1))
    kinds = [(e["data"]["app"], e["data"]["call"]) for e in win]
    assert kinds == [("EXCEL.EXE", False), ("ms-teams.exe", True), ("Private", False), ("EXCEL.EXE", False)]
    assert win[2]["data"]["title"] == ""
    segs = segments(win, [])
    assert segs[1].call and round(segs[0].minutes * 60) == 25          # six samples, five seconds apart


def test_recorder_trims_to_the_last_input_and_respects_pause(tmp_path):
    store = Store(tmp_path)
    start = MONDAY.replace(hour=9)
    # five minutes of work, then the idle limit is passed: the stretch ends at the last input, 200 seconds earlier
    record(store, [EXCEL] * 60 + [("EXCEL.EXE", "Budget 2027.xlsx - Excel", 200, False)], start)
    win, _ = SynlaRecorder(store).events(start, start + timedelta(hours=1))
    assert len(win) == 1 and round(win[0]["duration"]) == 100
    store.pause(30)
    record(store, [EXCEL] * 5, start + timedelta(minutes=10))
    win2, _ = SynlaRecorder(store).events(start, start + timedelta(hours=1))
    assert len(win2) == len(win)


def test_window_names_expire_but_the_timesheet_stays(tmp_path):
    store = Store(tmp_path)
    store.save_config({"source": "synla", "projects": {"Budget": {"keywords": ["budget"]}}})
    start = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=1)
    record(store, [EXCEL] * 120, start)                                   # ten minutes of Excel yesterday
    a = Synla(store)
    d = start.date()
    for b in a.day(d):
        a.answer_block(d, b.id, "Budget", "project")
    minutes = sum(b.minutes for b in a.day(d))
    recorder.prune(store, 0)                                              # window names deleted
    after = a.day(d)
    assert after and all(b.confirmed and b.main.title == DELETED for b in after)
    assert abs(sum(b.minutes for b in after) - minutes) < 0.1


def test_autostart_command_runs_the_recorder_without_a_window():
    cmd = recorder.autostart_command()
    assert "main(['record'])" in cmd and cmd.startswith('"')


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_encryption_round_trip():
    secret = "Budget 2027.xlsx - Excel"
    blob = protect.enc(secret)
    assert secret.encode() not in blob and protect.dec(blob) == secret


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_probe_answers():
    p = recorder.WindowsProbe()
    fg = p.foreground()
    assert fg is None or isinstance(fg[1], str)
    assert p.idle_seconds() >= 0
    assert p.mic_in_use() in (True, False)


# ---- the review page ---------------------------------------------------------------------------------------------
@pytest.fixture
def server(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, "secret-token", port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", port
    httpd.shutdown()


def post(url, body, headers):
    req = urllib.request.Request(url, method="POST", headers=headers, data=json.dumps(body).encode())
    try:
        return LOCAL.open(req).status
    except urllib.error.HTTPError as e:
        return e.code


def test_review_page_and_answers_over_http(app, server):
    base, _ = server
    d = MONDAY.date()
    html = LOCAL.open(f"{base}/day?date={d}").read().decode()
    assert "need your answer" in html and "Biggest unsorted windows" in html and "secret-token" in html
    b = app.day(d)[0]
    body = {"date": str(d), "ids": [s.id for s in b.segments], "project": "Internal", "kind": "admin", "how": "block"}
    ok = {"Content-Type": "application/json", "X-Synla-Token": "secret-token"}
    assert post(f"{base}/api/answer", body, ok) == 200
    assert app.day(d)[0].confirmed
    assert "unplanned work" in LOCAL.open(f"{base}/week?date={d}").read().decode()
    assert "Never recorded" in LOCAL.open(f"{base}/data").read().decode()
    assert LOCAL.open(f"{base}/export.csv?date={d}").read().decode().startswith("date,project,hours")


def test_other_websites_cannot_change_anything(app, server):
    base, port = server
    body = {"date": str(MONDAY.date()), "ids": [], "project": "Internal", "kind": "admin"}
    assert post(f"{base}/api/answer", body, {"Content-Type": "application/json"}) == 403              # no token
    assert post(f"{base}/api/answer", body, {"Content-Type": "text/plain", "X-Synla-Token": "secret-token"}) == 403
    assert post(f"{base}/api/delete", {"what": "all"}, {"Content-Type": "application/json",
                                                         "X-Synla-Token": "wrong"}) == 403
    req = urllib.request.Request(f"{base}/day", headers={"Host": f"evil.example:{port}"})
    with pytest.raises(urllib.error.HTTPError) as e:
        LOCAL.open(req)
    assert e.value.code == 403
