"""Write sessions to vocab/tests/ as they happen (GitHub issue nhanpc/DET#5).

sessions/<id>.json  rewritten after every answer — the full session, resumable
results.csv         one row per finished block
misses.csv          one row per wrong answer: real word rejected (miss) or invented word accepted (false_alarm)
levels.csv          one row per finished session
practice/attempts.csv  one row per drill attempt, every task type (issue #10; the columns are in practice/README.md)
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .adaptive import Block, Result, Session
from .bank import PRACTICE, VOCAB

TESTS = VOCAB / "tests"
SESSIONS = TESTS / "sessions"
RESULTS = TESTS / "results.csv"
MISSES = TESTS / "misses.csv"
LEVELS = TESTS / "levels.csv"
ATTEMPTS = PRACTICE / "attempts.csv"

RESULTS_HEADER = ["date", "session", "subband", "n", "hits", "false_alarms", "score"]
MISSES_HEADER = ["date", "session", "subband", "word", "kind", "ms"]
LEVELS_HEADER = ["date", "session", "level", "det_low", "det_high", "blocks", "items", "fa_rate", "reliable"]
ATTEMPTS_HEADER = ["date", "attempt", "task", "item", "subband", "seconds", "timed_out", "score", "self", "words",
                   "errors", "file"]


def _append(path: Path, header: list[str], rows: list[list]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(header)
        w.writerows(rows)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def session_dict(s: Session) -> dict:
    d = {
        "id": s.id,
        "started": s.started.isoformat(timespec="seconds"),
        "finished": s.finished,
        "stop_reason": s.stop_reason,
        "blocks": [
            {"no": b.no, "subband": b.subband, "pos": b.pos, "hits": b.hits, "false_alarms": b.false_alarms,
             "score": b.score if b.done else None, "items": [asdict(i) for i in b.items]}
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
              r.det_high if r.det_high is not None else "", r.blocks, r.items, r.fa_rate, int(r.reliable)]])


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
    return _read(LEVELS)


def load_results() -> list[dict]:
    return _read(RESULTS)


def append_attempt(row: dict) -> None:
    """One practice/attempts.csv row (ATTEMPTS_HEADER order; missing keys blank, None blank, bools 0/1)."""
    def cell(v):
        return "" if v is None else int(v) if isinstance(v, bool) else v
    _append(ATTEMPTS, ATTEMPTS_HEADER, [[cell(row.get(k, "")) for k in ATTEMPTS_HEADER]])


def load_attempts() -> list[dict]:
    return _read(ATTEMPTS)
