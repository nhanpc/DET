#!/usr/bin/env python3
"""Audit the raw word data and the built index. Writes data/AUDIT.md and prints it.

    python3 scripts/audit_data.py
"""
from __future__ import annotations

import csv
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, EXTRACT, VOCAB = ROOT / "data" / "raw", ROOT / "data" / "extract", ROOT / "vocab"
LEVELS = ["A1", "A2", "B1", "B2", "C1", ""]


def read(path: Path, delim: str) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=delim))


def table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def main() -> None:
    index = read(VOCAB / "index.csv", ",")
    cuts = read(VOCAB / "subbands.csv", ",")
    order = [c["subband"] for c in sorted(cuts, key=lambda c: int(c["order"]))]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in index:
        groups[r["subband"]].append(r)
    zipf = read(EXTRACT / "subtlex_zipf.tsv", "\t")
    prev = read(EXTRACT / "prevalence.tsv", "\t")
    oxford = read(EXTRACT / "oxford_cefr.tsv", "\t")
    awl = read(EXTRACT / "awl_families.tsv", "\t")
    pdf = read(EXTRACT / "nation_pdf_headwords.tsv", "\t")
    oewn_senses = read(EXTRACT / "oewn_senses.tsv", "\t")
    oewn_rels = read(EXTRACT / "oewn_relations.tsv", "\t")
    senses = read(VOCAB / "senses.csv", ",") if (VOCAB / "senses.csv").exists() else []
    relations = read(VOCAB / "relations.csv", ",") if (VOCAB / "relations.csv").exists() else []
    zipf_by = {r["word"]: r for r in zipf}
    prev_by = {r["word"]: r for r in prev}
    md: list[str] = ["# Data audit", ""]

    # 1. inventory
    md += ["## 1. Sources", "", table(
        ["source", "file", "rows", "used for"],
        [["Nation BNC/COCA 1-6K", "data/raw/nation/basewrd1-6.txt", "6,000 families", "family, rank, members"],
         ["Nation official headword PDFs", "data/raw/nation/official-pdf/*.pdf", f"{len(pdf):,} headwords", "verification of the mirror only"],
         ["Coxhead AWL", "data/extract/awl_families.tsv", f"{len(awl)} families", "awl flag"],
         ["SUBTLEX-US (Zipf + PoS)", "data/extract/subtlex_zipf.tsv", f"{len(zipf):,} words", "zipf, pos fallback"],
         ["Brysbaert 2019 prevalence", "data/extract/prevalence.tsv", f"{len(prev):,} lemmas", "prevalence (Pknown)"],
         ["Oxford 3000/5000", "data/extract/oxford_cefr.tsv", f"{len(oxford):,} words", "cefr, pos"],
         ["Pearson GSE", "-", "0", "not obtainable without licence; column left blank"],
         ["Open English WordNet 2025", "data/extract/oewn_senses.tsv, oewn_relations.tsv", f"{len(oewn_senses):,} senses, {len(oewn_rels):,} links", "definition, example, synonym/antonym/similar links"]]), ""]

    # 2. mirror vs official pdf
    md += ["## 2. Nation mirror vs official headword PDFs", "",
           "The family files (`basewrd*.txt`) come from a GitHub mirror; Nation's site only publishes headword PDFs. "
           "Per 1000-band, headwords present in only one of the two:", ""]
    rows = []
    for band in range(1, 7):
        official = {r["headword"] for r in pdf if int(r["band"]) == band}
        mirror = set()
        for line in (RAW / "nation" / f"basewrd{band}.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            if line and line[0] not in " \t":
                mirror.add(line.split()[0].lower())
        only_pdf, only_mirror = sorted(official - mirror), sorted(mirror - official)
        rows.append([band, len(official), len(mirror), f"{len(official & mirror)} ({pct(len(official & mirror), len(mirror))})",
                     ", ".join(only_pdf) or "-", ", ".join(only_mirror) or "-"])
    md += [table(["band", "pdf", "mirror", "shared", "only in pdf", "only in mirror"], rows), ""]

    # 3. nation internals
    md += ["## 3. Nation list internals", ""]
    rows = []
    for band in range(1, 7):
        heads = members = zero = 0
        for line in (RAW / "nation" / f"basewrd{band}.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            if line[0] in " \t":
                members += 1
            else:
                heads += 1
                parts = line.split()
                if len(parts) < 2 or parts[1] == "0":
                    zero += 1
        rows.append([band, heads, members, f"{members / heads:.1f}", zero])
    md += [table(["band", "headwords", "members", "members/family", "count = 0"], rows), "",
           "Headwords inside each file are alphabetical (plus a few hand-placed words at the top), so the rank inside "
           "a 1000-band is derived from the family frequency count in the file. Families with count 0 sort last in their band.", ""]

    # 4. join coverage per subband
    md += ["## 4. Join coverage per sub-band (matched on headword)", ""]
    rows = []
    tot = Counter()
    for name in order:
        g = groups[name]
        n = len(g)
        c = Counter()
        for r in g:
            c["zipf"] += bool(r["zipf"])
            c["prev"] += bool(r["prevalence"])
            c["cefr"] += bool(r["cefr"])
            c["pos"] += bool(r["pos"])
            members = r["members"].split("|") if r["members"] else []
            c["zipf_fb"] += bool(r["zipf"] or any(m in zipf_by for m in members))
            c["prev_fb"] += bool(r["prevalence"] or any(m in prev_by for m in members))
        tot.update(c)
        tot["n"] += n
        rows.append([name, n, pct(c["zipf"], n), pct(c["zipf_fb"], n), pct(c["prev"], n), pct(c["prev_fb"], n), pct(c["cefr"], n), pct(c["pos"], n)])
    n = tot["n"]
    rows.append(["**all**", n, pct(tot["zipf"], n), pct(tot["zipf_fb"], n), pct(tot["prev"], n), pct(tot["prev_fb"], n), pct(tot["cefr"], n), pct(tot["pos"], n)])
    md += [table(["subband", "rows", "zipf", "zipf +members", "prevalence", "prevalence +members", "cefr", "pos"], rows), "",
           "`+members` = coverage if a missing headword falls back to any family member (e.g. British *privatise* -> member *privatize*); "
           "the build applies this fallback.", ""]

    # 5. cefr cross-tab
    md += ["## 5. Oxford CEFR tag by sub-band", ""]
    rows = []
    for name in order:
        g = groups[name]
        c = Counter(r["cefr"] for r in g)
        tagged = len(g) - c[""]
        rows.append([name] + [c[l] for l in LEVELS[:-1]] + [c[""], pct(c["A1"] + c["A2"], tagged) if name.startswith(("1k", "2k")) else pct(c["B2"] + c["C1"], tagged)])
    md += [table(["subband", "A1", "A2", "B1", "B2", "C1", "untagged", "A1+A2 (1-2k) / B2+C1 (3k+) of tagged"], rows), "",
           "Oxford covers ~5,000 words, so most 5k-6k rows are untagged; the last column is computed over tagged rows only.", ""]

    # 6. zipf / prevalence distribution
    md += ["## 6. Difficulty gradient per sub-band", ""]
    rows = []
    for name in order:
        z = sorted(float(r["zipf"]) for r in groups[name] if r["zipf"])
        p = sorted(float(r["prevalence"]) for r in groups[name] if r["prevalence"])
        rows.append([name, f"{z[0]:.2f}", f"{statistics.median(z):.2f}", f"{z[-1]:.2f}", f"{statistics.median(p):.3f}", sum(1 for x in p if x < 0.90)])
    md += [table(["subband", "zipf min", "zipf median", "zipf max", "prevalence median", "prevalence < 0.90"], rows), "",
           "Zipf: 1-2 rare, 3-4 mid, 5-7 very common (SUBTLEX-US, film subtitles). Prevalence = share of US native speakers who know the word.", ""]

    # 7. AWL overlap
    md += ["## 7. AWL overlap with Nation 1-6K", ""]
    c = Counter(r["subband"] for r in index if r["awl"] == "1")
    rows = [[name, c[name]] for name in order if c[name]]
    md += [table(["subband", "AWL families"], rows), "",
           f"{sum(c.values())} of 570 AWL families are inside Nation 1-6K and flagged awl=1; the rest are in data/dropped.txt.", ""]

    # 8. Oxford not in nation
    ox_words = {r["word"] for r in oxford}
    in_index = {r["family"] for r in index} | {m for r in index for m in r["members"].split("|") if m}
    missing = sorted(w for w in ox_words if w not in in_index and " " not in w and "," not in w)
    by_level = Counter(next(r["cefr"] for r in oxford if r["word"] == w) for w in missing)
    md += ["## 8. Oxford 3000/5000 words not covered by Nation 1-6K (headword or member)", "",
           table(["cefr", "missing"], [[l, by_level[l]] for l in LEVELS[:-1]]), "",
           f"{len(missing)} of {len(ox_words):,} Oxford words are outside Nation 1-6K, e.g. " + ", ".join(missing[:25]) + " ...", ""]

    # 9. dropped
    dropped = (ROOT / "data" / "dropped.txt").read_text(encoding="utf-8").splitlines() if (ROOT / "data" / "dropped.txt").exists() else []
    md += ["## 9. Dropped entries", "",
           f"{len(dropped)} entries in `data/dropped.txt` (`bandN` = failed the `^[a-z][a-z'-]*$` filter, "
           "`awl` = AWL family not in Nation 1-6K)" + (": " + ", ".join(d.replace("\t", " ") for d in dropped) if dropped else "."), ""]

    # 10. dictionary and links (build_dict.py)
    md += ["## 10. Dictionary and links per sub-band (Open English WordNet)", ""]
    if senses:
        has_sense = defaultdict(set)
        has_ex = defaultdict(set)
        for r in senses:
            has_sense[r["family"]].add(r["sense"])
            if r["example"]:
                has_ex[r["family"]].add(r["sense"])
        has_rel = defaultdict(lambda: defaultdict(set))
        for r in relations:
            has_rel[r["relation"]][r["family"]].add(r["target"])
        rows, tot = [], Counter()
        for name in order:
            g = groups[name]
            n = len(g)
            c = Counter(sense=sum(1 for r in g if r["family"] in has_sense),
                        example=sum(1 for r in g if r["family"] in has_ex),
                        synonym=sum(1 for r in g if r["family"] in has_rel["synonym"]),
                        antonym=sum(1 for r in g if r["family"] in has_rel["antonym"]),
                        similar=sum(1 for r in g if r["family"] in has_rel["similar"]))
            tot.update(c)
            tot["n"] += n
            rows.append([name, n, pct(c["sense"], n), pct(c["example"], n), pct(c["synonym"], n), pct(c["antonym"], n), pct(c["similar"], n)])
        n = tot["n"]
        rows.append(["**all**", n, pct(tot["sense"], n), pct(tot["example"], n), pct(tot["synonym"], n), pct(tot["antonym"], n), pct(tot["similar"], n)])
        md += [table(["subband", "rows", "≥1 sense", "≥1 example", "≥1 synonym", "≥1 antonym", "≥1 similar"], rows), ""]
        no_entry = sorted(r["family"] for r in index if r["family"] not in has_sense)
        in_index = {r["family"] for r in index} | {m for r in index for m in r["members"].split("|") if m}
        outside = Counter(r["target"] for r in oewn_rels if r["lemma"] in {x["family"] for x in index} and r["target"] not in in_index)
        by_rel = Counter(r["relation"] for r in relations)
        md += [f"{len(senses):,} senses (≤3 per family), {len(relations):,} links "
               f"({', '.join(f'{k} {v:,}' for k, v in sorted(by_rel.items()))}). "
               f"Synonym and antonym links are stored in both directions.", "",
               f"{len(no_entry)} families have no OEWN entry (headword or member): " + ", ".join(no_entry) + ".", "",
               f"Link targets outside Nation 1-6K (dropped): {len(outside):,} distinct lemmas, most frequent: "
               + ", ".join(f"{w} ({c})" for w, c in outside.most_common(20)) + ".", ""]
    else:
        md += ["`vocab/senses.csv` not built yet — run `python3 scripts/build_dict.py`.", ""]

    text = "\n".join(md)
    (ROOT / "data" / "AUDIT.md").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
