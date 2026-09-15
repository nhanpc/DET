#!/usr/bin/env python3
"""Pick the pseudo-words (invented words) used by the yes/no level test.

Reads   vocab/index.csv, vocab/subbands.csv, data/extract/blp_nonwords.tsv,
        data/raw/nation/basewrd*.txt, data/extract/{subtlex_zipf,prevalence}.tsv
Writes  vocab/pseudowords.csv  (pseudoword, subband, length, accuracy)

Source: British Lexicon Project nonwords (Keuleers et al. 2012, CC BY-NC-SA 4.0). They were
built with Wuggy to mirror real English words, and each carries the share of native speakers
who correctly rejected it in a lexical-decision task. We keep only nonwords that natives
reject reliably, that are not in any word list we have, and that are not a real word plus an
inflection. Each sub-band gets its own set whose letter-length distribution mirrors that
sub-band's headwords, so length gives nothing away. Specified in GitHub issue nhanpc/DET#4.

Standard library only. Deterministic: the same inputs give byte-identical output.

    python3 scripts/build_pseudowords.py [--per-band 120] [--min-accuracy 0.95]
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOCAB = ROOT / "vocab"
EXTRACT = ROOT / "data" / "extract"
NATION = ROOT / "data" / "raw" / "nation"

MIN_LEN, MAX_LEN = 4, 11
ALPHA = re.compile(r"^[a-z]+$")
SUFFIXES = ("s", "es", "ed", "ing", "er", "ly")


def read_rows(path: Path, delimiter: str = ",") -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=delimiter))


def load_lexicon(index: list[dict]) -> set[str]:
    """Every real word form we know of."""
    lex: set[str] = set()
    for r in index:
        lex.add(r["family"])
        lex.update(m for m in r["members"].split("|") if m)
    for p in sorted(NATION.glob("basewrd*.txt")):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                lex.add(line.split()[0].lower())
    for name in ("subtlex_zipf.tsv", "prevalence.tsv"):
        p = EXTRACT / name
        if p.exists():
            lex.update(r["word"] for r in read_rows(p, "\t"))
    return lex


def looks_inflected(pw: str, lex: set[str]) -> bool:
    return any(pw.endswith(s) and pw[: -len(s)] in lex for s in SUFFIXES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-band", type=int, default=120)
    ap.add_argument("--min-accuracy", type=float, default=0.95)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    index = read_rows(VOCAB / "index.csv")
    bands = [r["subband"] for r in read_rows(VOCAB / "subbands.csv")]
    lexicon = load_lexicon(index)

    pool: dict[int, list[tuple[str, str]]] = defaultdict(list)   # length -> [(nonword, accuracy)]
    for r in read_rows(EXTRACT / "blp_nonwords.tsv", "\t"):
        pw, acc = r["nonword"], float(r["accuracy"])
        if not (MIN_LEN <= len(pw) <= MAX_LEN) or acc < args.min_accuracy:
            continue
        if not ALPHA.match(pw) or pw in lexicon or looks_inflected(pw, lexicon):
            continue
        pool[len(pw)].append((pw, r["accuracy"]))
    for n in pool:
        pool[n].sort()
    print("pool by length:", {n: len(pool[n]) for n in sorted(pool)})

    rng = random.Random(args.seed)
    rows: list[tuple[str, str, int, str]] = []
    for band in bands:
        heads = [r["family"] for r in index if r["subband"] == band and ALPHA.match(r["family"])]
        lengths = Counter(min(MAX_LEN, max(MIN_LEN, len(w))) for w in heads)
        total = sum(lengths.values())
        want = {n: round(args.per_band * c / total) for n, c in lengths.items()}
        got = 0
        for n in sorted(want):
            for _ in range(want[n]):
                m = n
                # take from the nearest length that still has candidates
                while m <= MAX_LEN and not pool[m]:
                    m += 1
                if m > MAX_LEN:
                    break
                pw, acc = pool[m].pop(rng.randrange(len(pool[m])))
                rows.append((pw, band, len(pw), acc))
                got += 1
        print(f"{band}: {got} pseudo-words")
        if got < args.per_band - 5:
            print(f"  warning: {band} short of target", file=sys.stderr)

    out = VOCAB / "pseudowords.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["pseudoword", "subband", "length", "accuracy"])
        w.writerows(rows)
    print(f"wrote {out.relative_to(ROOT)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
