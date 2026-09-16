#!/usr/bin/env python3
"""Rewrite the generated block of vocab/progress.md from the level-test history (app.progress, issue nhanpc/DET#9)
and the hand-typed mock tests in vocab/tests/mocks.csv (issue #11: chart, table, focus and the booking verdict).

    python3 scripts/report.py                       # vocab/tests/ → vocab/progress.md, prints the Now line
    python3 scripts/report.py --print               # the generated Markdown on stdout, nothing written
    python3 scripts/report.py --out PATH            # write to PATH instead (created with a stub when missing)
    python3 scripts/report.py --anki collection.anki2   # + an Anki table from a *copy* of Anki's collection
    python3 scripts/report.py --check               # exit 1 when results.csv / levels.csv disagree with the session JSON
    python3 scripts/report.py --sentences           # the b_text histogram per source band of the dictation bank (issue #15),
                                                    # the table in docs/sentences.md; nothing written

Only the text between `<!-- generated:start -->` and `<!-- generated:end -->` changes; the hand-written part
above them is never touched (the rule table is in issue #7 § 6). Run it after every test, after typing a mock
row, and at the Sunday review; then commit. A malformed mocks.csv row stops it (exit 1) with the line number.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import progress, store, textdiff                     # noqa: E402  (after the sys.path line)
from app.bank import VOCAB, Bank, load_subbands, read_csv           # noqa: E402

PROGRESS = VOCAB / "progress.md"


def sentences_table(subbands) -> str:
    """The dictation bank (built and cached when missing, like the app does) as a b_text histogram per source band."""
    bank = Bank()
    rows = textdiff.sentence_bank(read_csv(VOCAB / "senses.csv"), bank.index, textdiff.Lexicon(bank.index, bank.b))
    return textdiff.histogram_table(textdiff.histogram(rows, [sb.name for sb in subbands]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=PROGRESS, metavar="PATH", help=f"file to rewrite (default {PROGRESS.relative_to(ROOT)})")
    ap.add_argument("--print", action="store_true", help="print the generated Markdown, write nothing")
    ap.add_argument("--anki", type=Path, metavar="PATH", help="a copy of Anki's collection.anki2: adds the Anki table")
    ap.add_argument("--check", action="store_true", help="exit 1 when the CSVs disagree with the session JSON")
    ap.add_argument("--sentences", action="store_true", help="print the b_text histogram per source band of the sentence bank")
    a = ap.parse_args(argv)

    subbands = load_subbands()
    if a.sentences:
        print(sentences_table(subbands))
        return 0
    sessions, levels = store.load_sessions(), store.load_levels()
    if a.check:
        problems = progress.check(sessions, levels, store.load_results())
        print("\n".join(problems) if problems else "levels.csv and results.csv agree with the session JSON", file=sys.stderr)
        if problems:
            return 1
    try:
        report = progress.build(sessions, levels, subbands, mocks=store.load_mocks(), b_of=Bank().b)
    except ValueError as e:
        path = store.MOCKS.relative_to(ROOT) if store.MOCKS.is_relative_to(ROOT) else store.MOCKS
        print(f"{path}: {e} — nothing written", file=sys.stderr)
        return 1
    if a.anki:
        try:
            with sqlite3.connect(f"file:{a.anki}?mode=ro", uri=True) as conn:
                report["anki"] = progress.anki_stats(conn, subbands)
        except sqlite3.Error as e:
            print(f"{a.anki}: {e} — copy Anki's collection.anki2 while Anki is closed", file=sys.stderr)
            return 1
    generated = progress.render_markdown(report, subbands)
    if a.print:
        print(generated, end="")
        return 0

    text = a.out.read_text(encoding="utf-8") if a.out.exists() else progress.STUB
    try:
        out = progress.splice(text, generated)
    except ValueError as e:
        print(f"{a.out}: {e} — nothing written", file=sys.stderr)
        return 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(out, encoding="utf-8")
    rel = a.out.relative_to(ROOT) if a.out.is_absolute() and a.out.is_relative_to(ROOT) else a.out
    print(f"{progress.now_line(report)}  → {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
