"""DET task drills (GitHub issue nhanpc/DET#10, Phase 5). Pure functions; main.py does the I/O.

Read and Complete   cloze(): C-test damage of a senses.csv example (sentence mode) or a passage of the bank
                    (practice/read-and-complete/passages/, issue #17) — every second eligible word loses its
                    second half. Items are not stored: the id encodes the inputs (`<family>.<sense>.<seed>` /
                    `<passage-slug>.<seed>`) and cloze() is deterministic. passage_targets() / passage_seed() put
                    a priority family's word inside the 5-blank window; cloze_events() says what each blank told
                    us — hit, spelling (≤ NEAR letter edits) or vocabulary.
Fill in the Blanks  fill_blank(): one sentence of the dictation bank, the target family's form removed but for its
                    first ceil(len / 3) letters (`ten____`); id `<family>.<sense>.fb`, exact match, 20 s (#17).
Listen and Type     build_sentences(): the dictation bank (one 6–14-word example per family) cached in
                    practice/listen-and-type/sentences.csv with its b_text (app/textdiff.py, issue #15);
                    dictation_score(): the credit (character-level edit distance, issue #16) and the word-level
                    diff; dictation_events(): what each heard word says — hit, form, hearing, spelling, vocabulary.
                    in_window(): the sentences with |b − θ| ≤ DRILL_WINDOW, widened until DRILL_MIN candidates.
Read Aloud, speaking and writing tasks: TASKS holds the real DET timings (docs/det-format.md); the prompts
come from practice/{speaking,writing}/prompts.csv; the self-rating is the mean of four 1–5 lines.
Every wrong or lacked word goes through family_of() → learn.add_my_word(family, source=<task>, note=…).
Selection (issue #13): pick() is a uniform draw of an item not shown in NO_REPEAT_DAYS; pick_weighted() the same
with the family weights of learn.priority_pool(), the missed words first.
"""
from __future__ import annotations

import csv
import random
import re
import uuid
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from itertools import zip_longest
from pathlib import Path
from typing import Iterable, Optional

from . import phon
from .bank import PRACTICE, WORD
from . import tts

SENTENCES = PRACTICE / "listen-and-type" / "sentences.csv"
SENTENCES_HEADER = ["id", "family", "subband", "sentence", "voice", "b_text", "b_adjust", "features"]   # #15 columns last
PASSAGES = PRACTICE / "read-and-complete" / "passages"
SPEAKING_PROMPTS = PRACTICE / "speaking" / "prompts.csv"
WRITING_PROMPTS = PRACTICE / "writing" / "prompts.csv"
PHOTOS = PRACTICE / "speaking" / "photos"
REL_RECORDINGS, REL_DRAFTS = "speaking/recordings", "writing/drafts"   # the `file` column: relative to practice/
RECORDINGS = PRACTICE / REL_RECORDINGS
DRAFTS = PRACTICE / REL_DRAFTS

VOICES = tts.voices()               # the engine's pool (#21): Kokoro's American voices, or edge-tts's four accents
MIN_WORDS, MAX_WORDS = 6, 14        # example length for the drills (cloze: ≥ MIN_WORDS; dictation: also ≤ MAX_WORDS)
MIN_BLANKS, MAX_BLANKS = 2, 5       # blanks per cloze item; fewer → the item is skipped, more → a window of MAX_BLANKS
PLAYS = 3                           # dictation and Listen Then Speak: how often the audio may be played
NO_REPEAT_DAYS = 7                  # an item shown in the last week is not drawn again
PASSAGE_REPEAT_DAYS = 30            # a passage read in the last 30 days is not drawn again (#17)
NEAR = 1                            # a cloze blank within this many letter edits of the answer is a spelling slip, not a miss
SEED_TRIES = 200                    # passage_seed(): seeds tried before giving up on a target
PRIORITY_SHARE = 0.5                # pick_weighted(): P(the draw comes from the priority items, weight > FRONTIER_WEIGHT)
FRONTIER_WEIGHT = 1                 # the weight of a frontier family in learn.priority_pool(); above it = priority
DRILL_WINDOW, DRILL_STEP, DRILL_MIN = 0.6, 0.3, 10   # |b − θ| ≤ 0.6, widened by 0.3 until 10 candidates (#16)
EVENT_KINDS = ("hit", "form", "hearing", "spelling", "vocabulary")   # what a heard or read word tells us (#16)
SKILL_KINDS = ("form", "hearing", "spelling")                        # the kinds that are not vocabulary evidence
CLOSE = 2                           # letter edits within which a wrong word is a near miss, not a different word
RATING_LINES = ("task", "fluency", "vocabulary", "grammar")   # the four 1–5 self-rating lines

# The task table (docs/det-format.md): seconds of preparation and answer time, the minimum (seconds spoken or
# words written), the second part of Interactive Writing, and which folder the attempt's file lands in.
TASKS: dict[str, dict] = {
    "read-and-complete": {"skill": "reading", "prep": 0, "seconds": 60, "passage_seconds": 180, "min": 0},
    "fill-in-the-blanks": {"skill": "reading", "prep": 0, "seconds": 20, "min": 0},
    "listen-and-type": {"skill": "listening", "prep": 0, "seconds": 60, "min": 0, "plays": PLAYS},
    "read-aloud": {"skill": "speaking", "prep": 0, "seconds": 20, "min": 0},
    "speak-photo": {"skill": "speaking", "prep": 20, "seconds": 90, "min": 30},
    "read-then-speak": {"skill": "speaking", "prep": 20, "seconds": 90, "min": 30},
    "listen-then-speak": {"skill": "speaking", "prep": 20, "seconds": 90, "min": 30, "plays": PLAYS},
    "write-photo": {"skill": "writing", "prep": 0, "seconds": 60, "min": 0},
    "read-then-write": {"skill": "writing", "prep": 0, "seconds": 300, "min": 50},
    "interactive-writing": {"skill": "writing", "prep": 0, "seconds": 300, "seconds2": 180, "min": 50},
}
SPEAKING = [t for t, v in TASKS.items() if v["skill"] == "speaking"]
WRITING = [t for t, v in TASKS.items() if v["skill"] == "writing"]

TOKEN = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)*")     # one word, apostrophes inside (don't → one token, not alphabetic)
SENTENCE_END = re.compile(r"[.!?]+[\"'’”)]*(?=\s|$)")
LETTERS = re.compile(r"^[A-Za-z]+$")
# WordNet examples quote Bacon, the King James Bible and Kipling; nobody should have to type `keepeth` (issue #22).
ARCHAIC = re.compile(r"\b(?:thou|thee|thy|thine|hath|doth|hast|dost|shalt|wilt|ere|whilst|unto|oft|nay|yea|'tis|'twas|o'er|e'er|"
                     r"whence|thither|hither|betwixt|(?!twentieth|thirtieth|fortieth|fiftieth|sixtieth|seventieth|eightieth|ninetieth|hundredth)[a-z]{3,}eth)\b", re.IGNORECASE)


# ---- Read and Complete ------------------------------------------------------------------------------------

def archaic(text: str) -> bool:
    """True for an example with an archaic form — skipped by every drill that draws sentences."""
    return ARCHAIC.search(text) is not None


def words(text: str) -> list[str]:
    return [m.group() for m in TOKEN.finditer(text)]


def word_count(text: str) -> int:
    return len(words(text))


def _sentence_start(text: str, pos: int) -> bool:
    """True when the word at `pos` opens a sentence: nothing but space or quotes since the last `.`, `!`, `?`."""
    before = text[:pos].rstrip().rstrip("\"'‘“(")
    return not before or before[-1] in ".!?"


def eligible(text: str, keep_first_sentence: bool = False) -> list[tuple[int, int]]:
    """(start, end) of every word the damage rule may touch: letters only, 3+ of them, not a capitalised word
    inside a sentence (a name), and — passage mode — not in the first sentence."""
    m = SENTENCE_END.search(text) if keep_first_sentence else None
    first_end = m.end() if m else 0
    out = []
    for m in TOKEN.finditer(text):
        w = m.group()
        if m.start() < first_end or not LETTERS.match(w) or len(w) < 3:
            continue
        if w[0].isupper() and not _sentence_start(text, m.start()):
            continue
        out.append((m.start(), m.end()))
    return out


def damage(word: str) -> str:
    """Keep the first `len // 2` letters (at least one): `skipped → ski____`, `the → t__`."""
    keep = max(1, len(word) // 2)
    return word[:keep] + "_" * (len(word) - keep)


def forms_pattern(family: str, members: Iterable[str]) -> re.Pattern:
    forms = sorted({w for w in [family, *members] if w}, key=len, reverse=True)
    return re.compile(r"^(?:" + "|".join(re.escape(w) for w in forms) + r")$", re.IGNORECASE)


def cloze(text: str, seed: int, target: Optional[re.Pattern] = None, passage: bool = False) -> Optional[dict]:
    """The C-test item, or None when the text yields no item (target not eligible, fewer than MIN_BLANKS blanks).
    `pieces` alternates plain text and blanks (`{"keep": "ski", "missing": 4}`), `answers` the damaged words in
    order. Sentence mode (`target` = forms_pattern() of the family) damages every second eligible word with the
    parity that hits the first target form; passage mode keeps the first sentence and damages the 2nd, 4th, …
    eligible word after it. More than MAX_BLANKS damaged words → a window of MAX_BLANKS consecutive ones that
    contains the target, its offset drawn from `seed`; the same (text, seed) always gives the same item."""
    spans = eligible(text, keep_first_sentence=passage)
    if target is not None:
        hit = next((i for i, (a, b) in enumerate(spans) if target.match(text[a:b])), None)
        if hit is None:
            return None
        parity = hit % 2
    else:
        hit, parity = None, 1
    picked = [s for i, s in enumerate(spans) if i % 2 == parity]
    if len(picked) < MIN_BLANKS:
        return None
    if len(picked) > MAX_BLANKS:
        t = picked.index(spans[hit]) if hit is not None else None
        lo = 0 if t is None else max(0, t - MAX_BLANKS + 1)
        hi = len(picked) - MAX_BLANKS if t is None else min(t, len(picked) - MAX_BLANKS)
        start = random.Random(seed).randint(lo, hi)
        picked = picked[start:start + MAX_BLANKS]
    pieces: list = []
    answers, pos = [], 0
    for a, b in picked:
        w, keep = text[a:b], max(1, (b - a) // 2)
        pieces.append(text[pos:a])
        pieces.append({"keep": w[:keep], "missing": len(w) - keep})
        answers.append(w)
        pos = b
    pieces.append(text[pos:])
    return {"pieces": [p for p in pieces if p != ""], "answers": answers, "blanks": len(answers),
            "damaged": "".join(p if isinstance(p, str) else p["keep"] + "_" * p["missing"] for p in pieces)}


def score_cloze(answers: list[str], typed: list[str]) -> tuple[float, list[tuple[str, str]]]:
    """(correct / blanks, [(expected, typed)] for the wrong ones). Letters only, case-insensitive, exact match."""
    wrong = []
    for want, got in zip_longest(answers, typed, fillvalue=""):
        got = re.sub(r"[^A-Za-z]", "", got or "")
        if got.lower() != want.lower():
            wrong.append((want, got))
    return round((len(answers) - len(wrong)) / len(answers), 4) if answers else 0.0, wrong


def cloze_id(family: str, sense: str | int, seed: int) -> str:
    return f"{family}.{sense}.{seed}"


def passage_id(slug: str, seed: int) -> str:
    return f"{slug}.{seed}"


def parse_cloze_id(item: str, index: dict[str, dict]) -> tuple[str, Optional[int], int]:
    """`skip.1.42` → ("skip", 1, 42); `<slug>.42` → (slug, None, 42). Families have no dots (bank.WORD), a slug
    may — the sentence shape is tried first and must name a family in `index`."""
    parts = item.rsplit(".", 2)
    if len(parts) == 3 and parts[0] in index and parts[1].isdigit() and parts[2].isdigit():
        return parts[0], int(parts[1]), int(parts[2])
    slug, _, seed = item.rpartition(".")
    if not slug or not seed.isdigit():
        raise ValueError(f"bad item id {item!r}")
    return slug, None, int(seed)


def passage_targets(text: str, families, lex) -> list[tuple[str, str]]:
    """The (family, word) pairs a passage can test in passage mode: the words at the damaged parity (the 2nd,
    4th, … eligible word after the first sentence) whose family (`lex.family`, a textdiff.Lexicon) is one of
    `families` — one entry per family, first occurrence, in text order. A priority family found here can be put
    inside the 5-blank window by passage_seed() without changing the passage rule."""
    out, seen = [], set()
    for a, b in eligible(text, keep_first_sentence=True)[1::2]:
        w = text[a:b]
        family = lex.family(w)
        if family is not None and family in families and family not in seen:
            seen.add(family)
            out.append((family, w))
    return out


def passage_seed(text: str, target: re.Pattern, rng: random.Random, tries: int = SEED_TRIES) -> Optional[int]:
    """A seed whose passage-mode window holds a form of the target (forms_pattern()): seeds are tried from a
    random start until one fits, so the item id `<slug>.<seed>` replays the same item; None when none fits."""
    base = rng.randrange(10_000)
    for i in range(tries):
        seed = (base + i) % 10_000
        item = cloze(text, seed, passage=True)
        if item is None:
            return None
        if any(target.match(a) for a in item["answers"]):
            return seed
    return None


def blank_kind(want: str, got: str) -> str:
    """What one blank says: `hit` (letters only, case-insensitive, exact), `spelling` (within NEAR letter edits of
    the answer — the word was known, a letter was not), else `vocabulary`."""
    got = re.sub(r"[^A-Za-z]", "", got or "").lower()
    if got == want.lower():
        return "hit"
    return "spelling" if got and levenshtein(want.lower(), got) <= NEAR else "vocabulary"


def cloze_events(answers: list[str], typed: list[str], lex) -> list[dict]:
    """One event per content-word blank (a family of `lex` that is not a function word and that the test can
    show — bank.WORD, so `the` and `and` blanks are scored but never word evidence): blank_kind() of the pair.
    [{family, kind, word, typed}] in blank order; the same family twice → two events."""
    out = []
    for want, got in zip_longest(answers, typed, fillvalue=""):
        if not want:
            break
        family = lex.family(want)
        if family is None or family in lex.function or not WORD.match(family):
            continue
        out.append({"family": family, "kind": blank_kind(want, got), "word": want, "typed": got or ""})
    return out


def fill_keep(word: str) -> int:
    """Letters shown in a Fill in the Blanks item: ceil(len / 3) — `tenant` → 2 (`te____`), `tenants` → 3."""
    return -(-len(word) // 3)


def fill_blank(sentence: str, target: re.Pattern) -> Optional[dict]:
    """The Fill in the Blanks item: the first word of `sentence` matching `target` (forms_pattern() of the
    family; letters only, LETTERS) is removed but for its first fill_keep() letters. `pieces` has the text
    before, `{"keep", "missing"}` and the text after, like cloze(); `answer` the word. None when no form of the
    family stands in the sentence as a plain word."""
    for m in TOKEN.finditer(sentence):
        w = m.group()
        if LETTERS.match(w) and target.match(w):
            keep = fill_keep(w)
            pieces = [p for p in (sentence[:m.start()], {"keep": w[:keep], "missing": len(w) - keep}, sentence[m.end():]) if p != ""]
            return {"pieces": pieces, "answer": w, "keep": keep,
                    "damaged": sentence[:m.start()] + w[:keep] + "_" * (len(w) - keep) + sentence[m.end():]}
    return None


def fill_id(family: str, sense: str | int) -> str:
    return f"{family}.{sense}.fb"


def parse_fill_id(item: str) -> str:
    """`tenant.1.fb` → the sentence id `tenant.1`."""
    sid, sep, tail = item.rpartition(".")
    if not sep or tail != "fb" or not sid:
        raise ValueError(f"bad item id {item!r}")
    return sid


def sentence_of(text: str, word: str) -> str:
    """The sentence of `text` holding `word` (whole word, first occurrence) — the my-words note of a passage
    blank; the whole text when the word is not found."""
    m = re.search(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", text)
    if m is None:
        return text
    starts = [0] + [e.end() for e in SENTENCE_END.finditer(text)]
    start = max(s for s in starts if s <= m.start())
    end = next((e.end() for e in SENTENCE_END.finditer(text) if e.end() > m.start()), len(text))
    return text[start:end].strip()


# ---- the sentence bank (cloze candidates, dictation, Read Aloud) ----------------------------------------------

def members(row: dict) -> list[str]:
    return [m for m in row.get("members", "").split("|") if m]


def sentence_candidates(senses: Iterable[dict], index: dict[str, dict], families: Optional[set[str]] = None,
                        longest: Optional[int] = None) -> list[dict]:
    """senses.csv rows (`family, sense, example, subband` added) whose example holds a form of the family and has
    MIN_WORDS words or more (≤ `longest` when given) and no archaic form; `families` narrows the pool. In
    senses.csv order."""
    out, pats = [], {}
    for r in senses:
        f, ex = r["family"], r["example"]
        if not ex or f not in index or (families is not None and f not in families) or archaic(ex):
            continue
        n = word_count(ex)
        if n < MIN_WORDS or (longest is not None and n > longest):
            continue
        pat = pats.get(f) or pats.setdefault(f, forms_pattern(f, members(index[f])))
        if any(pat.match(w) for w in words(ex)):
            out.append({"family": f, "sense": r["sense"], "example": ex, "subband": index[f]["subband"]})
    return out


def build_sentences(senses: Iterable[dict], index: dict[str, dict], rng: Optional[random.Random] = None) -> list[dict]:
    """The dictation bank: one row per family — the lowest sense whose example passes the 6–14-word filter —
    with a random voice (local to the machine that built the cache). `id` = `<family>.<sense>`."""
    rng = rng or random.Random()
    rows, seen = [], set()
    for c in sorted(sentence_candidates(senses, index, longest=MAX_WORDS), key=lambda c: (c["family"], int(c["sense"]))):
        if c["family"] in seen:
            continue
        seen.add(c["family"])
        rows.append({"id": f"{c['family']}.{c['sense']}", "family": c["family"], "subband": c["subband"],
                     "sentence": c["example"], "voice": rng.choice(VOICES)})
    return sorted(rows, key=lambda r: (index[r["family"]]["subband"], int(index[r["family"]]["rank"])))


def write_sentences(rows: list[dict], path: Optional[Path] = None) -> Path:
    """SENTENCES_HEADER columns; a row without b_text / b_adjust / features gets blanks (textdiff.score_rows()
    fills them)."""
    path = path or SENTENCES
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, SENTENCES_HEADER, lineterminator="\n", restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def load_sentences(path: Optional[Path] = None) -> list[dict]:
    """The cached bank, or [] when the file is missing or was written with another header (before #15) — the
    caller rebuilds it. Archaic rows of a bank built before the filter are dropped on the way in."""
    path = path or SENTENCES
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return [r for r in rows if not archaic(r["sentence"])] if reader.fieldnames == SENTENCES_HEADER else []


# ---- Listen and Type ---------------------------------------------------------------------------------------

def normalise(text: str) -> list[str]:
    """Lower-case words, punctuation stripped, apostrophes inside a word kept (`don't`)."""
    return [w.lower().replace("’", "'") for w in TOKEN.findall(text)]


def levenshtein(a: str, b: str) -> int:
    """Character-level edit distance (insert, delete, substitute)."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def credit(reference: str, typed: str) -> float:
    """The DET's partial credit for a dictation: 1 − d / max(len(ref), len(typed)) on the normalised strings
    (lower-case, punctuation stripped, whitespace collapsed), `d` = character-level Levenshtein. Identical → 1,
    one letter off in 40 characters → 0.975, nothing typed → 0."""
    ref, got = " ".join(normalise(reference)), " ".join(normalise(typed))
    longest = max(len(ref), len(got))
    return round(1 - levenshtein(ref, got) / longest, 4) if longest else 0.0


def dictation_score(reference: str, typed: str) -> dict:
    """`score` = credit() (the attempts.csv score since #16); `word_score` = the old word-level measure,
    max(0, 1 − errors / reference words) over difflib opcodes (delete = missing reference words, insert = extra
    typed words, replace = the longer span); `errors` = [(expected, typed)] pairs, blank on one side for a missing
    or extra word; `wrong` = the reference words in delete and replace spans; `diff` = the opcodes with their
    words, for the result screen and dictation_events()."""
    ref, got = normalise(reference), normalise(typed)
    errors, wrong, diff, n = [], [], [], 0
    for op, i1, i2, j1, j2 in SequenceMatcher(None, ref, got, autojunk=False).get_opcodes():
        diff.append({"op": op, "ref": ref[i1:i2], "typed": got[j1:j2]})
        if op == "equal":
            continue
        if op == "delete":
            n += i2 - i1
        elif op == "insert":
            n += j2 - j1
        else:
            n += max(i2 - i1, j2 - j1)
        errors += list(zip_longest(ref[i1:i2], got[j1:j2], fillvalue=""))
        wrong += ref[i1:i2]
    word_score = max(0.0, 1 - n / len(ref)) if ref else 0.0
    return {"score": credit(reference, typed), "word_score": round(word_score, 4), "errors": errors, "wrong": wrong,
            "words": len(ref), "diff": diff}


def error_kind(ref: str, typed: str, family: str, lex) -> str:
    """What a wrong content word says (a `replace` pair): `form` — the typed word is another member of the same
    family (`evicts` for `evict`); `hearing` — the typed word is a *different word of the bank* that sounds like
    the reference (same Metaphone key, `ward` for `word`) or lies within CLOSE letter edits of it (`went` for
    `rent`): a real word was heard in place of the right one; `spelling` — a form the bank does not know with the
    reference's sound (`tennant`, `tenent` for `tenant`) or within CLOSE edits: the right word, the wrong letters;
    `vocabulary` — anything else (`avoid` for `evict`). Metaphone alone cannot split hearing from spelling
    (`ward`/`word` and `tennant`/`tenant` share one key each), hence the bank lookup."""
    other = lex.family(typed)
    if other == family:
        return "form"
    if phon.sounds_alike(ref, typed) or levenshtein(ref, typed) <= CLOSE:
        return "hearing" if other is not None else "spelling"
    return "vocabulary"


def dictation_events(diff: list[dict], theta: float, lex) -> list[dict]:
    """One event per content word of the reference (a word whose family is not a function word of `lex`, a
    textdiff.Lexicon, and is one the test can show — bank.WORD, so `do` and `be` stay out): `equal` → hit; `replace` → error_kind() of the pair (a reference word the typed span is
    too short to pair with counts as missing); missing (`delete`) → `vocabulary` when the word's b ≥ θ − 1 (a word
    the learner is not expected to know), `hearing` below it (an easy word that was not caught); an extra typed
    word is not an event. [{family, kind, word, typed}] in reference order."""
    out = []
    for d in diff:
        if d["op"] == "insert":
            continue
        for ref, got in zip_longest(d["ref"], d["typed"] if d["op"] == "replace" else [], fillvalue=""):
            if not ref:
                break
            family = lex.family(ref)
            if family is None or family in lex.function or not WORD.match(family):
                continue
            if d["op"] == "equal":
                kind = "hit"
            elif got:
                kind = error_kind(ref, got, family, lex)
            else:
                kind = "vocabulary" if lex.b[family] >= theta - 1 else "hearing"
            out.append({"family": family, "kind": kind, "word": ref, "typed": got})
    return out


def events_column(events: Iterable[dict]) -> str:
    """`family:kind|family:kind` — the attempts.csv `events` column."""
    return "|".join(f"{e['family']}:{e['kind']}" for e in events)


def parse_events(s: str) -> list[tuple[str, str]]:
    """The column back to [(family, kind)]; blank (rows from before #16) → []."""
    out = []
    for part in (s or "").split("|"):
        family, sep, kind = part.partition(":")
        if sep and kind in EVENT_KINDS:
            out.append((family, kind))
    return out


def in_window(rows: list[dict], theta: float, b_of, width: float = DRILL_WINDOW, step: float = DRILL_STEP,
              least: int = DRILL_MIN) -> tuple[list[dict], float]:
    """The rows with |b_of(row) − θ| ≤ width, the window widened by `step` until it holds `least` rows or the
    whole list. Returns (rows, the width used)."""
    span = max(abs(b_of(r) - theta) for r in rows) if rows else 0.0
    while True:
        pool = [r for r in rows if abs(b_of(r) - theta) <= width]
        if len(pool) >= least or width >= span:
            return pool, width
        width = round(width + step, 4)


# ---- speaking and writing ----------------------------------------------------------------------------------

def load_prompts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_passages(folder: Optional[Path] = None) -> list[dict]:
    """practice/read-and-complete/passages/*.md → [{slug, text, source, ..., b_text, b_adjust}]: an optional
    front-matter block (`---`, `key: value` lines, `---`) then the passage; the slug is the file name. `b_text`
    (a float, None until `scripts/passages.py score` wrote it) and `b_adjust` (float, 0 by default) are the
    #15 difficulty; every other key stays a string."""
    folder = folder or PASSAGES
    out = []
    for p in sorted(folder.glob("*.md")) if folder.exists() else []:
        meta, body = {}, p.read_text(encoding="utf-8")
        if body.startswith("---\n"):
            head, _, body = body[4:].partition("\n---\n")
            for line in head.splitlines():
                k, _, v = line.partition(":")
                if _:
                    meta[k.strip()] = v.strip()
        meta["b_text"] = float(meta["b_text"]) if meta.get("b_text") else None
        meta["b_adjust"] = float(meta["b_adjust"]) if meta.get("b_adjust") else 0.0
        out.append({**meta, "slug": p.stem, "text": " ".join(body.split())})
    return out


def self_rating(lines: Iterable[int]) -> int:
    """One number from the four 1–5 lines: the mean, rounded half up (2.5 → 3)."""
    vals = [int(v) for v in lines]
    if not vals or any(v < 1 or v > 5 for v in vals):
        raise ValueError("each rating line is 1–5")
    return (2 * sum(vals) + len(vals)) // (2 * len(vals))


def draft_text(task: str, prompt_id: str, seconds: int, n_words: int, parts: list[tuple[str, str]]) -> str:
    """practice/writing/drafts/<attempt>.md: front matter, then each part under its prompt."""
    md = ["---", f"task: {task}", f"prompt: {prompt_id}", f"seconds: {seconds}", f"words: {n_words}", "---", ""]
    for heading, text in parts:
        md += [f"## {heading}", "", text.strip(), ""]
    return "\n".join(md)


# ---- wrong word → family, the attempt log -------------------------------------------------------------------

def form_index(index: dict[str, dict]) -> dict[str, str]:
    """Every headword and member (lower-case) → family; a form shared by two families keeps the first by rank."""
    out: dict[str, str] = {}
    for r in sorted(index.values(), key=lambda r: int(r["rank"])):
        for w in [r["family"], *members(r)]:
            out.setdefault(w.lower(), r["family"])
    return out


def family_of(word: str, forms: dict[str, str]) -> Optional[str]:
    """The family a wrong or lacked word belongs to, or None: a form outside index.csv, or a family the app never
    shows (bank.WORD: `be`, `a`, `ms`) — so a blank dictation answer does not push `is` and `to` onto the study list."""
    family = forms.get(word.strip().lower().replace("’", "'"))
    return family if family and WORD.match(family) else None


def lacked_words(text: str) -> list[str]:
    """The "words I lacked" box: comma- or newline-separated, trimmed, lower-case, no blanks or repeats."""
    seen, out = set(), []
    for w in re.split(r"[,;\n]+", text or ""):
        w = " ".join(w.split()).lower()
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def attempt_id(now: Optional[datetime] = None) -> str:
    """`<date>_<HHMMSS>_<4 hex>` — the shape of the test session id."""
    return (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]


def errors_column(pairs: Iterable[tuple[str, str]]) -> str:
    """`expected>typed;expected>typed` — `;`, `>` and newlines inside a word are dropped so the column parses back."""
    clean = lambda s: re.sub(r"[;>\s]+", " ", s).strip()                                       # noqa: E731
    return ";".join(f"{clean(a)}>{clean(b)}" for a, b in pairs)


def recent_items(attempts: Iterable[dict], task: str, today: Optional[date] = None, days: int = NO_REPEAT_DAYS) -> set[str]:
    """Item ids of `task` attempted in the last `days` days, with the cloze seed stripped (`skip.1.42` → `skip.1`,
    `honey.42` → `honey`) so a sentence or passage is not shown twice with different windows, and the `.fb` of
    a Fill in the Blanks id (`tenant.1.fb` → `tenant.1`, the sentence)."""
    since = (today or date.today()) - timedelta(days=days)
    out = set()
    for r in attempts:
        if r["task"] == task and r["date"] >= since.isoformat():
            item = r["item"]
            if task in ("read-and-complete", "fill-in-the-blanks"):
                item = item.rpartition(".")[0]
            out.add(item)
    return out


def pick(pool: list, recent: set[str], key, rng: random.Random):
    """A random item whose key is not in `recent`; any item once everything was shown this week; None when empty."""
    fresh = [p for p in pool if key(p) not in recent]
    return rng.choice(fresh or pool) if pool else None


def pick_weighted(pool: list, recent: set[str], key, weight, rng: random.Random, share: Optional[float] = None):
    """random.choices over the items not in `recent` with `weight(item)` (issue #13); items of weight 0 are
    dropped. When the fresh items include a priority one (weight > FRONTIER_WEIGHT), the draw comes from those
    with probability `share`, else from the weighted union — a small pool of missed words is neither drowned by
    500 frontier words nor allowed to drown them. Every weighted item shown this week → pick() over all of them;
    nothing with a weight → None."""
    weighted = [(p, weight(p)) for p in pool]
    weighted = [(p, w) for p, w in weighted if w > 0]
    if not weighted:
        return None
    fresh = [(p, w) for p, w in weighted if key(p) not in recent]
    if not fresh:
        return pick([p for p, _ in weighted], recent, key, rng)
    priority = [(p, w) for p, w in fresh if w > FRONTIER_WEIGHT]
    draw = priority if priority and rng.random() < (PRIORITY_SHARE if share is None else share) else fresh
    return rng.choices([p for p, _ in draw], [w for _, w in draw])[0]


def today_counts(attempts: Iterable[dict], today: Optional[date] = None) -> dict[str, int]:
    """Attempts per task today — the start page's routine line."""
    day = (today or date.today()).isoformat()
    counts = {t: 0 for t in TASKS}
    for r in attempts:
        if r["date"] == day and r["task"] in counts:
            counts[r["task"]] += 1
    return counts
