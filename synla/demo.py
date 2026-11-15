"""Makes up a believable work week in ActivityWatch's format, so Synla can be tried without real data.
Everything here is invented: the company, the projects, the files and the people (who are only roles)."""
import json
import random
from datetime import datetime, timedelta

DEMO_CONFIG = {
    "projects": {
        "Data platform": {"keywords": ["lakehouse", "notebook", "pipeline", "silver", "gold", "bronze", "platform"]},
        "Supplier audit": {"keywords": ["audit", "supplier", "prequal"]},
        "Monthly report": {"keywords": ["monthly", "kpi", "board"]},
        "Internal": {"keywords": ["timesheet", "expense", "intranet"]},
    },
    "role": "specialist",
    "workday": {"start": "07:00", "end": "17:00"},
    "roundMinutes": 15,
    "keepDays": 90,
}

# (app, title, minutes low, minutes high)
FOCUS = [
    ("Code.exe", "quality-gate.py - data-platform - Visual Studio Code", 20, 70),
    ("Code.exe", "job-risk.py - data-platform - Visual Studio Code", 20, 60),
    ("msedge.exe", "Lakehouse Sales - Notebook Sales 2 Silver - Microsoft Edge", 10, 40),
    ("msedge.exe", "Pipeline run history - data platform - Microsoft Edge", 5, 15),
    ("EXCEL.EXE", "Supplier audit checklist 2026.xlsx - Excel", 15, 45),
    ("WINWORD.EXE", "Supplier audit report - draft.docx - Word", 15, 50),
    ("PBIDesktop.exe", "Monthly KPI report - Power BI Desktop", 20, 60),
    ("POWERPNT.EXE", "Board pack November.pptx - PowerPoint", 10, 30),
]
INTERRUPT = [  # the unplanned work
    ("ms-teams.exe", "Chat | Service lead | Microsoft Teams", 2, 12),
    ("ms-teams.exe", "Chat | Sales team | Microsoft Teams", 2, 15),
    ("ms-teams.exe", "Chat | New colleague in finance | Microsoft Teams", 5, 20),
    ("OUTLOOK.EXE", "RE: Where is the delivery report? - Message (HTML)", 3, 10),
    ("OUTLOOK.EXE", "RE: Any update on the supplier certificates? - Message (HTML)", 3, 8),
    ("OUTLOOK.EXE", "URGENT: Invoice export failed - Message (HTML)", 10, 35),
    ("msedge.exe", "Integration error log - Microsoft Edge", 10, 30),
    ("EXCEL.EXE", "Copy of Customer list FINAL (2).xlsx - Excel", 15, 40),
    ("EXCEL.EXE", "Product codes cleanup.xlsx - Excel", 10, 35),
]
ROUTINE = [
    ("OUTLOOK.EXE", "Inbox - Outlook", 5, 20),
    ("ms-teams.exe", "Weekly status meeting | Microsoft Teams", 30, 45),
    ("ms-teams.exe", "Data platform standup | Microsoft Teams", 15, 15),
    ("msedge.exe", "Timesheet - Intranet - Microsoft Edge", 5, 10),
]


def _ev(t, minutes, data):
    return {"timestamp": t.astimezone().isoformat(), "duration": round(minutes * 60, 1), "data": data}


def week(monday: datetime, seed: int = 7) -> dict:
    rnd = random.Random(seed)
    win, afk = [], []

    def work(t, app, title, minutes):
        win.append(_ev(t, minutes, {"app": app, "title": title}))
        afk.append(_ev(t, minutes, {"status": "not-afk"}))
        return t + timedelta(minutes=minutes)

    def idle(t, minutes):
        afk.append(_ev(t, minutes, {"status": "afk"}))
        # ActivityWatch keeps the last window while you are away; Synla must cut that time out
        if win:
            last = win[-1]["data"]
            win.append(_ev(t, minutes, dict(last)))
        return t + timedelta(minutes=minutes)

    for d in range(5):
        day = monday + timedelta(days=d)
        t = day.replace(hour=7, minute=40) + timedelta(minutes=rnd.randint(0, 35))
        end = day.replace(hour=16, minute=0) + timedelta(minutes=rnd.randint(0, 80))
        # busier days have more interruptions: Monday and Thursday are the bad ones in this story
        p_interrupt = [0.55, 0.35, 0.3, 0.6, 0.25][d]
        t = work(t, *ROUTINE[0][:2], rnd.randint(10, 25))
        if d in (0, 2, 4):
            t = work(t, *ROUTINE[2][:2], 15)
        lunch_done = met = False
        while t < end:
            if not lunch_done and t.hour * 60 + t.minute >= 11 * 60 + 20:
                t = idle(t, rnd.randint(25, 35))
                lunch_done = True
                continue
            if d == 3 and not met and t.hour * 60 + t.minute >= 13 * 60 + 30:
                t = idle(t, 75)                  # a supplier meeting in a room: the laptop stays on the desk
                met = True
                continue
            if d == 1 and t.hour == 13 and not any("Weekly status" in e["data"].get("title", "") for e in win[-30:]):
                t = work(t, *ROUTINE[1][:2], 40)
                continue
            app, title, lo, hi = rnd.choice(FOCUS)
            left = rnd.randint(lo, hi)
            while left > 0:                      # focus work, broken up by interruptions
                chunk = min(left, rnd.randint(8, 30))
                t = work(t, app, title, chunk)
                left -= chunk
                if left > 0 and rnd.random() < p_interrupt:
                    a, ti, l2, h2 = rnd.choice(INTERRUPT)
                    t = work(t, a, ti, rnd.randint(l2, h2))
                    if rnd.random() < 0.3:
                        t = work(t, *ROUTINE[0][:2], rnd.randint(2, 6))
            if rnd.random() < 0.25:
                t = idle(t, rnd.randint(4, 12))
        if d == 4:
            t = work(t, *ROUTINE[3][:2], 8)
        if d in (0, 3):                          # evening catch-up
            ev = day.replace(hour=20, minute=30) + timedelta(minutes=rnd.randint(0, 30))
            ev = work(ev, *INTERRUPT[5][:2], rnd.randint(15, 30))
            work(ev, "OUTLOOK.EXE", "Inbox - Outlook", rnd.randint(10, 20))
    return {"window": win, "afk": afk}


def write(path, monday: datetime | None = None):
    if monday is None:
        today = datetime.now().astimezone()
        monday = (today - timedelta(days=today.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    data = week(monday)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return monday
