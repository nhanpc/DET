#!/usr/bin/env python3
"""Build the vocabulary index (one row per word family, with its sub-band).

Reads   data/raw/nation/basewrd1-6.txt, data/extract/*.tsv, vocab/subbands.csv
Writes  vocab/index.csv (definition/example kept from build_dict.py), data/dropped.txt

Standard library only. Deterministic: the same inputs give byte-identical outputs.
Grouping rules are specified in GitHub issue nhanpc/DET#2.

    python3 scripts/build_bands.py
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
EXTRACT = ROOT / "data" / "extract"
VOCAB = ROOT / "vocab"
SUBBANDS = VOCAB / "subbands.csv"

WORD_RE = re.compile(r"^[a-z][a-z'-]*$")
INDEX_COLS = ["family", "subband", "rank", "pos_in_band", "zipf", "prevalence", "cefr", "gse", "awl", "pos", "members",
              "definition", "example"]

# SUBTLEX dominant-PoS labels -> short tags used in the Oxford list
SUBTLEX_POS = {"Noun": "n", "Verb": "v", "Adjective": "adj", "Adverb": "adv", "Preposition": "prep",
               "Pronoun": "pron", "Determiner": "det", "Article": "det", "Conjunction": "conj",
               "Interjection": "exclam", "Number": "number", "Name": "name", "Letter": "letter",
               "To": "to", "Ex": "ex", "Not": "not"}


def read_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- Nation lists
def parse_nation() -> tuple[list[dict], list[str]]:
    """Return families with rank, and the list of dropped headwords.

    Each basewrdN.txt lists `HEADWORD count` lines followed by tab-indented `MEMBER count`
    lines. Headwords inside a file are alphabetical, not by frequency, so the rank inside a
    1000-band is taken from the family count (descending); band N covers ranks
    (N-1)*1000+1 .. N*1000.
    """
    families: list[dict] = []
    dropped: list[str] = []
    for band in range(1, 7):
        entries: list[dict] = []
        cur = None
        for line in (RAW / "nation" / f"basewrd{band}.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            parts = line.strip().split()
            word = parts[0].lower()
            count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            if line[0] in " \t":
                if cur is not None and word != cur["family"] and word not in cur["members"]:
                    cur["members"].append(word)
            else:
                if not WORD_RE.match(word):
                    dropped.append(f"band{band}\t{word}")
                    cur = None
                    continue
                cur = {"family": word, "count": count, "band": band, "members": []}
                entries.append(cur)
        entries.sort(key=lambda e: (-e["count"], e["family"]))
        for i, e in enumerate(entries, start=1):
            e["rank"] = (band - 1) * 1000 + i
        families.extend(entries)
    return families, dropped


# ---------------------------------------------------------------- main build
def build() -> int:
    cuts = read_csv(SUBBANDS)
    rank_bands = [(int(c["rank_from"]), int(c["rank_to"]), c["subband"]) for c in cuts if c["rank_from"]]
    band_order = [c["subband"] for c in sorted(cuts, key=lambda c: int(c["order"]))]

    def subband_for(rank: int) -> str:
        for lo, hi, name in rank_bands:
            if lo <= rank <= hi:
                return name
        raise ValueError(f"rank {rank} outside cut table")

    families, dropped = parse_nation()
    by_head = {f["family"]: f for f in families}
    member_owner = {m: f for f in families for m in f["members"]}

    # AWL is a tag, not a sub-band: families in Nation 1-6K get awl=1, the rest are dropped
    for f in families:
        f["awl"] = 0
    awl_hit = 0
    for row in read_tsv(EXTRACT / "awl_families.tsv"):
        hw = row["headword"]
        owner = by_head.get(hw) or member_owner.get(hw)
        if owner is not None:
            owner["awl"] = 1
            awl_hit += 1
        else:
            dropped.append(f"awl\t{hw}")

    # difficulty columns: join on the headword, else fall back to the family members
    # (British spellings, inflected headwords). Best value across members wins.
    zipf = {r["word"]: r for r in read_tsv(EXTRACT / "subtlex_zipf.tsv")}
    prev = {r["word"]: r for r in read_tsv(EXTRACT / "prevalence.tsv")}
    oxford = {r["word"]: r for r in read_tsv(EXTRACT / "oxford_cefr.tsv")}
    CEFR_ORDER = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}

    def lookup(table: dict, f: dict, best):
        hit = table.get(f["family"])
        if hit is not None:
            return hit
        hits = [table[m] for m in f["members"] if m in table]
        return min(hits, key=best) if hits else None

    for f in families:
        z = lookup(zipf, f, lambda r: -float(r["zipf"]))
        p = lookup(prev, f, lambda r: -float(r["pknown"]))
        o = lookup(oxford, f, lambda r: CEFR_ORDER.get(r["cefr"], 9))
        f["zipf"] = z["zipf"] if z else ""
        f["prevalence"] = p["pknown"] if p else ""
        f["cefr"] = o["cefr"] if o else ""
        f["gse"] = ""
        if o and o["pos"]:
            f["pos"] = o["pos"]
        elif z and z["dom_pos"] in SUBTLEX_POS:
            f["pos"] = SUBTLEX_POS[z["dom_pos"]]
        else:
            f["pos"] = ""
        f["subband"] = subband_for(f["rank"])

    # order inside each sub-band: zipf desc, prevalence desc, headword asc (blanks last)
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in families:
        groups[f["subband"]].append(f)
    for name, rows in groups.items():
        rows.sort(key=lambda f: (-(float(f["zipf"]) if f["zipf"] else -1.0),
                                 -(float(f["prevalence"]) if f["prevalence"] else -1.0),
                                 f["family"]))
        for i, f in enumerate(rows, start=1):
            f["pos_in_band"] = i

    # validate
    errors, warnings = [], []
    for lo, hi, name in rank_bands:
        n = len(groups.get(name, []))
        if n != hi - lo + 1:
            errors.append(f"{name}: expected {hi - lo + 1} rows, got {n}")
    dup = [w for w, c in Counter(f["family"] for f in families).items() if c > 1]
    if dup:
        errors.append(f"duplicate headwords: {dup[:10]}{'...' if len(dup) > 10 else ''}")
    n_zipf = sum(1 for f in families if f["zipf"])
    if n_zipf / len(families) < 0.95:
        errors.append(f"only {n_zipf}/{len(families)} rows have zipf (<95%)")
    # CEFR sanity checks use Oxford-tagged rows only (Oxford stops at ~5,000 words)
    def share(rows, levels):
        tagged = [f for f in rows if f["cefr"]]
        return (sum(1 for f in tagged if f["cefr"] in levels) / len(tagged), len(tagged)) if len(tagged) >= 30 else (None, len(tagged))

    a1, n_tagged = share(groups["1k-a"], ("A1",))
    if a1 is not None and a1 < 0.80:
        warnings.append(f"1k-a: only {a1:.0%} of {n_tagged} Oxford-tagged rows are A1 (expected >=80%)")
    b2up, n_tagged = share(groups["6k-a"] + groups["6k-b"], ("B2", "C1"))
    if b2up is not None and b2up < 0.70:
        warnings.append(f"6k: only {b2up:.0%} of {n_tagged} Oxford-tagged rows are B2+ (expected >=70%)")

    for w in warnings:
        print("WARN", w)
    for e in errors:
        print("ERROR", e)
    if errors:
        return 1

    # write; definition/example are filled by build_dict.py, so carry them over if present
    index_path = VOCAB / "index.csv"
    dict_cols = {r["family"]: (r["definition"], r["example"]) for r in read_csv(index_path)} if index_path.exists() else {}

    def row_of(f: dict, cols: list[str]) -> list:
        definition, example = dict_cols.get(f["family"], ("", ""))
        d = {"family": f["family"], "subband": f["subband"], "rank": f["rank"] or "", "pos_in_band": f["pos_in_band"],
             "zipf": f["zipf"], "prevalence": f["prevalence"], "cefr": f["cefr"], "gse": f["gse"], "awl": f["awl"],
             "pos": f["pos"], "members": "|".join(f["members"]), "definition": definition, "example": example}
        return [d[c] for c in cols]

    ordered = [f for name in band_order for f in groups.get(name, [])]
    with (VOCAB / "index.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(INDEX_COLS)
        w.writerows(row_of(f, INDEX_COLS) for f in ordered)
    (ROOT / "data" / "dropped.txt").write_text("\n".join(dropped) + ("\n" if dropped else ""), encoding="utf-8")

    print(f"families: {len(families)}  (awl=1: {awl_hit} of 570; AWL families outside Nation 1-6K are in data/dropped.txt)")
    print(f"dropped: {len(dropped)}  -> data/dropped.txt")
    for name in band_order:
        rows = groups.get(name, [])
        print(f"  {name:5s} {len(rows):4d} rows")
    return 0


if __name__ == "__main__":
    sys.exit(build())
