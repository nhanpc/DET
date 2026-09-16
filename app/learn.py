"""Learner: read the test history, find the frontier sub-band, list the words to learn next.
GitHub issue nhanpc/DET#6. Pure functions over the session dicts written by store.py.

Level and frontier come from the ability θ of issue #14 (app/irt.py): theta_history() replays every finished
session through the same posterior the test used, chaining each session's prior on the last reliable θ;
frontier() turns the current θ into (level, frontier). The pooled sub-band scores stay as a second view.

Word status, from every time the word was shown — in the test, and since #16 in the drills (the `events`
column of practice/attempts.csv, merged in time order: a `vocabulary` miss is a "no", a `hit` on two different
days with no later miss is a "yes"; `form`, `hearing`, `spelling` only set the stat's `skill` note):
  repeat   missed twice or more, still wrong the last time
  missed   wrong the last time it was shown
  learned  missed before, right the last time  → skipped unless missed again
  shaky    right, but slower than SLOW × that session's median answer time
  known    right and quick
  (blank)  only drill evidence so far — one hit day, or a skill note — no status yet
Study order: repeat → my-words (open rows of vocab/my-words.csv, issue #8; a row from a dictation slip,
source `<task>:<kind>`, shows as *heard wrong* and is never exported) → missed in the frontier sub-band →
other misses (newest first) → shaky → the rest of the frontier sub-band by rank.

The priority pool (priority_pool(), issue #13): the study list without the batch cap as family → (reason, weight)
— my-words and repeat 6, missed 3, shaky 2, frontier 1 — the order every drill draws in (drills.pick_weighted).
drill_hits() reads the `hit` events back; practice_done() marks an open my-words row done once its family was
hit on HIT_DAYS different days with no error in between (`done = <date> practice`, next to the deck exports).

The ability θ of the drills (drill_theta(), #16): the last reliable test's posterior, N(θ, se²), updated by every
scored drill attempt since it (rows with a `b`, the item's difficulty, and a `score`, the credit, through
irt.snap()); θ_listen is the same with the dictation attempts only.

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

from . import irt
from .bank import VOCAB, WORD, Subband
from .drills import SKILL_KINDS, parse_events

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
STATUSES = ("repeat", "missed", "learned", "shaky", "known")   # WordStat.status values, study order
HEARD_WRONG = "heard wrong"                                      # study-list reason of a my-words row from a skill slip
DRILL_TASKS = ("listen-and-type", "read-and-complete", "fill-in-the-blanks")   # drills whose score is a credit on the scale
HIT_DAYS = 2                                                    # drill hits on this many days = a right answer / row done
WEIGHTS = {"my-words": 6, "repeat": 6, "missed": 3, "shaky": 2, "frontier": 1}   # priority_pool() tiers (#13)
PRIORITY = ("my-words", "repeat", "missed", "shaky")            # the tiers that make a family a priority word
BY_PRACTICE = "practice"                                        # the suffix of `done` on a my-words row closed by drill hits


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
    hits: list[str] = field(default_factory=list)     # days with a drill hit since the last miss (#16)
    skill: str = ""                # the last form / hearing / spelling slip in a drill, "" when none (#16)

    @property
    def status(self) -> str:
        if self.last == "miss":
            return "repeat" if self.missed >= 2 else "missed"
        if self.last == "correct":
            return "learned" if self.missed else ("shaky" if self.slow else "known")
        return ""


def attempt_time(row: dict) -> str:
    """The ISO timestamp of an attempts.csv row from its id (`2026-09-16_101010_abcd` → `2026-09-16T10:10:10`),
    comparable with a session's `started`."""
    a = row["attempt"]
    return f"{a[:10]}T{a[11:13]}:{a[13:15]}:{a[15:17]}"


def _sorted(sessions: list[dict]) -> list[dict]:
    return sorted((s for s in sessions if s.get("finished")), key=lambda s: s["started"])


def _merge_events(stats: dict[str, WordStat], row: dict, index: Optional[dict[str, dict]]) -> None:
    """One attempts.csv row's `events` into the stats (the module docstring has the rules)."""
    day, at = row["date"], attempt_time(row)
    for family, kind in parse_events(row.get("events", "")):
        w = stats.get(family) or stats.setdefault(family, WordStat(family, index[family]["subband"] if index and family in index else ""))
        if kind == "vocabulary":
            w.shown, w.missed, w.last, w.slow, w.hits = w.shown + 1, w.missed + 1, "miss", False, []
            w.last_date, w.last_at = day, at
            w.dates.append(day)
        elif kind == "hit":
            if day not in w.hits:
                w.hits.append(day)
            w.shown, w.last_date, w.last_at = w.shown + 1, day, at
            w.dates.append(day)
            if len(w.hits) >= HIT_DAYS:
                w.last, w.slow = "correct", False
        elif kind in SKILL_KINDS:
            w.skill = kind


def word_stats(sessions: list[dict], subbands: Optional[list[Subband]] = None, attempts: Optional[list[dict]] = None,
               index: Optional[dict[str, dict]] = None) -> dict[str, WordStat]:
    """`subbands` lets an item saved with its `b` (#14) carry its own sub-band; a block spans up to three, so
    the block's label is only the fallback for files from before #14. `attempts` (attempts.csv rows) merge the
    drill events of #16 in time order with the sessions; `index` gives those words their sub-band."""
    stats: dict[str, WordStat] = {}
    timeline: list[tuple[str, int, dict]] = [(s["started"], 0, s) for s in _sorted(sessions)]
    timeline += [(attempt_time(r), 1, r) for r in attempts or () if r.get("events")]
    for _, kind, s in sorted(timeline, key=lambda t: t[:2]):
        if kind:
            _merge_events(stats, s, index)
            continue
        day = s["started"][:10]
        items = [i for b in s["blocks"] for i in b["items"] if i["real"] and i["answer"] is not None]
        times = [i["ms"] for i in items if i["answer"] and i["ms"] is not None]
        limit = SLOW * median(times) if times else None
        for b in s["blocks"]:
            for i in b["items"]:
                if not i["real"] or i["answer"] is None:
                    continue
                own = irt.item_band(i["b"], subbands).name if subbands and i.get("b") is not None else b["subband"]
                w = stats.setdefault(i["word"], WordStat(i["word"], own))
                w.shown += 1
                w.last_date, w.last_at = day, s["started"]
                w.dates.append(day)
                if i["answer"]:
                    w.last = "correct"
                    w.slow = limit is not None and i["ms"] is not None and i["ms"] > limit
                else:
                    w.last, w.missed, w.slow, w.hits = "miss", w.missed + 1, False, []
    return stats


def drill_responses(attempts: Iterable[dict], since: str = "", tasks: Iterable[str] = DRILL_TASKS) -> list[tuple[float, float]]:
    """(b, credit) of every scored attempt of `tasks` after `since` (an ISO timestamp), in time order — the rows
    that carry the item's `b` (written from #16 on)."""
    rows = [r for r in attempts if r.get("task") in set(tasks) and r.get("b") and r.get("score") not in (None, "")
            and attempt_time(r) > since]
    return [(float(r["b"]), float(r["score"])) for r in sorted(rows, key=attempt_time)]


def drill_theta(history: list[dict], attempts: Iterable[dict], tasks: Iterable[str] = DRILL_TASKS) -> Optional[tuple[float, float]]:
    """(θ, se) now: the last reliable session's posterior, N(θ, se²), updated by the drill attempts since it
    (irt.snap() on each credit). None before the first reliable test — the drills then run on the frontier
    without moving θ. `tasks` = ("listen-and-type",) gives θ_listen."""
    now = current_theta(history)
    if now is None:
        return None
    last = [h for h in history if h["reliable"]][-1]
    responses = drill_responses(attempts, last["started"], tasks)
    return irt.eap(((b, irt.snap(s)) for b, s in responses), now[0], now[1]) if responses else now


def subband_scores(sessions: list[dict], subbands: list[Subband]) -> list[dict]:
    """Pooled score per sub-band over all reliable sessions, newer sessions weighing more.
    `known` = weighted hits/real (the "% known"); `score` = known − weighted false alarms/pseudo, the number
    the mastery line applies to. Both come from the same aggregation."""
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
            out.append({"subband": sb.name, "blocks": 0, "known": None, "score": None, "status": "untested"})
            continue
        h, nr, fa, npd, n = agg[sb.name]
        score = round(h / nr - fa / npd, 4)
        out.append({"subband": sb.name, "blocks": n, "known": round(h / nr, 4), "score": score,
                    "status": "mastered" if score >= sb.mastery else "not yet"})
    return out


def status_counts(stats: dict[str, WordStat]) -> dict[str, int]:
    """How many words carry each status, plus `seen` = every word shown so far (the Learn page and the
    progress report show the same numbers)."""
    counts = {k: 0 for k in STATUSES}
    for w in stats.values():
        if w.status:
            counts[w.status] += 1
    counts["seen"] = sum(counts.values())
    return counts


def session_theta(s: dict, subbands: list[Subband], b_of: Optional[dict[str, float]] = None,
                  theta0: float = irt.THETA0) -> tuple[float, float]:
    """(θ, se) of one saved session, every answered item replayed in order through irt.Posterior exactly as the
    test did: a real word at its `b` (the item's, else `b_of[word]`, else the middle of the block's sub-band),
    a pseudo-word "yes" as a wrong answer at the θ of that moment. Prior mean = the session's own `theta0`
    when saved, else `theta0`."""
    by = {sb.name: sb for sb in subbands}
    post = irt.Posterior(s.get("theta0", theta0))
    theta, se = post.estimate()
    for b in s["blocks"]:
        for i in b["items"][: b.get("pos", len(b["items"]))]:
            if i["answer"] is None:
                continue
            if i["real"]:
                b_ = i.get("b")
                if b_ is None:
                    b_ = (b_of or {}).get(i["word"], by[b["subband"]].order - 0.5)
                post.add(b_, 1.0 if i["answer"] else 0.0)
            elif i["answer"]:
                post.add(theta, 0.0)
            else:
                continue
            theta, se = post.estimate()
    return theta, se


def theta_history(sessions: list[dict], subbands: list[Subband], b_of: Optional[dict[str, float]] = None) -> list[dict]:
    """One row per finished session, oldest first: id, date, started, theta, se, level, frontier, det, det_range,
    reliable. The prior of each session is the θ of the last reliable session before it (6.0 for the first),
    unless the file carries its own `theta0`; an unreliable session gets a θ but never becomes the prior."""
    out, prior = [], irt.THETA0
    for s in _sorted(sessions):
        theta, se = session_theta(s, subbands, b_of, prior)
        reliable = bool(s["result"]["reliable"])
        out.append({"id": s["id"], "date": s["started"][:10], "started": s["started"], "theta": theta, "se": se,
                    "level": irt.level_of(theta, subbands), "frontier": irt.frontier_of(theta, subbands),
                    "det": irt.det_estimate(theta, subbands), "det_range": irt.det_range(theta, se, subbands),
                    "reliable": reliable})
        if reliable:
            prior = theta
    return out


def current_theta(history: list[dict]) -> Optional[tuple[float, float]]:
    """(θ, se) of the last reliable session — the learner's ability now and the next session's prior; None
    before the first reliable test."""
    ok = [h for h in history if h["reliable"]]
    return (ok[-1]["theta"], ok[-1]["se"]) if ok else None


def frontier(theta: Optional[float], subbands: list[Subband]) -> tuple[Optional[str], str]:
    """(level, frontier) from θ: level = the sub-band containing θ − irt.MASTERY_GAP (None below the scale),
    frontier = the sub-band containing θ. Without a reliable test (θ None): no level, the first sub-band."""
    if theta is None:
        return None, subbands[0].name
    return irt.level_of(theta, subbands), irt.frontier_of(theta, subbands)


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
        take([row["family"]], HEARD_WRONG if skill_source(row.get("source", "")) else "my-words", row["note"])
    take([w.word for w in newest if w.status == "missed" and w.subband == front], "missed")
    take([w.word for w in newest if w.status == "missed"], "missed")
    take([w.word for w in newest if w.status == "shaky"], "shaky")
    rest = sorted((r for r in index.values() if r["subband"] == front and WORD.match(r["family"])
                   and r["family"] not in stats), key=lambda r: int(r["rank"]))
    take([r["family"] for r in rest], "frontier")
    return picked


def priority_pool(stats: dict[str, WordStat], my_words: Iterable[dict], index: dict[str, dict], front: str) -> dict[str, tuple[str, int]]:
    """Family → (reason, weight) for every family a drill may draw, in study_list() order and without its cap
    (issue #13): `repeat` and open `my-words` rows (drill errors, lacked words, pins — heard-wrong rows too) at
    WEIGHTS 6, `missed` 3 (frontier sub-band first), `shaky` 2, then the `frontier` families never shown at 1. A
    family outside `index` (an *extra* my-words entry) has no sentence and is left out; one entry per family,
    the first reason wins."""
    newest = sorted((w for w in stats.values() if w.word in index), key=lambda w: w.last_at, reverse=True)
    pool: dict[str, tuple[str, int]] = {}

    def take(words, reason):
        for word in words:
            if word in index and word not in pool:
                pool[word] = (reason, WEIGHTS[reason])

    take([w.word for w in newest if w.status == "repeat"], "repeat")
    take([r["family"] for r in my_words], "my-words")
    take([w.word for w in newest if w.status == "missed" and w.subband == front], "missed")
    take([w.word for w in newest if w.status == "missed"], "missed")
    take([w.word for w in newest if w.status == "shaky"], "shaky")
    rest = sorted((r for r in index.values() if r["subband"] == front and WORD.match(r["family"]) and r["family"] not in stats),
                  key=lambda r: int(r["rank"]))
    take([r["family"] for r in rest], "frontier")
    return pool


def pool_counts(pool: dict[str, tuple[str, int]]) -> dict[str, int]:
    """`total` = the priority families (PRIORITY tiers) and one count per reason — the practice-page header."""
    counts = {k: 0 for k in WEIGHTS}
    for reason, _ in pool.values():
        counts[reason] += 1
    return {"total": sum(counts[k] for k in PRIORITY), **counts}


def reason_label(family: str, reason: str, stats: dict[str, WordStat], my_words: Iterable[dict] = ()) -> str:
    """The chip on a drill item: `missed 2× in the test`, `missed in the test`, `my-words · listen-and-type
    2026-09-14`, `shaky`; "" for a frontier word."""
    if reason == "repeat" or reason == "missed":
        n = stats[family].missed if family in stats else 1
        return f"missed {n}× in the test" if n > 1 else "missed in the test"
    if reason == "my-words":
        row = next((r for r in my_words if r["family"] == family), None)
        return f"my-words · {row['source']} {row['date']}" if row else "my-words"
    return reason if reason == "shaky" else ""


def drill_hits(attempts: Iterable[dict], since: str = "") -> dict[str, set[str]]:
    """Family → the days it was a `hit` in a drill (the `events` column) after its last error there, counting
    only rows dated `since` or later — a hit followed by an error starts over."""
    out: dict[str, set[str]] = {}
    rows = sorted((r for r in attempts if r.get("events") and r["date"] >= since), key=attempt_time)
    for r in rows:
        for family, kind in parse_events(r["events"]):
            if kind == "hit":
                out.setdefault(family, set()).add(r["date"])
            else:
                out.pop(family, None)
    return out


def practice_done(rows: list[dict], attempts: Iterable[dict], day: date, path: Optional[Path] = None) -> list[str]:
    """Close the open my-words rows whose family has drill hits on HIT_DAYS different days since the row was
    added, with no error after them (drill_hits from the row's date) — the rule the Learn page applies to cards,
    fed by practice. Returns the families marked done (`done = <day> practice`)."""
    attempts = list(attempts)
    done = [r["family"] for r in rows if not r["done"] and len(drill_hits(attempts, r["date"]).get(r["family"], ())) >= HIT_DAYS]
    if done:
        mark_done(done, day, path, BY_PRACTICE)
    return done


def done_counts(rows: Iterable[dict]) -> dict[str, int]:
    """How many my-words rows were closed by a deck export (`cards`) and by drill hits (`practice`)."""
    counts = {"cards": 0, "practice": 0}
    for r in rows:
        if r["done"]:
            counts["practice" if r["done"].endswith(BY_PRACTICE) else "cards"] += 1
    return counts


def subband_entries(subband: str, index: dict[str, dict], stats: dict[str, WordStat], synonyms: dict[str, list[str]],
                    examples: Optional[dict[str, list[str]]] = None) -> list[dict]:
    """Every family of one sub-band that the test can show (WORD), by rank — the whole-sub-band deck.
    Reason = the word's status when it has been shown, else `new`."""
    rows = sorted((r for r in index.values() if r["subband"] == subband and WORD.match(r["family"])),
                  key=lambda r: int(r["rank"]))
    return [card_entry(r["family"], (stats[r["family"]].status if r["family"] in stats else "") or "new",
                       index, stats, synonyms, "", examples) for r in rows]


def my_words_entries(my_words: Iterable[dict], index: dict[str, dict], stats: dict[str, WordStat],
                     synonyms: dict[str, list[str]], examples: Optional[dict[str, list[str]]] = None) -> list[dict]:
    """The my-words deck: one note per open row, skill slips (skill_source()) left out."""
    return [card_entry(r["family"], "my-words", index, stats, synonyms, r["note"], examples)
            for r in my_words if not skill_source(r["source"])]


def card_entries(entries: Iterable[dict]) -> list[dict]:
    """The study-list rows that become Anki notes: everything but *heard wrong* (a dictation slip is a dictation
    matter, not a card)."""
    return [e for e in entries if e["reason"] != HEARD_WRONG]


def skill_source(source: str) -> bool:
    """True for a my-words `source` written by a drill for a form / hearing / spelling slip: `<task>:<kind>`
    (`listen-and-type:hearing`). Such a row is shown as *heard wrong* on the Learn page and never exported."""
    return source.rpartition(":")[2] in SKILL_KINDS if ":" in source else False


# ---- vocab/my-words.csv: words met in practice (date, family, source, note, done) ------------------------

def load_my_words(path: Optional[Path] = None) -> list[dict]:
    """Every row, oldest first; `done` is the export date (`<date> practice` when drill hits closed it) or blank."""
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


def mark_done(families: Iterable[str], day: date, path: Optional[Path] = None, how: str = "") -> int:
    """Set `done = day` on the open row of each family (after a deck that holds them was written); `how` =
    BY_PRACTICE appends the word, so the two ways a row closes stay apart (done_counts)."""
    wanted, n = set(families), 0
    path = path or MY_WORDS
    rows = load_my_words(path)
    for r in rows:
        if r["family"] in wanted and not r["done"]:
            r["done"], n = f"{day.isoformat()} {how}".strip(), n + 1
    if n:
        _write_my_words(rows, path)
    return n


def level_history(levels: list[dict]) -> tuple[list[dict], str]:
    """levels.csv rows → (history, trend); trend compares the last two reliable results. `theta` and `se` are
    None on rows from before #14."""
    hist = [{"date": r["date"], "session": r["session"], "level": r["level"] or None,
             "det_low": int(r["det_low"]) if r["det_low"] else None,
             "det_high": int(r["det_high"]) if r["det_high"] else None, "reliable": r["reliable"] == "1",
             "theta": float(r["theta"]) if r.get("theta") else None, "se": float(r["se"]) if r.get("se") else None}
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
