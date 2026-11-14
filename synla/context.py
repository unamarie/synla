"""Turns an app name and window title into features: the clues used to work out what the work was for.

A window title like "Budget 2027.xlsx - Excel" or "RE: Missing delivery report - Outlook" says a lot more than the app
name alone. We pull out the app, file names, folders, email subjects and plain words, each with a prefix so the
classifier can weigh them differently ("file:budget 2027" is a much stronger clue than "word:report").
"""
import re

STOP = set("""a an and are as at be by for from has have in is it its of on or the to was were will with this that
these those you your our we i me my new tab window page untitled document workbook microsoft google chrome edge
firefox mozilla outlook teams excel word powerpoint code visual studio inbox mail message re fw fwd sv vs og i på til
for med som det er en et av""".split())

# Text after the last " - " or " | " in a title is usually the app name; we drop it so it isn't counted twice.
APP_SUFFIX = re.compile(r"\s+[-|\u2013\u2014]\s+(Microsoft\s+)?(Excel|Word|PowerPoint|Outlook|Teams|Visual Studio Code|"
                        r"Google Chrome|Microsoft Edge|Mozilla Firefox|Notepad\+\+|Notepad|File Explorer|Power BI Desktop|"
                        r"OneNote|Adobe Acrobat.*|Code)$", re.I)
FILE = re.compile(r"([\w\-. ()\']{2,80}?)\.(xlsx|xlsm|xls|csv|docx|doc|pptx|pdf|py|ipynb|sql|pbix|pbip|json|md|txt|html|js|ts|tmdl)\b", re.I)
PATH = re.compile(r"(?:[A-Za-z]:\\|/|~/)[^\s|]+")
EMAIL_SUBJECT = re.compile(r"^(?:(?:re|fw|fwd|sv|vs)\s*:\s*)+(.+)$", re.I)
EXTS = set("xlsx xlsm xls csv docx doc pptx pdf py ipynb sql pbix pbip json md txt html js ts tmdl".split())
WORD = re.compile(r"[A-Za-zÆØÅæøå][A-Za-zÆØÅæøå0-9_]{2,}")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def strip_app(title: str) -> str:
    t = title
    for _ in range(2):            # "Chat | Sales team | Microsoft Teams" has two suffixes
        t = APP_SUFFIX.sub("", t)
    return t.strip()


def features(app: str, title: str) -> list:
    """Return a list of 'type:value' strings. The same input always gives the same list."""
    out = []
    a = norm(app).removesuffix(".exe")
    if a:
        out.append(f"app:{a}")
    t = strip_app(title or "")
    if not t:
        return out
    out.append(f"win:{a}|{norm(t)[:80]}")      # the exact same window as before: the strongest clue of all

    for m in FILE.finditer(t):
        stem = norm(m.group(1)).split("\\")[-1].split("/")[-1]
        out.append(f"file:{stem}")
        out.append(f"ext:{m.group(2).lower()}")
    for m in PATH.finditer(t):
        parts = [p for p in re.split(r"[\\/]", m.group(0)) if p and not p.endswith(":")]
        for p in parts[:-1][-2:]:  # the two folders closest to the file
            out.append(f"folder:{norm(p)}")

    # "repo - path - Visual Studio Code" style: the part right before the editor name is often the project folder
    if a in ("code", "vscode", "visual studio code") or "visual studio code" in title.lower():
        bits = [b.strip() for b in re.split(r"\s+[-\u2013]\s+", t) if b.strip()]
        if len(bits) >= 2:
            out.append(f"folder:{norm(bits[-1])}")

    m = EMAIL_SUBJECT.match(t)
    if m:
        out.append(f"subject:{norm(m.group(1))[:60]}")
        out.append("is:reply")

    # Teams/Slack: "Chat | Sales team" or "Sales team (Chat)"
    if re.search(r"\bchat\b", t, re.I):
        out.append("is:chat")
    if re.search(r"\b(meeting|call|møte|standup|stand-up|review)\b", t, re.I):
        out.append("is:meeting")
    if re.search(r"\b(urgent|asap|haster|error|failed|feil|down|incident|broken)\b", t, re.I):
        out.append("is:urgent")
    if re.search(r"\?\s*$|\b(where is|any update|status on|waiting for|reminder|purring|påminnelse)\b", t, re.I):
        out.append("is:asking")

    seen = set()
    for w in WORD.findall(t):
        w = w.lower()
        if w in STOP or w in EXTS or w.isdigit() or w in seen:
            continue
        seen.add(w)
        out.append(f"word:{w}")
    return out
