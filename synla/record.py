"""The record book: a sealed record of your working time, week by week, kept for years.

What a week's record holds: for each day, the stretches of time with start, end, project, kind of work and who decided
it ("you", "added" for time away from the computer, or "guess" when you never checked Synla's guess), plus totals and
the workload numbers. It holds no window names, so nothing about colleagues or customers is kept for years.

Sealing: each record gets a fingerprint (SHA-256) of the fingerprint before it plus the record itself. The records form
a chain, so changing an old record afterwards breaks every fingerprint after it, and `verify` shows where.

Changes: you can still correct a sealed week. Sealing it again adds a new version; the old version stays in the chain.

Limit, said plainly: the chain lives on your computer, so someone with access could rebuild all of it. That is why
Synla prepares an email with each week's fingerprint for you to send to yourself: the email's date, kept by your email
provider, shows that this exact record existed that week, and no rebuilt chain can match it.
"""
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

GENESIS = "0" * 64
FORMAT = "synla-week/1"

SCHEMA = """CREATE TABLE IF NOT EXISTS seals (
    seq INTEGER PRIMARY KEY,      -- order of sealing: 1, 2, 3, ...
    week TEXT NOT NULL,           -- the Monday of the week, YYYY-MM-DD
    version INTEGER NOT NULL,     -- 1 for the first seal of a week, 2 for the first change, ...
    sealedat TEXT NOT NULL,       -- when, in UTC
    statement TEXT NOT NULL,      -- the record, as canonical JSON
    prev TEXT NOT NULL,           -- the fingerprint before this one
    fingerprint TEXT NOT NULL     -- SHA-256 of prev + statement
);
CREATE TABLE IF NOT EXISTS chain (start TEXT NOT NULL);  -- where the chain starts after old records are deleted"""


def canonical(obj) -> str:
    """One exact text for a record: sorted keys, no spaces. Anyone can recompute the fingerprint from it."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(prev: str, statement_text: str) -> str:
    return hashlib.sha256((prev + statement_text).encode("utf-8")).hexdigest()


def core(statement: dict) -> dict:
    """The part of a record that describes the work, without when or how it was sealed."""
    return {k: v for k, v in statement.items() if k not in ("sealedAt", "version", "sealedBy")}


def renamed(old: dict, new: dict) -> dict:
    """The rename list after more renames: every old name points to the name it has now."""
    out = {k: new.get(v, v) for k, v in old.items()}
    out.update(new)
    return {k: v for k, v in out.items() if k != v}


def with_names(statement: dict, names: dict) -> dict:
    """A record with today's project names, to compare it with today's numbers. The record itself never changes."""
    if not names:
        return statement
    st = json.loads(json.dumps(statement))
    projects = {}
    for p, h in st.get("projects", {}).items():
        projects[names.get(p, p)] = round(projects.get(names.get(p, p), 0) + h, 2)
    st["projects"] = dict(sorted(projects.items()))
    for d in st.get("days", []):
        for r in d.get("stretches", []):
            if "project" in r:
                r["project"] = names.get(r["project"], r["project"])
    return st


class RecordBook:
    def __init__(self, store):
        self.store = store
        self.path = store.dir / "record.db"

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.executescript(SCHEMA)
        return db

    def _start(self, db) -> str:
        row = db.execute("SELECT start FROM chain").fetchone()
        return row[0] if row else GENESIS

    def seals(self, week: date | None = None) -> list:
        if not self.path.exists():
            return []
        db = self._db()
        q = "SELECT seq, week, version, sealedat, statement, prev, fingerprint FROM seals"
        rows = db.execute(q + (" WHERE week = ? ORDER BY seq" if week else " ORDER BY seq"),
                          ((week.isoformat(),) if week else ())).fetchall()
        db.close()
        keys = ("seq", "week", "version", "sealedAt", "statement", "prev", "fingerprint")
        out = []
        for r in rows:
            s = dict(zip(keys, r))
            s["data"] = json.loads(s["statement"])
            out.append(s)
        return out

    def latest(self, week: date) -> dict | None:
        s = self.seals(week)
        return s[-1] if s else None

    def latest_per_week(self) -> dict:
        out = {}
        for s in self.seals():
            out[s["week"]] = s
        return out

    def changed(self, week: date, statement: dict, names: dict | None = None) -> bool:
        """True if the week's numbers differ from its latest seal. names: renamed projects, which are no change."""
        last = self.latest(week)
        return last is not None and canonical(core(with_names(last["data"], names))) != canonical(core(statement))

    def seal(self, statement: dict, by: str = "you", only_new: bool = False) -> dict | None:
        """Adds a record to the end of the chain. The write lock is taken before reading the last fingerprint, so the
        recorder sealing a week automatically and you pressing Seal at the same moment cannot fork the chain.
        only_new: do nothing if the week was sealed in the meantime (automatic sealing)."""
        db = self._db()
        db.isolation_level = None                 # we say when the transaction starts
        try:
            db.execute("BEGIN IMMEDIATE")
            n = db.execute("SELECT COUNT(*) FROM seals WHERE week = ?", (statement["week"],)).fetchone()[0]
            if only_new and n:
                db.execute("ROLLBACK")
                return None
            last = db.execute("SELECT fingerprint FROM seals ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last[0] if last else self._start(db)
            st = dict(statement, version=n + 1, sealedBy=by,
                      sealedAt=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            text = canonical(st)
            fp = fingerprint(prev, text)
            db.execute("INSERT INTO seals (week, version, sealedat, statement, prev, fingerprint) VALUES (?,?,?,?,?,?)",
                       (st["week"], st["version"], st["sealedAt"], text, prev, fp))
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
        return self.latest(date.fromisoformat(statement["week"]))

    def verify(self) -> dict:
        """Recomputes every fingerprint in order. Any record changed after sealing shows up here."""
        if not self.path.exists():
            return {"ok": True, "seals": 0, "problems": []}
        db = self._db()
        expected = self._start(db)
        rows = db.execute("SELECT seq, week, version, statement, prev, fingerprint FROM seals ORDER BY seq").fetchall()
        db.close()
        problems = []
        for seq, week, version, text, prev, fp in rows:
            if prev != expected:
                problems.append(f"Record {seq} (week of {week}, version {version}) does not follow the one before it.")
            if fingerprint(prev, text) != fp:
                problems.append(f"Record {seq} (week of {week}, version {version}) was changed after it was sealed.")
            try:
                if json.loads(text).get("week") != week:
                    problems.append(f"Record {seq} has the wrong week.")
            except ValueError:
                problems.append(f"Record {seq} is damaged.")
            expected = fp
        return {"ok": not problems, "seals": len(rows), "problems": problems, "last": expected}

    def find(self, fp: str) -> dict | None:
        fp = fp.strip().lower()
        return next((s for s in self.seals() if s["fingerprint"] == fp), None)

    def prune(self, years: float):
        """Deletes records older than `years`, keeping the chain checkable from the first one that remains."""
        if not self.path.exists():
            return
        cutoff = (date.today() - timedelta(days=round(365.25 * years))).isoformat()
        db = self._db()
        with db:
            old = db.execute("SELECT seq, fingerprint FROM seals WHERE week < ? ORDER BY seq", (cutoff,)).fetchall()
            if old:
                # only cut whole runs from the start, so the remaining chain stays continuous
                first_kept = db.execute("SELECT MIN(seq) FROM seals WHERE week >= ?", (cutoff,)).fetchone()[0]
                limit = first_kept if first_kept is not None else old[-1][0] + 1
                last_cut = db.execute("SELECT fingerprint FROM seals WHERE seq < ? ORDER BY seq DESC LIMIT 1",
                                      (limit,)).fetchone()
                if last_cut:
                    db.execute("DELETE FROM seals WHERE seq < ?", (limit,))
                    db.execute("DELETE FROM chain")
                    db.execute("INSERT INTO chain VALUES (?)", (last_cut[0],))
        db.close()

    def delete_all(self):
        for name in ("record.db", "record.db-wal", "record.db-shm"):
            try:
                (self.store.dir / name).unlink()
            except FileNotFoundError:
                pass


# ---- what you send to yourself --------------------------------------------------------------------------------------
def email_text(seal: dict) -> tuple[str, str]:
    st = seal["data"]
    monday = date.fromisoformat(st["week"])
    subject = f"Synla record, week of {monday:%d %B %Y}" + (f" (version {st['version']})" if st["version"] > 1 else "")
    lines = [subject, ""]
    for d in st["days"]:
        day = date.fromisoformat(d["date"])
        extra = f" (outside working hours {d['outsideHours']:.2f} h)" if d.get("outsideHours") else ""
        lines.append(f"{day:%a %d.%m}  {d['hours']:.2f} h{extra}")
    lines += ["", f"Total {st['totalHours']:.2f} h. Sorted by Synla on its own {st['unconfirmedHours']:.2f} h.", ""]
    lines += ["Hours per project: " + ", ".join(f"{p} {h:.2f}" for p, h in st["projects"].items()), ""]
    lines += [f"Fingerprint: {seal['fingerprint']}", f"Previous:    {seal['prev']}", "",
              "Keep this email. Its date shows that this record existed this week.",
              "Check it on the computer with: python -m synla verify " + seal["fingerprint"][:16]]
    return subject, "\n".join(lines)


def mailto(address: str, seal: dict) -> str:
    subject, body = email_text(seal)
    return f"mailto:{quote(address or '', safe='@')}?subject={quote(subject, safe='')}&body={quote(body, safe='')}"
