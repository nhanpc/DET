"""Write finished sessions to vocab/tests/ and missed words to vocab/my-words.csv."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .adaptive import Result, Session
from .bank import VOCAB

TESTS = VOCAB / "tests"
SESSIONS = TESTS / "sessions"
RESULTS = TESTS / "results.csv"          # one row per block (Phase 2 schema)
LEVELS = TESTS / "levels.csv"            # one row per session
MY_WORDS = VOCAB / "my-words.csv"

RESULTS_HEADER = ["date", "session", "subband", "n", "hits", "false_alarms", "score"]
LEVELS_HEADER = ["date", "session", "level", "det_low", "det_high", "blocks", "items", "fa_rate", "reliable"]


def _append(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(header)
        w.writerows(rows)


def session_dict(s: Session, r: Result) -> dict:
    return {
        "id": s.id,
        "started": s.started.isoformat(timespec="seconds"),
        "stop_reason": s.stop_reason,
        "blocks": [
            {"no": b.no, "subband": b.subband, "hits": b.hits, "false_alarms": b.false_alarms, "score": b.score,
             "items": [asdict(i) for i in b.items]}
            for b in s.blocks if b.done
        ],
        "result": {**{k: v for k, v in asdict(r).items() if k not in ("misses", "pooled")},
                   "pooled": [asdict(p) for p in r.pooled],
                   "misses": [i.word for i in r.misses]},
    }


def save_session(s: Session, r: Result) -> Path:
    SESSIONS.mkdir(parents=True, exist_ok=True)
    path = SESSIONS / f"{s.id}.json"
    path.write_text(json.dumps(session_dict(s, r), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    date = s.started.date().isoformat()
    _append(RESULTS, RESULTS_HEADER,
            [[date, s.id, b.subband, len(b.items), b.hits, b.false_alarms, b.score] for b in s.blocks if b.done])
    _append(LEVELS, LEVELS_HEADER,
            [[date, s.id, r.level or "", r.det_low if r.det_low is not None else "",
              r.det_high if r.det_high is not None else "", r.blocks, r.items, r.fa_rate, int(r.reliable)]])
    return path


def load_levels() -> list[dict]:
    if not LEVELS.exists():
        return []
    with LEVELS.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save_misses(words: list[str], index: dict[str, dict]) -> int:
    """Append missed families to my-words.csv (same columns as index.csv). Returns rows added."""
    header = list(next(iter(index.values())).keys())
    have: set[str] = set()
    if MY_WORDS.exists():
        with MY_WORDS.open(encoding="utf-8", newline="") as f:
            have = {r["family"] for r in csv.DictReader(f)}
    rows = [[index[w][c] for c in header] for w in words if w in index and w not in have]
    if rows:
        _append(MY_WORDS, header, rows)
    return len(rows)
