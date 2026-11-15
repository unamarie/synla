"""Turns an app name and window title into features: the clues used to work out what the work was for.

A window title like "Budget 2027.xlsx - Excel" or "RE: Missing delivery report - Outlook" says a lot more than the app
name alone. We pull out the app, file names, folders, email subjects, numbers and plain words, each with a prefix so
the classifier can weigh them differently ("file:budget 2027" is a much stronger clue than "word:report").

Numbers matter in ERP, PLM and CRM screens, where the item, order or change number is often the only link to the work:
"Display Material 4500-1203" gives "id:4500-1203". Years, dates, times, versions and small counts are not numbers in
that sense and are left out.
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
# An email opened in its own window: "RE: Seal kit - Message (HTML)", in Norwegian "Melding (HTML)"
MESSAGE_SUFFIX = re.compile(r"\s+[-\u2013]\s+(Message|Melding)\s+\((HTML|Plain Text|Rich Text|ren tekst|rik tekst)\)$", re.I)
EMAIL_ADDRESS = re.compile(r"\S+@\S+")
TOKEN = re.compile(r"[0-9A-Za-zÆØÅæøå][0-9A-Za-zÆØÅæøå\-./_]*")
NOT_ID = re.compile(r"(19|20)\d\d|\d{4}-\d\d?-\d\d?|\d\d?[./]\d\d?([./]\d{2,4})?|\d+[.,]\d{1,2}|(v|rev|ver)\.?\d+(\.\d+)*")
LETTERS_THEN_NUMBER = re.compile(r"[a-zæøå]{1,6}[-_/.]?\d[\d\-./_]*[a-zæøå]{0,3}")
EXTS = set("xlsx xlsm xls csv docx doc pptx pdf py ipynb sql pbix pbip json md txt html js ts tmdl".split())
WORD = re.compile(r"[A-Za-zÆØÅæøå][A-Za-zÆØÅæøå0-9_]{2,}")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def strip_app(title: str) -> str:
    t = title
    for _ in range(2):            # "Chat | Sales team | Microsoft Teams" has two suffixes
        t = MESSAGE_SUFFIX.sub("", APP_SUFFIX.sub("", t))
    return t.strip()


def numbers(t: str) -> list:
    """Item, order, change and document numbers in a title: 4500-1203, ECR-2026-0153, 5523019, P-45."""
    out = []
    for tok in TOKEN.findall(EMAIL_ADDRESS.sub(" ", t)):
        tok = tok.strip("-./_").lower()
        digits = sum(c.isdigit() for c in tok)
        if not digits or len(tok) > 30 or NOT_ID.fullmatch(tok):
            continue
        if tok[0].isalpha():
            ok = digits >= 2 and LETTERS_THEN_NUMBER.fullmatch(tok)
        else:
            ok = digits >= 4 if tok.isdigit() else digits >= 3
        if ok and tok not in out:
            out.append(tok)
    return out


def features(app: str, title: str, call: bool = False) -> list:
    """Return a list of 'type:value' strings. The same input always gives the same list."""
    return list(dict.fromkeys(_features(app, title, call)))


def _features(app: str, title: str, call: bool) -> list:
    out = ["is:meeting"] if call else []
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

    for n in numbers(t):
        out.append(f"id:{n}")

    seen = set()
    for w in WORD.findall(t):
        w = w.lower()
        if w in STOP or w in EXTS or w.isdigit() or w in seen:
            continue
        seen.add(w)
        out.append(f"word:{w}")
    return out
