#!/usr/bin/env python3
"""Export an Anki deck through the same code the Learn page uses (app.learn, GitHub issue nhanpc/DET#8).

    python3 scripts/export_anki.py 4k-a            # every 4k-a family, by rank        → vocab/decks/4k-a.txt
    python3 scripts/export_anki.py --batch 20      # what the Learn button exports     → vocab/decks/<date>.txt
    python3 scripts/export_anki.py --my-words      # open vocab/my-words.csv entries   → vocab/decks/my-words.txt
    python3 scripts/export_anki.py 4k-a --check    # no file: families with no gappable example (overrides.csv to-do)

One note per family for the "DET family" note type (docs/anki.md): 7 fields + a tag column. --batch and
--my-words mark the my-words entries they wrote as done; a sub-band deck never touches my-words.csv.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import learn, store                                  # noqa: E402  (after the sys.path line)
from app.bank import Bank, load_subbands                      # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("subband", nargs="?", help="sub-band to export, e.g. 4k-a")
    ap.add_argument("--batch", type=int, metavar="N", help="the study-list batch of N words (the Learn button)")
    ap.add_argument("--my-words", action="store_true", help="the open my-words entries")
    ap.add_argument("--check", action="store_true", help="with a sub-band: list families with no gappable example")
    a = ap.parse_args(argv)
    if sum(bool(x) for x in (a.subband, a.batch, a.my_words)) != 1:
        ap.error("give exactly one of: a sub-band, --batch N, --my-words")

    subbands = load_subbands()
    bank = Bank()
    synonyms = learn.load_synonyms()
    sessions = store.load_sessions()
    stats = learn.word_stats(sessions)
    today = date.today()

    if a.subband:
        if a.subband not in {b.name for b in subbands}:
            ap.error(f"unknown sub-band {a.subband!r}; one of {', '.join(b.name for b in subbands)}")
        entries = learn.subband_entries(a.subband, bank.index, stats, synonyms, bank.examples)
        if a.check:
            missing = [e["family"] for e in entries if not e["gap"]]
            print("\n".join(missing))
            print(f"{len(missing)} of {len(entries)} {a.subband} families have no example containing the word "
                  f"(add a row to vocab/overrides.csv)", file=sys.stderr)
            return 0
        name, done = a.subband, []
    elif a.batch:
        _, front = learn.frontier(learn.subband_scores(sessions, subbands))
        my_words = learn.open_my_words(learn.load_my_words())
        entries = learn.study_list(stats, front, bank.index, synonyms, my_words, a.batch, bank.examples)
        name, done = today.isoformat(), [e["family"] for e in entries if e["reason"] == "my-words"]
    else:
        entries = learn.my_words_entries(learn.open_my_words(learn.load_my_words()), bank.index, stats, synonyms,
                                         bank.examples)
        name, done = "my-words", [e["family"] for e in entries]

    path = learn.export_anki(name, entries)
    n_done = learn.mark_done(done, today) if done else 0
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    print(f"{len(entries)} notes → {rel}" + (f"  ({n_done} my-words marked done)" if n_done else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
