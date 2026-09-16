"""Learner: read the test history, find the frontier sub-band, list the words to learn next.
GitHub issue nhanpc/DET#6. Pure functions over the session dicts written by store.py.

Word status, from every time the word was shown:
  repeat   missed twice or more, still wrong the last time
  missed   wrong the last time it was shown
  learned  missed before, right the last time  → skipped unless missed again
  shaky    right, but slower than SLOW × that session's median answer time
  known    right and quick
Study order: repeat → my-words (open rows of vocab/my-words.csv, issue #8) → missed in the frontier
sub-band → other misses (newest first) → shaky → the rest of the frontier sub-band by rank.

Every deck (the Learn-page batch, a whole sub-band, the my-words deck) goes through card_entry() and
export_anki(): one note per family for the "DET family" note type (docs/anki.md), two cards — recognise
(word → meaning) and recall (definition + gapped example → type the word).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Iterable, Optional

from .bank import VOCAB, WORD, Subband

BATCH = 20
SLOW = 2.0            # a correct answer this many times slower than the session median → shaky
RECENCY = 0.5         # weight of a session relative to the next more recent one
RETEST_DAYS = 7       # re-test the frontier this long after the last reliable test
DECKS = VOCAB / "decks"
RELATIONS = VOCAB / "relations.csv"
MY_WORDS = VOCAB / "my-words.csv"
MY_WORDS_HEADER = ["date", "family", "source", "note", "done"]
NOTE_TYPE = "DET family"
FIELDS = ["Word", "Forms", "Definition", "Example", "Gap", "Hint", "Synonyms"]   # note type fields, in order
BLANK = "_____"


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


def gap(family: str, members: list[str], example: str) -> str:
    """`example` with the headword or a family member replaced by BLANK; "" when none occurs in it.
    Whole word, case-insensitive, longest form first (`uttered` before `utter`); only the first match goes."""
    forms = sorted({w for w in [family, *members] if w}, key=len, reverse=True)
    if not forms or not example:
        return ""
    pat = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in forms) + r")\b", re.IGNORECASE)
    out, n = pat.subn(BLANK, example, count=1)
    return out if n else ""


def hint(word: str) -> str:
    """First letter + length: `utter` → `u _ _ _ _`."""
    return " ".join([word[:1], *["_"] * (len(word) - 1)])


def card_entry(word: str, reason: str, index: dict[str, dict], stats: dict[str, WordStat],
               synonyms: dict[str, list[str]], note: str = "", examples: Optional[dict[str, list[str]]] = None) -> dict:
    """One study-list row / Anki note. `examples` = Bank.examples (override first, then the senses); the
    example that contains the word is picked so the recall card has a gap, else sense 1 and a hint only.
    A family outside `index` (an *extra* my-words entry) gets subband `extra`, no rank and the note as
    definition."""
    r, w = index.get(word), stats.get(word)
    if r is None:
        subband, rank, members, syn, definition, example, gapped = "extra", None, [], [], note, "", ""
    else:
        subband, rank = r["subband"], int(r["rank"])
        members = [m for m in r["members"].split("|") if m]
        syn, definition = synonyms.get(word, [])[:5], r["definition"]
        candidates = list((examples or {}).get(word, [])) or [r["example"]]
        example, gapped = candidates[0], ""
        for e in [*candidates, note]:
            gapped = gap(word, members, e)
            if gapped:
                example = e
                break
    return {"family": word, "subband": subband, "rank": rank, "members": members, "definition": definition,
            "example": example, "gap": gapped, "hint": hint(word), "synonyms": syn, "reason": reason, "note": note,
            "missed": w.missed if w else 0, "shown": w.shown if w else 0, "last_date": w.last_date if w else ""}


def study_list(stats: dict[str, WordStat], front: str, index: dict[str, dict], synonyms: dict[str, list[str]],
               my_words: Iterable[dict] = (), n: int = BATCH, examples: Optional[dict[str, list[str]]] = None) -> list[dict]:
    """`my_words` = open my-words rows, newest first (open_my_words()); they come right after `repeat`."""
    newest = sorted((w for w in stats.values() if w.word in index), key=lambda w: w.last_at, reverse=True)
    picked: list[dict] = []
    seen: set[str] = set()

    def take(words, reason, note=""):
        for word in words:
            if word not in seen and len(picked) < n:
                seen.add(word)
                picked.append(card_entry(word, reason, index, stats, synonyms, note, examples))

    take([w.word for w in newest if w.status == "repeat"], "repeat")
    for row in my_words:
        take([row["family"]], "my-words", row["note"])
    take([w.word for w in newest if w.status == "missed" and w.subband == front], "missed")
    take([w.word for w in newest if w.status == "missed"], "missed")
    take([w.word for w in newest if w.status == "shaky"], "shaky")
    rest = sorted((r for r in index.values() if r["subband"] == front and WORD.match(r["family"])
                   and r["family"] not in stats), key=lambda r: int(r["rank"]))
    take([r["family"] for r in rest], "frontier")
    return picked


def subband_entries(subband: str, index: dict[str, dict], stats: dict[str, WordStat], synonyms: dict[str, list[str]],
                    examples: Optional[dict[str, list[str]]] = None) -> list[dict]:
    """Every family of one sub-band that the test can show (WORD), by rank — the whole-sub-band deck.
    Reason = the word's status when it has been shown, else `new`."""
    rows = sorted((r for r in index.values() if r["subband"] == subband and WORD.match(r["family"])),
                  key=lambda r: int(r["rank"]))
    return [card_entry(r["family"], stats[r["family"]].status if r["family"] in stats else "new",
                       index, stats, synonyms, "", examples) for r in rows]


def my_words_entries(my_words: Iterable[dict], index: dict[str, dict], stats: dict[str, WordStat],
                     synonyms: dict[str, list[str]], examples: Optional[dict[str, list[str]]] = None) -> list[dict]:
    """The my-words deck: one note per open row."""
    return [card_entry(r["family"], "my-words", index, stats, synonyms, r["note"], examples) for r in my_words]


# ---- vocab/my-words.csv: words met in practice (date, family, source, note, done) ------------------------

def load_my_words(path: Optional[Path] = None) -> list[dict]:
    """Every row, oldest first; `done` is the export date or blank."""
    path = path or MY_WORDS
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def open_my_words(rows: list[dict]) -> list[dict]:
    """Rows not yet exported, newest first — what the study list and the my-words deck take."""
    return [r for r in reversed(rows) if not r["done"]]


def _write_my_words(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, MY_WORDS_HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in MY_WORDS_HEADER} for r in rows)


def add_my_word(family: str, source: str, note: str = "", day: Optional[date] = None,
                path: Optional[Path] = None) -> bool:
    """Append one open row. False (nothing written) when the family already has an open row — one open row
    per family, whoever adds it; the same family can be added again once that row is done."""
    family = family.strip().lower()
    if not family:
        raise ValueError("empty family")
    path = path or MY_WORDS
    rows = load_my_words(path)
    if any(r["family"] == family and not r["done"] for r in rows):
        return False
    rows.append({"date": (day or date.today()).isoformat(), "family": family, "source": source,
                 "note": " ".join(note.split()), "done": ""})
    _write_my_words(rows, path)
    return True


def mark_done(families: Iterable[str], day: date, path: Optional[Path] = None) -> int:
    """Set `done = day` on the open row of each family (after a deck that holds them was written)."""
    wanted, n = set(families), 0
    path = path or MY_WORDS
    rows = load_my_words(path)
    for r in rows:
        if r["family"] in wanted and not r["done"]:
            r["done"], n = day.isoformat(), n + 1
    if n:
        _write_my_words(rows, path)
    return n


def level_history(levels: list[dict]) -> tuple[list[dict], str]:
    """levels.csv rows → (history, trend); trend compares the last two reliable results."""
    hist = [{"date": r["date"], "session": r["session"], "level": r["level"] or None,
             "det_low": int(r["det_low"]) if r["det_low"] else None,
             "det_high": int(r["det_high"]) if r["det_high"] else None, "reliable": r["reliable"] == "1"}
            for r in levels]
    ok = [h["det_low"] or 0 for h in hist if h["reliable"]]
    trend = "" if len(ok) < 2 else "up" if ok[-1] > ok[-2] else "down" if ok[-1] < ok[-2] else "flat"
    return hist, trend


def retest_due(levels: list[dict], front: str, today: Optional[date] = None) -> Optional[dict]:
    """When to take the next test: RETEST_DAYS after the last reliable levels.csv row, in the frontier
    sub-band. None until one reliable test exists; `days` < 0 = overdue."""
    ok = [r for r in levels if r["reliable"] == "1"]
    if not ok:
        return None
    last = date.fromisoformat(ok[-1]["date"])
    due = last + timedelta(days=RETEST_DAYS)
    return {"subband": front, "last": last.isoformat(), "due": due.isoformat(), "days": (due - (today or date.today())).days}


def export_anki(name: str, entries: list[dict], decks: Optional[Path] = None) -> Path:
    """vocab/decks/<name>.txt for Anki's text import: one note per family for the `DET family` note type
    (docs/anki.md) — FIELDS in order, then the tag `<subband> <reason>` in column 8. `Word` is the first
    column, so re-importing a regenerated file updates the notes instead of duplicating them."""
    decks = decks or DECKS
    decks.mkdir(parents=True, exist_ok=True)
    path = decks / f"{name}.txt"
    lines = ["#separator:tab", "#html:true", f"#notetype:{NOTE_TYPE}", f"#deck:DET::{name}",
             "#columns:" + "\t".join([*FIELDS, "Tags"]), "#tags column:8"]
    for e in entries:
        cols = [e["family"], ", ".join(e["members"]), e["definition"], e["example"], e["gap"], e["hint"],
                ", ".join(e["synonyms"]), f"{e['subband']} {e['reason']}"]
        lines.append("\t".join(" ".join(str(c).split()) for c in cols))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
