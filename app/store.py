"""Write sessions to vocab/tests/ as they happen (GitHub issue nhanpc/DET#5).

sessions/<id>.json  rewritten after every answer — the full session, resumable
results.csv         one row per finished block
misses.csv          one row per wrong answer: real word rejected (miss) or invented word accepted (false_alarm)
levels.csv          one row per finished session (theta, se since #14; older rows read back with blanks)
mocks.csv           one row per full DET practice test, typed by hand (issue #11 fixes the schema; never written here)
practice/attempts.csv  one row per drill attempt, every task type (issue #10; the columns are in practice/README.md;
                       theta, b, events since #16 — older rows read back with blanks)
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .adaptive import RECENT_DAYS, Block, Result, Session
from .bank import PRACTICE, VOCAB

TESTS = VOCAB / "tests"
SESSIONS = TESTS / "sessions"
RESULTS = TESTS / "results.csv"
MISSES = TESTS / "misses.csv"
LEVELS = TESTS / "levels.csv"
MOCKS = TESTS / "mocks.csv"
ATTEMPTS = PRACTICE / "attempts.csv"

RESULTS_HEADER = ["date", "session", "subband", "n", "hits", "false_alarms", "score"]
MISSES_HEADER = ["date", "session", "subband", "word", "kind", "ms"]
LEVELS_HEADER = ["date", "session", "level", "det_low", "det_high", "blocks", "items", "fa_rate", "reliable", "theta", "se"]
MOCKS_HEADER = ["date", "source", "overall", "literacy", "comprehension", "conversation", "production", "weakest", "notes"]
ATTEMPTS_HEADER = ["date", "attempt", "task", "item", "subband", "seconds", "timed_out", "score", "self", "words",
                   "errors", "file", "theta", "b", "events"]          # theta, b, events since #16 (blank before)


def _append(path: Path, header: list[str], rows: list[list]) -> None:
    """Append rows; a file whose header is a prefix of `header` (levels.csv from before #14) is rewritten once
    with the new header, its old rows padded with blanks."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    if not new:
        with path.open(encoding="utf-8", newline="") as f:
            old = list(csv.reader(f))
        if old and old[0] != header and old[0] == header[: len(old[0])]:
            pad = len(header) - len(old[0])
            with path.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, lineterminator="\n")
                w.writerow(header)
                w.writerows(r + [""] * pad for r in old[1:])
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(header)
        w.writerows(rows)


def _read(path: Path, header: Optional[list[str]] = None) -> list[dict]:
    """Rows as dicts; a `header` column the file does not have (an old levels.csv without theta) reads as ""."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, restval=""))
    for k in header or ():
        for r in rows:
            r.setdefault(k, "")
    return rows


def session_dict(s: Session) -> dict:
    d = {
        "id": s.id,
        "started": s.started.isoformat(timespec="seconds"),
        "finished": s.finished,
        "stop_reason": s.stop_reason,
        "theta0": s.theta0,
        "blocks": [
            {"no": b.no, "subband": b.subband, "pos": b.pos, "hits": b.hits, "false_alarms": b.false_alarms,
             "score": b.score if b.done else None, "theta_from": b.theta_from, "theta": b.theta, "se": b.se,
             "items": [asdict(i) for i in b.items]}
            for b in s.blocks
        ],
    }
    if s.finished:
        r = s.result()
        d["result"] = {**{k: v for k, v in asdict(r).items() if k not in ("misses", "pooled")},
                       "pooled": [asdict(p) for p in r.pooled],
                       "misses": [i.word for i in r.misses]}
    return d


def write_session(s: Session) -> Path:
    SESSIONS.mkdir(parents=True, exist_ok=True)
    path = SESSIONS / f"{s.id}.json"
    path.write_text(json.dumps(session_dict(s), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def save_block(s: Session, b: Block) -> None:
    date = s.started.date().isoformat()
    _append(RESULTS, RESULTS_HEADER, [[date, s.id, b.subband, len(b.items), b.hits, b.false_alarms, b.score]])
    _append(MISSES, MISSES_HEADER,
            [[date, s.id, b.subband, i.word, "miss" if i.real else "false_alarm", i.ms if i.ms is not None else ""]
             for i in b.items if not i.correct])


def save_result(s: Session, r: Result) -> None:
    _append(LEVELS, LEVELS_HEADER,
            [[s.started.date().isoformat(), s.id, r.level or "", r.det_low if r.det_low is not None else "",
              r.det_high if r.det_high is not None else "", r.blocks, r.items, r.fa_rate, int(r.reliable),
              r.theta, r.se]])


def load_sessions() -> list[dict]:
    """Every saved session, oldest first. Files from before #5 have no `finished` flag but always a result."""
    out = []
    for p in sorted(SESSIONS.glob("*.json")) if SESSIONS.exists() else []:
        d = json.loads(p.read_text(encoding="utf-8"))
        d.setdefault("finished", d.get("result") is not None)
        for b in d["blocks"]:
            b.setdefault("pos", len(b["items"]))
        out.append(d)
    return out


def load_levels() -> list[dict]:
    """levels.csv rows as strings; `theta` and `se` are "" on rows written before #14."""
    return _read(LEVELS, LEVELS_HEADER)


def recent_words(sessions: list[dict], days: int = RECENT_DAYS, today: Optional[date] = None) -> set[str]:
    """Every word (real or invented) shown in a session started in the last `days` days — not drawn again."""
    since = ((today or date.today()) - timedelta(days=days)).isoformat()
    return {i["word"] for s in sessions if s["started"][:10] >= since for b in s["blocks"] for i in b["items"]}


def load_results() -> list[dict]:
    return _read(RESULTS)


def load_mocks() -> list[dict]:
    """The hand-typed mocks.csv rows as strings; progress.parse_mocks() validates and types them."""
    return _read(MOCKS)


def append_attempt(row: dict) -> None:
    """One practice/attempts.csv row (ATTEMPTS_HEADER order; missing keys blank, None blank, bools 0/1)."""
    def cell(v):
        return "" if v is None else int(v) if isinstance(v, bool) else v
    _append(ATTEMPTS, ATTEMPTS_HEADER, [[cell(row.get(k, "")) for k in ATTEMPTS_HEADER]])


def load_attempts() -> list[dict]:
    """attempts.csv rows as strings; `theta`, `b` and `events` are "" on rows written before #16."""
    return _read(ATTEMPTS, ATTEMPTS_HEADER)
