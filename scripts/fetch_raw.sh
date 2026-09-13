#!/usr/bin/env bash
# Download every raw word-data source into data/raw/.
# Re-runnable: existing files are overwritten. See data/raw/SOURCES.md for what each file is.
set -euo pipefail
cd "$(dirname "$0")/.."
RAW=data/raw
mkdir -p "$RAW"/{nation/official-pdf,awl,subtlex,prevalence,oxford}

echo "== Nation BNC/COCA base word lists (bands 1-6)"
# Nation's site no longer hosts the family files; only the headword PDFs remain there.
# The family files come from a public GitHub mirror and are verified against the official PDFs.
for i in 1 2 3 4 5 6; do
  gh api "repos/Cindyzzz616/Mora/contents/video_analysis/external_data/basewords_130/basewrd$i.txt" --jq '.download_url' \
    | xargs curl -sSL -o "$RAW/nation/basewrd$i.txt"
done
for n in first second third fourth fifth sixth; do
  curl -sSL -o "$RAW/nation/official-pdf/headwords-$n-thousand.pdf" \
    "https://www.wgtn.ac.nz/lals/resources/paul-nations-resources/vocabulary-lists/bnccoca-headword-lists/headwords-$n-thousand.pdf"
done

echo "== Coxhead Academic Word List (10 sublists, with family members)"
for i in 01 02 03 04 05 06 07 08 09 10; do
  curl -sSL -o "$RAW/awl/sublist$i.html" "https://www.wgtn.ac.nz/lals/resources/academicwordlist/sublist/sublist$i"
done

echo "== SUBTLEX-US with PoS and Zipf"
curl -sSL -o "$RAW/subtlex/subtlexus1.zip" \
  "https://www.ugent.be/pp/experimentele-psychologie/en/research/documents/subtlexus/subtlexus1.zip"
unzip -oq "$RAW/subtlex/subtlexus1.zip" -d "$RAW/subtlex"

echo "== Brysbaert et al. 2019 word prevalence (OSF g4xrt)"
curl -sSL -o "$RAW/prevalence/English_Word_Prevalences.xlsx" "https://osf.io/download/nbu9e/"
curl -sSL -o "$RAW/prevalence/license.txt" "https://osf.io/download/z7fgj/"

echo "== Oxford 3000 / 5000 (official PDFs with CEFR levels)"
for f in The_Oxford_3000 The_Oxford_5000; do
  curl -sSL -o "$RAW/oxford/$f.pdf" \
    "https://www.oxfordlearnersdictionaries.com/external/pdf/wordlists/oxford-3000-5000/$f.pdf"
done

echo "done. Now run: python3 scripts/extract_raw.py"
