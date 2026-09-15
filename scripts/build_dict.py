#!/usr/bin/env python3
"""Build the dictionary (senses) and the synonym/antonym links for every family.

Reads   vocab/index.csv, data/extract/oewn_{senses,relations,forms}.tsv
Writes  vocab/senses.csv, vocab/relations.csv, and fills definition/example in vocab/index.csv

Standard library only. Deterministic: the same inputs give byte-identical outputs.
Rules are specified in GitHub issue nhanpc/DET#3.

    python3 scripts/build_dict.py
"""
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = ROOT / "data" / "extract"
VOCAB = ROOT / "vocab"

MAX_SENSES = 3
POS_ORDER = ["n", "v", "adj", "adv"]
SYMMETRIC = ("synonym", "antonym")


def read_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, header: list[str], rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def build() -> int:
    index = read_csv(VOCAB / "index.csv")
    families = [r["family"] for r in index]
    fam_set = set(families)
    to_family = {r["family"]: r["family"] for r in index}          # headword first, then members
    for r in index:
        for m in r["members"].split("|"):
            to_family.setdefault(m, r["family"])

    # OEWN slice, grouped per lemma
    senses_of: dict[str, list[dict]] = defaultdict(list)
    for r in read_tsv(EXTRACT / "oewn_senses.tsv"):
        senses_of[r["lemma"]].append(r)
    form_of = {r["form"]: r["lemma"] for r in read_tsv(EXTRACT / "oewn_forms.tsv")}
    links_of: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for r in read_tsv(EXTRACT / "oewn_relations.tsv"):
        links_of[(r["lemma"], r["pos"], r["sense_no"])].append((r["relation"], r["target"]))

    # senses: headword, else Capitalised headword (Friday), an OEWN inflected form (media -> medium),
    # the headword minus a regular inflection (patients, presented, kidding), else the first member
    # with an entry. Preferred pos (from index.pos) first, then n, v, adj, adv; OEWN order inside a
    # pos; keep MAX_SENSES
    def stems(w: str) -> list[tuple[str, str]]:
        """(stem, pos hint) for a regular inflection: -ed/-ing say verb, -s says nothing."""
        out = []
        for suffix, repl, hint in (("ies", "y", ""), ("ied", "y", "v"), ("es", "", ""), ("s", "", ""),
                                   ("ed", "", "v"), ("ed", "e", "v"), ("d", "", "v"), ("ing", "", "v"), ("ing", "e", "v")):
            if w.endswith(suffix) and len(w) > len(suffix) + 2:
                out.append((w[: -len(suffix)] + repl, hint))
        return out

    def lemma_for(fam: str, members: list[str], pos: str) -> tuple[str | None, str]:
        """Return (OEWN lemma, preferred pos). Capitalised forms only for nouns (Friday, not You)."""
        cap = pos == "n"
        candidates = [(fam, pos), (fam.capitalize() if cap else "", pos), (form_of.get(fam, ""), pos)]
        for st, hint in stems(fam):
            candidates += [(st, hint or pos), (st.capitalize() if cap else "", hint or pos)]
        # a member only counts if it has the family's part of speech (so `you` does not become `yer`)
        candidates += [(m, pos) for m in members if not pos or any(x["pos"] == pos for x in senses_of.get(m, []))]
        return next(((c, p) for c, p in candidates if c and c in senses_of), (None, pos))

    sense_rows: list[tuple] = []                   # (family, sense, pos, definition, example, synset)
    origin: dict[tuple[str, int], tuple[str, str, str]] = {}   # (family, sense) -> (lemma, pos, oewn sense_no)
    kept: dict[tuple[str, str], int] = {}          # (family, synset) -> sense number
    no_entry, via_member = [], 0
    for r in index:
        fam = r["family"]
        lemma, preferred = lemma_for(fam, [m for m in r["members"].split("|") if m], r["pos"].split("|")[0])
        if lemma is None:
            no_entry.append(fam)
            continue
        via_member += lemma != fam
        rank = {p: i for i, p in reversed(list(enumerate([preferred] + POS_ORDER)))}   # preferred wins
        ordered = sorted(senses_of[lemma], key=lambda s: (rank.get(s["pos"], 99), int(s["sense_no"])))
        for n, s in enumerate(ordered[:MAX_SENSES], start=1):
            sense_rows.append((fam, n, s["pos"], s["definition"], s["example"], s["synset"]))
            kept.setdefault((fam, s["synset"]), n)
            origin[(fam, n)] = (lemma, s["pos"], s["sense_no"])

    # links: from the kept senses only, targets mapped to families inside the index
    links: dict[tuple[str, str, str], int | None] = {}   # (family, relation, target) -> sense (None = not a kept sense)
    dropped = Counter()
    dropped_targets = Counter()

    def add(fam: str, rel: str, target: str, sense: int | None) -> None:
        key = (fam, rel, target)
        cur = links.get(key, None)
        if key not in links or (sense is not None and (cur is None or sense < cur)):
            links[key] = sense

    pos_of = defaultdict(set)                      # family -> pos of its kept senses
    for fam, _n, pos, *_ in sense_rows:
        pos_of[fam].add(pos)

    def target_family(target: str, pos: str) -> str | None:
        """Headword match, else a member match whose family has a kept sense with the same pos
        (so `mountainous` does not link `big` to `mountain`)."""
        if target in fam_set:
            return target
        tfam = to_family.get(target)
        return tfam if tfam is not None and pos in pos_of[tfam] else None

    for fam, n, pos, _d, _e, synset in sense_rows:
        for rel, target in links_of.get(origin[(fam, n)], []):
            tfam = target_family(target, pos)
            if tfam is None:
                dropped[rel] += 1
                dropped_targets[target] += 1
                continue
            if tfam == fam:
                continue
            add(fam, rel, tfam, n)
            if rel in SYMMETRIC:
                add(tfam, rel, fam, kept.get((tfam, synset)) if rel == "synonym" else None)

    # antonyms are rare and lexical, so take them from every sense of the lemma, not just the kept
    # ones; `sense` stays blank when the antonym belongs to a sense beyond the kept three
    chosen = {fam: lemma for (fam, n), (lemma, _p, _s) in origin.items() if n == 1}
    for fam, lemma in chosen.items():
        for s in senses_of[lemma]:
            for rel, target in links_of.get((lemma, s["pos"], s["sense_no"]), []):
                tfam = target_family(target, s["pos"])
                if rel != "antonym" or tfam is None or tfam == fam:
                    continue
                add(fam, rel, tfam, None)
                add(tfam, rel, fam, None)

    rel_rows = sorted((f, r, t, s if s is not None else "") for (f, r, t), s in links.items())

    # validate
    errors, warnings = [], []
    bad = [f for f, *_ in sense_rows if f not in fam_set] + [x for f, _r, t, _s in rel_rows for x in (f, t) if x not in fam_set]
    if bad:
        errors.append(f"unknown families: {sorted(set(bad))[:10]}")
    dup = [k for k, c in Counter((f, n) for f, n, *_ in sense_rows).items() if c > 1]
    if dup:
        errors.append(f"duplicate (family, sense): {dup[:10]}")
    with_sense = {f for f, *_ in sense_rows}
    if len(with_sense) / len(families) < 0.95:
        errors.append(f"only {len(with_sense)}/{len(families)} families have a sense (<95%)")
    with_example = {f for f, n, _p, _d, e, _s in sense_rows if e}
    has = {rel: {f for f, r, *_ in rel_rows if r == rel} for rel in ("synonym", "antonym", "similar")}
    for name, got, floor in (("synonym", has["synonym"], 0.60), ("antonym", has["antonym"], 0.20), ("example", with_example, 0.50)):
        share = len(got) / len(families)
        if share < floor:
            warnings.append(f"only {share:.0%} of families have a {name} (expected >={floor:.0%})")
    for w in warnings:
        print("WARN", w)
    for e in errors:
        print("ERROR", e)
    if errors:
        return 1

    # write
    write_csv(VOCAB / "senses.csv", ["family", "sense", "pos", "definition", "example", "synset"], sense_rows)
    write_csv(VOCAB / "relations.csv", ["family", "relation", "target", "sense"], rel_rows)
    primary = {f: (d, e) for f, n, _p, d, e, _s in sense_rows if n == 1}
    cols = list(index[0].keys())
    for r in index:
        r["definition"], r["example"] = primary.get(r["family"], ("", ""))
    write_csv(VOCAB / "index.csv", cols, ([r[c] for c in cols] for r in index))

    print(f"families: {len(families)}  with sense: {len(with_sense)}  via member: {via_member}  no OEWN entry: {len(no_entry)}")
    print(f"senses: {len(sense_rows)}  with example: {sum(1 for *_, e, _s in sense_rows if e)}")
    for rel in ("synonym", "antonym", "similar"):
        print(f"  {rel:8s} {sum(1 for r in rel_rows if r[1] == rel):6d} links, {len(has[rel]):4d} families, dropped (target outside index): {dropped[rel]}")
    return 0


if __name__ == "__main__":
    sys.exit(build())
