"""Everything Synla keeps lives in one folder on your own computer (default: ~/.synla). Nothing is sent anywhere."""
import json
import os
from datetime import datetime
from pathlib import Path

DEFAULT_CONFIG = {
    "projects": {
        "Internal": {"keywords": []},
    },
    "workday": {"start": "07:00", "end": "17:00"},
    "roundMinutes": 15,
    "keepDays": 90,
    "source": "activitywatch",
    "activityWatchUrl": "http://localhost:5600",
}


class Store:
    def __init__(self, folder: str | os.PathLike | None = None):
        self.dir = Path(folder or os.environ.get("SYNLA_HOME") or Path.home() / ".synla")
        self.dir.mkdir(parents=True, exist_ok=True)

    def _read(self, name, default):
        p = self.dir / name
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def _write(self, name, data):
        p = self.dir / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    # config.json: your projects and keywords. Edit it by hand or from the review page.
    def config(self) -> dict:
        c = self._read("config.json", None)
        if c is None:
            c = json.loads(json.dumps(DEFAULT_CONFIG))
            self._write("config.json", c)
        return {**DEFAULT_CONFIG, **c}

    def save_config(self, c: dict):
        self._write("config.json", c)

    # memory.json: what Synla has learned from your answers
    def memory(self) -> dict:
        return self._read("memory.json", {})

    def save_memory(self, m: dict):
        self._write("memory.json", m)

    # decisions.json: your answer for each segment, keyed by its start time. The timesheet uses these first.
    def decisions(self) -> dict:
        return self._read("decisions.json", {})

    def save_decisions(self, d: dict):
        self._write("decisions.json", d)

    # corrections.jsonl: an append-only log of every answer, so you can see how often Synla was wrong
    def log(self, entry: dict):
        entry = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), **entry}
        with open(self.dir / "corrections.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def corrections(self) -> list:
        p = self.dir / "corrections.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    def forget_before(self, cutoff: datetime):
        """Delete answers older than the retention period (keepDays)."""
        d = self.decisions()
        keep = {k: v for k, v in d.items() if datetime.fromisoformat(k) >= cutoff}
        if len(keep) != len(d):
            self.save_decisions(keep)
