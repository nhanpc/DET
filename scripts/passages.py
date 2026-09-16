#!/usr/bin/env python3
"""The Read and Complete passage bank, practice/read-and-complete/passages/*.md (GitHub issue nhanpc/DET#15 for the
scoring; #17 adds `fetch` and the 150-passage bank).

    python3 scripts/passages.py score            # b_text, b_adjust (kept, default 0) and features into every front matter
    python3 scripts/passages.py score --print    # show the numbers, write nothing
    python3 scripts/passages.py check            # exit 1 when a passage lacks source, licence or a current b_text,
                                                 # or yields fewer than MIN_PASSAGE_BLANKS C-test blanks

b_text is app/textdiff.py's prediction from the words, length and coverage of the passage (docs/sentences.md § How
difficulty is computed); `check` recomputes it, so a passage edited after scoring fails until `score` runs again.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import drills, textdiff                                   # noqa: E402  (after the sys.path line)
from app.bank import Bank                                          # noqa: E402

MIN_PASSAGE_BLANKS = 6            # a passage must yield this many damaged words in passage mode (#17's rule)


def passage_blanks(text: str) -> int:
    """How many words the C-test rule damages in passage mode before the MAX_BLANKS window: the 2nd, 4th, …
    eligible word after the first sentence."""
    return len(drills.eligible(text, keep_first_sentence=True)[1::2])


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
    ap.add_argument("command", choices=["score", "check"])
    ap.add_argument("--folder", type=Path, default=drills.PASSAGES, help="passage folder (default practice/read-and-complete/passages)")
    ap.add_argument("--print", action="store_true", help="score: show the numbers, write nothing")
    a = ap.parse_args(argv)
    bank = Bank()
    lex = textdiff.Lexicon(bank.index, bank.b)
    return score(a.folder, lex, a.print) if a.command == "score" else check(a.folder, lex)


if __name__ == "__main__":
    sys.exit(main())
