#!/usr/bin/env python3
"""The Read and Complete passage bank, practice/read-and-complete/passages/*.md (GitHub issue nhanpc/DET#15 for the
scoring, #17 for `fetch` and the 150-passage bank).

    python3 scripts/passages.py fetch --n 200 --min 50 --max 80   # Simple English Wikipedia intros → passages/incoming/
    python3 scripts/passages.py score            # b_text, b_adjust (kept, default 0) and features into every front matter
    python3 scripts/passages.py score --print    # show the numbers, write nothing
    python3 scripts/passages.py check            # exit 1 when a passage lacks source, licence or a current b_text,
                                                 # or yields fewer than MIN_PASSAGE_BLANKS C-test blanks

fetch  searches Simple English Wikipedia (the MediaWiki API, `action=query&generator=search&prop=extracts&exintro`)
       for every term of scripts/topics.txt and keeps the opening sentences of an article's intro when they make a
       passage of --min … --max words (the DET's 50–80) in ≥ MIN_SENTENCES whole sentences, with no list, table,
       bracket artifact or pronunciation guide, at most MAX_OFF off-list content words (app/textdiff.py) and at
       least MIN_PASSAGE_BLANKS blanks by the C-test rule. Each kept passage is one file in passages/incoming/
       (gitignored) with `source:` (the article URL), `licence:` (CC BY-SA 4.0 — the text is a verbatim excerpt),
       `fetched:` (the date) and the #15 numbers already scored; a rejected article is named on stderr with the
       reason. Review by hand, then `git mv` the good ones into practice/read-and-complete/passages/.
b_text is app/textdiff.py's prediction from the words, length and coverage of the passage (docs/sentences.md § How
difficulty is computed); `check` recomputes it, so a passage edited after scoring fails until `score` runs again.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import drills, textdiff                                   # noqa: E402  (after the sys.path line)
from app.bank import Bank                                          # noqa: E402

MIN_PASSAGE_BLANKS = 6            # a passage must yield this many damaged words in passage mode (#17's rule)
MIN_SENTENCES = 4                 # fetch: a passage is at least this many whole sentences
MAX_OFF = 2                       # fetch: off-list content words allowed (textdiff.features()["off"])
MIN_WORDS, MAX_WORDS = 50, 80     # fetch defaults: the DET's passage length
PER_TOPIC = 10                    # fetch: search hits per topics.txt term
API = "https://simple.wikipedia.org/w/api.php"
ARTICLE = "https://simple.wikipedia.org/wiki/"
LICENCE = "CC BY-SA 4.0"
USER_AGENT = "DET-passages/1.0 (https://github.com/nhanpc/DET; a personal study repo)"
TOPICS = Path(__file__).resolve().parent / "topics.txt"
INCOMING = "incoming"

# a parenthesis holding a pronunciation guide (IPA, `pronounced …`) or nothing useful, dropped before the checks
PRONUNCIATION = re.compile(r"\s*\((?:[^()]*(?:/|ˈ|ˌ|ː|\bpronounced\b|\bpronunciation\b|\bIPA\b|\blisten\b)[^()]*|\s*)\)")
ARTIFACT = re.compile(r"[\[\]{}|<>*=#_\\]")                              # wiki markup that survived the plain extract
# letters the passages may use: ASCII, typographic quotes and dashes, Latin-1 and Latin Extended-A letters (names)
ALLOWED = re.compile(r"^[\x20-\x7e\u2018\u2019\u201c\u201d\u2013\u2014\u2026\u00a0\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u017f]*$")
MAX_UNSCORED = 0.25               # fetch: share of tokens that are names or numbers (a biography, a date list) allowed
# titles that are not the DET's everyday-science-history-places mix: disambiguated entries (`Bread (band)`), lists,
# songs, albums, films, games, sports fixtures, companies' products
SKIP_TITLE = re.compile(r"\(.*\)|^Lists? of |^Glossary |^Timeline |\b(song|album|band|movie|film|series|episode|game|"
                        r"wrestling|wwe|championship|tour|awards?|inc\.?|ltd\.?|corporation|company|studios?|records|hero)\b|\b(19|20)\d\d\b",
                        re.IGNORECASE)


def passage_blanks(text: str) -> int:
    """How many words the C-test rule damages in passage mode before the MAX_BLANKS window: the 2nd, 4th, …
    eligible word after the first sentence."""
    return len(drills.eligible(text, keep_first_sentence=True)[1::2])


# ---- fetch --------------------------------------------------------------------------------------------------

def read_topics(path: Path = TOPICS) -> list[str]:
    """The search terms: one per line, blank lines and `#` comments skipped, repeats dropped."""
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        term = line.split("#", 1)[0].strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            out.append(term)
    return out


def api_get(params: dict) -> dict:
    """One MediaWiki API call (JSON); replaced in the tests."""
    query = urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def search_intros(term: str, limit: int = PER_TOPIC, get: Callable[[dict], dict] = api_get) -> list[dict]:
    """The intro extracts of the articles a search for `term` finds: [{title, extract}], best match first."""
    d = get({"action": "query", "generator": "search", "gsrsearch": term, "gsrnamespace": 0, "gsrlimit": limit,
             "prop": "extracts", "exintro": 1, "explaintext": 1, "exlimit": limit, "exsectionformat": "plain"})
    pages = sorted(d.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
    return [{"title": p["title"], "extract": p.get("extract", "")} for p in pages if p.get("extract")]


def slugify(title: str) -> str:
    """`Great Wall of China` → `great-wall-of-china`; accents stripped, anything but letters and digits → `-`."""
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")


def clean(text: str) -> str:
    """Pronunciation guides and empty parentheses out, whitespace and typographic apostrophes normalised."""
    text = PRONUNCIATION.sub("", text.replace("\u00a0", " "))
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)                            # `word , word` after a dropped guide
    return " ".join(text.split())


def sentences(paragraph: str) -> list[str]:
    """Whole sentences of one paragraph (drills.SENTENCE_END), each trimmed; a tail without a stop is dropped."""
    out, pos = [], 0
    for m in drills.SENTENCE_END.finditer(paragraph):
        s = paragraph[pos:m.end()].strip()
        if s:
            out.append(s)
        pos = m.end()
    return out


def excerpt(extract: str, lo: int = MIN_WORDS, hi: int = MAX_WORDS, least: int = MIN_SENTENCES) -> tuple[Optional[str], str]:
    """(passage, reason): the longest run of opening sentences of the intro with lo … hi words and ≥ `least`
    sentences, or (None, why not). Paragraphs are taken in order; one that ends with a colon introduces a list,
    so the intake stops after its last whole sentence."""
    taken: list[str] = []
    for para in (p.strip() for p in extract.split("\n")):
        if not para:
            continue
        taken += sentences(clean(para))
        if para.endswith(":"):
            break
    if not taken:
        return None, "no whole sentence"
    best, n = None, 0
    for i in range(1, len(taken) + 1):
        n = drills.word_count(" ".join(taken[:i]))
        if n > hi:
            break
        if n >= lo and i >= least:
            best = " ".join(taken[:i])
    if best is None:
        total = drills.word_count(" ".join(taken))
        if total < lo:
            return None, f"too short: {total} words in the intro"
        if n > hi and len(taken) < least:
            return None, f"fewer than {least} sentences within {hi} words"
        return None, f"no run of whole sentences within {lo}–{hi} words"
    return best, ""


def reject(text: str, lex: textdiff.Lexicon, max_off: int = MAX_OFF, min_blanks: int = MIN_PASSAGE_BLANKS) -> str:
    """The filters after the length: "" when the passage passes, else the reason."""
    if ARTIFACT.search(text):
        return f"markup artifact: {ARTIFACT.search(text).group()!r}"
    if not ALLOWED.match(text):
        odd = next(ch for ch in text if not ALLOWED.match(ch))
        return f"unusual character {odd!r} (a pronunciation guide or a non-Latin name)"
    if "(" in text and text.count("(") != text.count(")"):
        return "unbalanced parenthesis"
    short = next((s for s in sentences(text) if drills.word_count(s) < 3), None)
    if short is not None:
        return f"abbreviation or fragment: {short!r}"
    f = textdiff.features(text, lex)
    if f["length"] and 1 - f["n"] / f["length"] > MAX_UNSCORED:
        return f"{f['length'] - f['n']} of {f['length']} tokens are names or numbers (a biography or a date list)"
    if f["off"] > max_off:
        return f"{f['off']} off-list content words (at most {max_off})"
    n = passage_blanks(text)
    if n < min_blanks:
        return f"{n} blanks by the C-test rule, fewer than {min_blanks}"
    return ""


def write_passage(path: Path, text: str, source: str, lex: textdiff.Lexicon, fetched: Optional[date] = None) -> dict:
    """The file: source, licence, fetched in the front matter, the passage as one paragraph, then scored in place
    (b_text, b_adjust 0, features) so the incoming folder can be reviewed by difficulty."""
    day = (fetched or date.today()).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nsource: {source}\nlicence: {LICENCE}\nfetched: {day}\n---\n{text}\n", encoding="utf-8")
    meta, _ = textdiff.score_passage(path, lex)
    return meta


def fetch(folder: Path, out: Path, lex: textdiff.Lexicon, n: int, lo: int, hi: int, topics: Iterable[str],
          per_topic: int = PER_TOPIC, get: Callable[[dict], dict] = api_get, log=sys.stderr, fetched: Optional[date] = None) -> int:
    """Search every topic, keep up to `n` passages in `out`; returns how many were written. A title already in
    `folder` or `out` (by slug) is skipped, every rejected article is one line on `log`."""
    have = {p.stem for p in folder.glob("*.md")} | {p.stem for p in out.glob("*.md")}
    written = 0
    for term in topics:
        if written >= n:
            break
        try:
            hits = search_intros(term, per_topic, get)
        except Exception as e:                                             # one failed call is not the end of the run
            print(f"{term}: search failed ({e.__class__.__name__}: {e})", file=log)
            continue
        for hit in hits:
            if written >= n:
                break
            title, slug = hit["title"], slugify(hit["title"])
            if SKIP_TITLE.search(title) or not slug:
                print(f"{title}: skipped by title", file=log)
                continue
            if slug in have:
                continue
            text, why = excerpt(hit["extract"], lo, hi)
            why = why or reject(text, lex)
            if why:
                print(f"{title}: {why}", file=log)
                continue
            meta = write_passage(out / f"{slug}.md", text, ARTICLE + urllib.parse.quote(title.replace(" ", "_")), lex, fetched)
            have.add(slug)
            written += 1
            print(f"{slug:36} b_text {float(meta['b_text']):6.2f}  {drills.word_count(text):3} words  ← {term}")
    return written


# ---- score, check -------------------------------------------------------------------------------------------

def score(folder: Path, lex: textdiff.Lexicon, dry: bool) -> int:
    for path in sorted(folder.glob("*.md")):
        meta, body = textdiff.passage_front_matter(path)
        f = textdiff.features(" ".join(body.split()), lex)
        b = textdiff.combine(f)
        if dry:
            print(f"{path.stem:24} b_text {b:6.2f}  {textdiff.features_column(f)}")
            continue
        _, changed = textdiff.score_passage(path, lex)
        print(f"{path.stem:24} b_text {b:6.2f}  {'written' if changed else 'unchanged'}")
    return 0


def check(folder: Path, lex: textdiff.Lexicon) -> int:
    problems = []
    passages = drills.load_passages(folder)
    for p in passages:
        rel = f"{folder.relative_to(ROOT) if folder.is_relative_to(ROOT) else folder}/{p['slug']}.md"
        for key in ("source", "licence"):
            if not p.get(key):
                problems.append(f"{rel}: no {key}: in the front matter")
        if p["b_text"] is None:
            problems.append(f"{rel}: no b_text: — run `scripts/passages.py score`")
        elif abs(p["b_text"] - textdiff.b_text(p["text"], lex)) > 1e-6:
            problems.append(f"{rel}: b_text {p['b_text']} is stale (now {textdiff.b_text(p['text'], lex)}) — run `scripts/passages.py score`")
        n = passage_blanks(p["text"])
        if n < MIN_PASSAGE_BLANKS:
            problems.append(f"{rel}: {n} blanks by the C-test rule, fewer than {MIN_PASSAGE_BLANKS}")
    if not passages:
        problems.append(f"{folder}: no passage")
    print("\n".join(problems) if problems else f"{len(passages)} passages: source, licence, b_text and ≥ {MIN_PASSAGE_BLANKS} blanks", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["fetch", "score", "check"])
    ap.add_argument("--folder", type=Path, default=drills.PASSAGES, help="passage folder (default practice/read-and-complete/passages)")
    ap.add_argument("--print", action="store_true", help="score: show the numbers, write nothing")
    ap.add_argument("--n", type=int, default=200, help="fetch: how many passages to write")
    ap.add_argument("--min", type=int, default=MIN_WORDS, help="fetch: shortest passage, words")
    ap.add_argument("--max", type=int, default=MAX_WORDS, help="fetch: longest passage, words")
    ap.add_argument("--per-topic", type=int, default=PER_TOPIC, help="fetch: search hits per topic")
    ap.add_argument("--topics", type=Path, default=TOPICS, help="fetch: the search terms (default scripts/topics.txt)")
    ap.add_argument("--out", type=Path, default=None, help="fetch: where the files go (default <folder>/incoming)")
    a = ap.parse_args(argv)
    bank = Bank()
    lex = textdiff.Lexicon(bank.index, bank.b)
    if a.command == "fetch":
        out = a.out or a.folder / INCOMING
        n = fetch(a.folder, out, lex, a.n, a.min, a.max, read_topics(a.topics), a.per_topic)
        print(f"{n} passages written to {out}", file=sys.stderr)
        return 0 if n else 1
    return score(a.folder, lex, a.print) if a.command == "score" else check(a.folder, lex)


if __name__ == "__main__":
    sys.exit(main())
