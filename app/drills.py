"""DET task drills (GitHub issue nhanpc/DET#10, Phase 5). Pure functions; main.py does the I/O.

Read and Complete   cloze(): C-test damage of a senses.csv example (sentence mode) or a hand-pasted passage —
                    every second eligible word loses its second half. Items are not stored: the id encodes the
                    inputs (`<family>.<sense>.<seed>` / `<passage-slug>.<seed>`) and cloze() is deterministic.
Listen and Type     build_sentences(): the dictation bank (one 6–14-word example per family) cached in
                    practice/listen-and-type/sentences.csv; dictation_score(): word-level edit distance.
Read Aloud, speaking and writing tasks: TASKS holds the real DET timings (docs/det-format.md); the prompts
come from practice/{speaking,writing}/prompts.csv; the self-rating is the mean of four 1–5 lines.
Every wrong or lacked word goes through family_of() → learn.add_my_word(family, source=<task>, note=…).
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

from .bank import PRACTICE, WORD

SENTENCES = PRACTICE / "listen-and-type" / "sentences.csv"
SENTENCES_HEADER = ["id", "family", "subband", "sentence", "voice"]
PASSAGES = PRACTICE / "read-and-complete" / "passages"
SPEAKING_PROMPTS = PRACTICE / "speaking" / "prompts.csv"
WRITING_PROMPTS = PRACTICE / "writing" / "prompts.csv"
PHOTOS = PRACTICE / "speaking" / "photos"
REL_RECORDINGS, REL_DRAFTS = "speaking/recordings", "writing/drafts"   # the `file` column: relative to practice/
RECORDINGS = PRACTICE / REL_RECORDINGS
DRAFTS = PRACTICE / REL_DRAFTS

VOICES = ["en-US-AriaNeural", "en-GB-SoniaNeural", "en-AU-NatashaNeural", "en-IN-NeerjaNeural"]
MIN_WORDS, MAX_WORDS = 6, 14        # example length for the drills (cloze: ≥ MIN_WORDS; dictation: also ≤ MAX_WORDS)
MIN_BLANKS, MAX_BLANKS = 2, 5       # blanks per cloze item; fewer → the item is skipped, more → a window of MAX_BLANKS
PLAYS = 3                           # dictation and Listen Then Speak: how often the audio may be played
NO_REPEAT_DAYS = 7                  # an item shown in the last week is not drawn again
RATING_LINES = ("task", "fluency", "vocabulary", "grammar")   # the four 1–5 self-rating lines

# The task table (docs/det-format.md): seconds of preparation and answer time, the minimum (seconds spoken or
# words written), the second part of Interactive Writing, and which folder the attempt's file lands in.
TASKS: dict[str, dict] = {
    "read-and-complete": {"skill": "reading", "prep": 0, "seconds": 60, "passage_seconds": 180, "min": 0},
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


# ---- Read and Complete ------------------------------------------------------------------------------------

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


# ---- the sentence bank (cloze candidates, dictation, Read Aloud) ----------------------------------------------

def members(row: dict) -> list[str]:
    return [m for m in row.get("members", "").split("|") if m]


def sentence_candidates(senses: Iterable[dict], index: dict[str, dict], families: Optional[set[str]] = None,
                        longest: Optional[int] = None) -> list[dict]:
    """senses.csv rows (`family, sense, example, subband` added) whose example holds a form of the family and has
    MIN_WORDS words or more (≤ `longest` when given); `families` narrows the pool. In senses.csv order."""
    out, pats = [], {}
    for r in senses:
        f, ex = r["family"], r["example"]
        if not ex or f not in index or (families is not None and f not in families):
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
    path = path or SENTENCES
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, SENTENCES_HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def load_sentences(path: Optional[Path] = None) -> list[dict]:
    path = path or SENTENCES
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---- Listen and Type ---------------------------------------------------------------------------------------

def normalise(text: str) -> list[str]:
    """Lower-case words, punctuation stripped, apostrophes inside a word kept (`don't`)."""
    return [w.lower().replace("’", "'") for w in TOKEN.findall(text)]


def dictation_score(reference: str, typed: str) -> dict:
    """Word-level edit distance with equal weights (difflib opcodes: delete = missing reference words, insert =
    extra typed words, replace = the longer span). `score` = max(0, 1 − errors / reference words); `errors` =
    [(expected, typed)] pairs, blank on one side for a missing or extra word; `wrong` = the reference words in
    delete and replace spans (the ones that go to my-words); `diff` = the opcodes with their words, for display."""
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
    score = max(0.0, 1 - n / len(ref)) if ref else 0.0
    return {"score": round(score, 4), "errors": errors, "wrong": wrong, "words": len(ref), "diff": diff}


# ---- speaking and writing ----------------------------------------------------------------------------------

def load_prompts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_passages(folder: Optional[Path] = None) -> list[dict]:
    """practice/read-and-complete/passages/*.md → [{slug, text, source, ...}]: an optional front-matter block
    (`---`, `key: value` lines, `---`) then the passage; the slug is the file name."""
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
    """Item ids of `task` attempted in the last `days` days, with the cloze seed stripped (`skip.1.42` → `skip.1`)
    so a sentence is not shown twice with different windows."""
    since = (today or date.today()) - timedelta(days=days)
    out = set()
    for r in attempts:
        if r["task"] == task and r["date"] >= since.isoformat():
            item = r["item"]
            if task == "read-and-complete":
                item = item.rpartition(".")[0]
            out.add(item)
    return out


def pick(pool: list, recent: set[str], key, rng: random.Random):
    """A random item whose key is not in `recent`; any item once everything was shown this week; None when empty."""
    fresh = [p for p in pool if key(p) not in recent]
    return rng.choice(fresh or pool) if pool else None


def today_counts(attempts: Iterable[dict], today: Optional[date] = None) -> dict[str, int]:
    """Attempts per task today — the start page's routine line."""
    day = (today or date.today()).isoformat()
    counts = {t: 0 for t in TASKS}
    for r in attempts:
        if r["date"] == day and r["task"] in counts:
            counts[r["task"]] += 1
    return counts
