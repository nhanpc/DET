# Raw data sources

All files fetched on **2026-09-13** with `scripts/fetch_raw.sh`. Slim extracts are produced by
`scripts/extract_raw.py` into `data/extract/`, which is what the build reads.

| Folder | Source | URL | Licence | Committed? |
|---|---|---|---|---|
| `nation/basewrd1-6.txt` | Paul Nation, BNC/COCA base word lists, bands 1–6 (headword + family members + family frequency count). Nation's site no longer hosts the family files, so these come from a public GitHub mirror (`Cindyzzz616/Mora`, `video_analysis/external_data/basewords_130/`). | https://www.wgtn.ac.nz/lals/resources/paul-nations-resources/vocabulary-lists | free for personal and research use (Nation) | yes (0.5 MB) |
| `nation/official-pdf/*.pdf` | Nation's official headword PDFs for bands 1–6. Used only to verify the mirror; see `data/AUDIT.md` §2 (≥99% identical per band). | same page, `bnccoca-headword-lists/headwords-<n>-thousand.pdf` | as above | no (extract in `data/extract/nation_pdf_headwords.tsv`) |
| `awl/sublist01-10.html` | Coxhead, Academic Word List, 10 sublists with family members. The official page omits `confirm` from sublist 7; `extract_raw.py` adds it back. | https://www.wgtn.ac.nz/lals/resources/academicwordlist/sublist | free | no (extract in `data/extract/awl_families.tsv`) |
| `subtlex/subtlexus1.zip` → `SUBTLEX-US frequency list with PoS and Zipf information.xlsx` | Brysbaert & New 2009; Zipf values from van Heuven et al. 2014; PoS from Brysbaert, New & Keuleers 2012. | https://www.ugent.be/pp/experimentele-psychologie/en/research/documents/subtlexus | free for research/non-commercial use, cite the papers | no (extract `data/extract/subtlex_zipf.tsv`: word, zipf, dom_pos, all_pos) |
| `prevalence/English_Word_Prevalences.xlsx` | Brysbaert, Mandera, McCormick & Keuleers 2019, word prevalence norms for 62,000 English lemmas (`Pknown` = share of US participants who know the word). | https://osf.io/g4xrt/ | CC BY-NC-SA 4.0 (`prevalence/license.txt`) | no (extract `data/extract/prevalence.tsv`) |
| `oxford/The_Oxford_3000.pdf`, `The_Oxford_5000.pdf` | Oxford University Press, Oxford 3000 and Oxford 5000 word lists with CEFR levels (A1–C1) per part of speech. | https://www.oxfordlearnersdictionaries.com/wordlists/ | © OUP; only the word / PoS / level tags are extracted for personal study | no (extract `data/extract/oxford_cefr.tsv`) |
| — | Pearson GSE Vocabulary (10–90 scale) | https://www.english.com/gse | requires licence; not fetched | — |

## Extract files (`data/extract/`, committed)

| File | Columns | Rows |
|---|---|---|
| `awl_families.tsv` | sublist, headword, members | 570 |
| `subtlex_zipf.tsv` | word, zipf, dom_pos, all_pos | 74,286 |
| `prevalence.tsv` | word, pknown, prevalence, nobs | 61,855 |
| `oxford_cefr.tsv` | word, pos, cefr (lowest level), cefr_all, list | 4,952 |
| `nation_pdf_headwords.tsv` | band, headword | 6,003 |

## Reproduce

```bash
scripts/fetch_raw.sh            # needs curl, gh, unzip
python3 scripts/extract_raw.py  # needs openpyxl and pdftotext (poppler-utils)
python3 scripts/build_bands.py  # standard library only
python3 scripts/audit_data.py   # writes data/AUDIT.md
```
