"""Learner: read the test history, find the frontier sub-band, list the words to learn next.
GitHub issue nhanpc/DET#6. Pure functions over the session dicts written by store.py.

Word status, from every time the word was shown:
  repeat   missed twice or more, still wrong the last time
  missed   wrong the last time it was shown
  learned  missed before, right the last time  → skipped unless missed again
  shaky    right, but slower than SLOW × that session's median answer time
  known    right and quick
Study order: repeat → missed in the frontier sub-band → other misses (newest first) → shaky
→ the rest of the frontier sub-band by rank.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import median
from typing import Optional

from .bank import VOCAB, WORD, Subband

BATCH = 20
SLOW = 2.0            # a correct answer this many times slower than the session median → shaky
RECENCY = 0.5         # weight of a session relative to the next more recent one
DECKS = VOCAB / "decks"
RELATIONS = VOCAB / "relations.csv"


@dataclass
class WordStat:
    word: str
    subband: str
    shown: int = 0
    missed: int = 0
    last: str = ""                 # "miss" or "correct"
    last_date: str = ""
    last_at: str = ""              # session start timestamp, for ordering
    slow: bool = False             # last correct answer was slow
    dates: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.last == "miss":
            return "repeat" if self.missed >= 2 else "missed"
        if self.missed:
            return "learned"
        return "shaky" if self.slow else "known"


def _sorted(sessions: list[dict]) -> list[dict]:
    return sorted((s for s in sessions if s.get("finished")), key=lambda s: s["started"])


def word_stats(sessions: list[dict]) -> dict[str, WordStat]:
    stats: dict[str, WordStat] = {}
    for s in _sorted(sessions):
        day = s["started"][:10]
        items = [i for b in s["blocks"] for i in b["items"] if i["real"] and i["answer"] is not None]
        times = [i["ms"] for i in items if i["answer"] and i["ms"] is not None]
        limit = SLOW * median(times) if times else None
        for b in s["blocks"]:
            for i in b["items"]:
                if not i["real"] or i["answer"] is None:
                    continue
                w = stats.setdefault(i["word"], WordStat(i["word"], b["subband"]))
                w.shown += 1
                w.last_date, w.last_at = day, s["started"]
                w.dates.append(day)
                if i["answer"]:
                    w.last = "correct"
                    w.slow = limit is not None and i["ms"] is not None and i["ms"] > limit
                else:
                    w.last, w.missed, w.slow = "miss", w.missed + 1, False
    return stats


def subband_scores(sessions: list[dict], subbands: list[Subband]) -> list[dict]:
    """Pooled score per sub-band over all reliable sessions, newer sessions weighing more."""
    done = [s for s in _sorted(sessions) if s["result"]["reliable"]]
    agg: dict[str, list[float]] = {}
    for k, s in enumerate(done):
        w = RECENCY ** (len(done) - 1 - k)
        for b in s["blocks"]:
            if b["pos"] < len(b["items"]):
                continue
            a = agg.setdefault(b["subband"], [0, 0, 0, 0, 0])
            real = [i for i in b["items"] if i["real"]]
            pseudo = [i for i in b["items"] if not i["real"]]
            a[0] += w * b["hits"]; a[1] += w * len(real); a[2] += w * b["false_alarms"]; a[3] += w * len(pseudo)
            a[4] += 1
    out = []
    for sb in subbands:
        if sb.name not in agg:
            out.append({"subband": sb.name, "blocks": 0, "score": None, "status": "untested"})
            continue
        h, nr, fa, npd, n = agg[sb.name]
        score = round(h / nr - fa / npd, 4)
        out.append({"subband": sb.name, "blocks": n, "score": score,
                    "status": "mastered" if score >= sb.mastery else "not yet"})
    return out


def frontier(scores: list[dict]) -> tuple[Optional[str], str]:
    """(level, frontier): level = highest mastered sub-band; frontier = the lowest sub-band at or below
    level+1 that was tested and failed, else the one right above the level."""
    names = [s["subband"] for s in scores]
    mastered = [s["subband"] for s in scores if s["status"] == "mastered"]
    level = mastered[-1] if mastered else None
    top = min(len(scores) - 1, names.index(level) + 1) if level else len(scores) - 1
    for s in scores[: top + 1]:
        if s["status"] == "not yet":
            return level, s["subband"]
    return level, names[top] if level else names[0]


def load_synonyms(path: Path = RELATIONS) -> dict[str, list[str]]:
    syn: dict[str, list[str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["relation"] == "synonym":
                lst = syn.setdefault(r["family"], [])
                if r["target"] not in lst:
                    lst.append(r["target"])
    return syn


def study_list(stats: dict[str, WordStat], front: str, index: dict[str, dict],
               synonyms: dict[str, list[str]], n: int = BATCH) -> list[dict]:
    def entry(word: str, reason: str) -> dict:
        r, w = index[word], stats.get(word)
        return {"family": word, "subband": r["subband"], "rank": int(r["rank"]),
                "members": [m for m in r["members"].split("|") if m], "definition": r["definition"],
                "example": r["example"], "synonyms": synonyms.get(word, [])[:5], "reason": reason,
                "missed": w.missed if w else 0, "shown": w.shown if w else 0, "last_date": w.last_date if w else ""}

    newest = sorted((w for w in stats.values() if w.word in index), key=lambda w: w.last_at, reverse=True)
    picked: list[dict] = []
    seen: set[str] = set()

    def take(words, reason):
        for w in words:
            if w.word not in seen and len(picked) < n:
                seen.add(w.word)
                picked.append(entry(w.word, reason))

    take([w for w in newest if w.status == "repeat"], "repeat")
    take([w for w in newest if w.status == "missed" and w.subband == front], "missed")
    take([w for w in newest if w.status == "missed"], "missed")
    take([w for w in newest if w.status == "shaky"], "shaky")
    rest = sorted((r for r in index.values() if r["subband"] == front and WORD.match(r["family"])
                   and r["family"] not in stats), key=lambda r: int(r["rank"]))
    take([WordStat(r["family"], front) for r in rest], "frontier")
    return picked


def level_history(levels: list[dict]) -> tuple[list[dict], str]:
    """levels.csv rows → (history, trend); trend compares the last two reliable results."""
    hist = [{"date": r["date"], "session": r["session"], "level": r["level"] or None,
             "det_low": int(r["det_low"]) if r["det_low"] else None,
             "det_high": int(r["det_high"]) if r["det_high"] else None, "reliable": r["reliable"] == "1"}
            for r in levels]
    ok = [h["det_low"] or 0 for h in hist if h["reliable"]]
    trend = "" if len(ok) < 2 else "up" if ok[-1] > ok[-2] else "down" if ok[-1] < ok[-2] else "flat"
    return hist, trend


def export_anki(entries: list[dict], day: Optional[date] = None, decks: Optional[Path] = None) -> Path:
    """One tab-separated card per family: front = word, back = forms + definition + example + synonyms."""
    decks = decks or DECKS
    decks.mkdir(parents=True, exist_ok=True)
    path = decks / f"{(day or date.today()).isoformat()}.txt"
    lines = ["#separator:tab", "#html:true", "#tags column:3"]
    for e in entries:
        back = [f"<i>{', '.join(e['members'])}</i>" if e["members"] else "", e["definition"],
                f"<q>{e['example']}</q>" if e["example"] else "",
                f"= {', '.join(e['synonyms'])}" if e["synonyms"] else ""]
        lines.append("\t".join([e["family"], "<br>".join(p for p in back if p), f"{e['subband']} {e['reason']}"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
