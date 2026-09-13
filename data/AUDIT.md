# Data audit

## 1. Sources

| source | file | rows | used for |
|---|---|---|---|
| Nation BNC/COCA 1-6K | data/raw/nation/basewrd1-6.txt | 6,000 families | family, rank, members |
| Nation official headword PDFs | data/raw/nation/official-pdf/*.pdf | 6,003 headwords | verification of the mirror only |
| Coxhead AWL | data/extract/awl_families.tsv | 570 families | awl flag, awl sub-band |
| SUBTLEX-US (Zipf + PoS) | data/extract/subtlex_zipf.tsv | 74,286 words | zipf, pos fallback |
| Brysbaert 2019 prevalence | data/extract/prevalence.tsv | 61,855 lemmas | prevalence (Pknown) |
| Oxford 3000/5000 | data/extract/oxford_cefr.tsv | 4,952 words | cefr, pos |
| Pearson GSE | - | 0 | not obtainable without licence; column left blank |

## 2. Nation mirror vs official headword PDFs

The family files (`basewrd*.txt`) come from a GitHub mirror; Nation's site only publishes headword PDFs. Per 1000-band, headwords present in only one of the two:

| band | pdf | mirror | shared | only in pdf | only in mirror |
|---|---|---|---|---|---|
| 1 | 1000 | 1000 | 993 (99%) | amaze, bush, hunger, internet, nobody, nothing, wed | hungry, liked, missed, presented, rested, willing, yards |
| 2 | 1000 | 1000 | 994 (99%) | fascinate, mow, non, nowhere, pat, ray | amaze, bowled, countryside, internet, rowed, wed |
| 3 | 1000 | 1000 | 997 (100%) | dna, elevate, likeness | cheque, identity, nets |
| 4 | 1001 | 1000 | 993 (99%) | alpha, appal, compliance, d, ethics, immigrate, mid, straightforwar | bush, dislike, elevate, fascinate, pat, straightforward, wifi |
| 5 | 1000 | 1000 | 995 (100%) | ex, ion, node, presently, un | aflame, appal, immigrate, niece, ray |
| 6 | 1002 | 1000 | 991 (99%) | ante, behold, epistemology, ethanol, g, granny, incomprehensib, le, niece, notwithstandin, pre | al, disregard, howdy, incomprehensible, ipad, maladjusted, mow, node, notwithstanding |

## 3. Nation list internals

| band | headwords | members | members/family | count = 0 |
|---|---|---|---|---|
| 1 | 1000 | 6072 | 6.1 | 0 |
| 2 | 1000 | 5505 | 5.5 | 0 |
| 3 | 1000 | 4976 | 5.0 | 0 |
| 4 | 1000 | 3955 | 4.0 | 1 |
| 5 | 1000 | 3343 | 3.3 | 0 |
| 6 | 1000 | 3162 | 3.2 | 1 |

Headwords inside each file are alphabetical (plus a few hand-placed words at the top), so the rank inside a 1000-band is derived from the family frequency count in the file. Families with count 0 sort last in their band.

## 4. Join coverage per sub-band (matched on headword)

| subband | rows | zipf | zipf +members | prevalence | prevalence +members | cefr | pos |
|---|---|---|---|---|---|---|---|
| 1k-a | 500 | 100% | 100% | 99% | 100% | 99% | 100% |
| 1k-b | 500 | 100% | 100% | 98% | 100% | 96% | 100% |
| 2k-a | 500 | 100% | 100% | 99% | 100% | 98% | 100% |
| 2k-b | 500 | 100% | 100% | 99% | 100% | 82% | 100% |
| 3k-a | 500 | 100% | 100% | 99% | 100% | 96% | 100% |
| 3k-b | 500 | 100% | 100% | 97% | 100% | 87% | 100% |
| 4k-a | 500 | 99% | 100% | 98% | 100% | 63% | 99% |
| 4k-b | 500 | 100% | 100% | 97% | 100% | 49% | 99% |
| 5k-a | 500 | 100% | 100% | 99% | 100% | 19% | 100% |
| 5k-b | 500 | 100% | 100% | 98% | 99% | 12% | 100% |
| 6k-a | 500 | 99% | 100% | 98% | 99% | 2% | 99% |
| 6k-b | 500 | 99% | 100% | 98% | 100% | 3% | 99% |
| awl | 7 | 71% | 86% | 86% | 86% | 43% | 86% |
| **all** | 6007 | 100% | 100% | 98% | 100% | 59% | 100% |

`+members` = coverage if a missing headword falls back to any family member (e.g. British *privatise* -> member *privatize*). Not applied in the build yet; listed to show the gain.

## 5. Oxford CEFR tag by sub-band

| subband | A1 | A2 | B1 | B2 | C1 | untagged | A1+A2 (1-2k) / B2+C1 (3k+) of tagged |
|---|---|---|---|---|---|---|---|
| 1k-a | 374 | 99 | 17 | 6 | 0 | 4 | 95% |
| 1k-b | 223 | 164 | 63 | 28 | 2 | 20 | 81% |
| 2k-a | 80 | 146 | 136 | 120 | 10 | 8 | 46% |
| 2k-b | 58 | 76 | 85 | 135 | 56 | 90 | 33% |
| 3k-a | 10 | 68 | 77 | 256 | 70 | 19 | 68% |
| 3k-b | 7 | 25 | 46 | 169 | 190 | 63 | 82% |
| 4k-a | 3 | 10 | 17 | 77 | 207 | 186 | 90% |
| 4k-b | 4 | 11 | 14 | 61 | 155 | 255 | 88% |
| 5k-a | 2 | 5 | 1 | 15 | 72 | 405 | 92% |
| 5k-b | 2 | 3 | 2 | 12 | 43 | 438 | 89% |
| 6k-a | 1 | 0 | 2 | 4 | 2 | 491 | 67% |
| 6k-b | 0 | 0 | 0 | 5 | 9 | 486 | 100% |
| awl | 0 | 0 | 0 | 3 | 0 | 4 | 100% |

Oxford covers ~5,000 words, so most 5k-6k rows are untagged; the last column is computed over tagged rows only.

## 6. Difficulty gradient per sub-band

| subband | zipf min | zipf median | zipf max | prevalence median | prevalence < 0.90 |
|---|---|---|---|---|---|
| 1k-a | 3.09 | 5.36 | 7.62 | 0.996 | 0 |
| 1k-b | 3.03 | 4.80 | 6.07 | 0.997 | 1 |
| 2k-a | 2.47 | 4.30 | 5.32 | 0.996 | 1 |
| 2k-b | 2.07 | 4.20 | 5.48 | 0.995 | 0 |
| 3k-a | 1.99 | 3.80 | 4.84 | 0.995 | 2 |
| 3k-b | 1.77 | 3.72 | 5.45 | 0.995 | 1 |
| 4k-a | 1.77 | 3.53 | 5.02 | 0.995 | 0 |
| 4k-b | 1.77 | 3.57 | 4.79 | 0.995 | 2 |
| 5k-a | 1.59 | 3.39 | 4.99 | 0.992 | 7 |
| 5k-b | 1.59 | 3.35 | 5.35 | 0.992 | 4 |
| 6k-a | 1.59 | 3.13 | 5.02 | 0.988 | 16 |
| 6k-b | 1.59 | 3.10 | 5.13 | 0.990 | 19 |
| awl | 1.77 | 2.77 | 3.02 | 0.975 | 0 |

Zipf: 1-2 rare, 3-4 mid, 5-7 very common (SUBTLEX-US, film subtitles). Prevalence = share of US native speakers who know the word.

## 7. AWL overlap with Nation 1-6K

| subband | AWL families |
|---|---|
| 1k-a | 11 |
| 1k-b | 10 |
| 2k-a | 125 |
| 2k-b | 8 |
| 3k-a | 238 |
| 3k-b | 80 |
| 4k-a | 45 |
| 4k-b | 17 |
| 5k-a | 17 |
| 5k-b | 4 |
| 6k-a | 6 |
| 6k-b | 2 |
| awl | 7 |

563 of 570 AWL families are already inside Nation 1-6K; only 7 are AWL-only: ethic, widespread, deduce, negate, administrate, append, so-called.

## 8. Oxford 3000/5000 words not covered by Nation 1-6K (headword or member)

| cefr | missing |
|---|---|
| A1 | 22 |
| A2 | 13 |
| B1 | 6 |
| B2 | 25 |
| C1 | 49 |

115 of 4,952 Oxford words are outside Nation 1-6K, e.g. ah, airport, app, artwork, atrocity, backdrop, backup, bathroom, battlefield, bedroom, benchmark, birthday, blog, boyfriend, breakdown, breakthrough, broadband, businessman, cd, classroom, decision-making, desktop, download, downstairs, downtown ...

## 9. Dropped entries

0 entries failed the `^[a-z][a-z'-]*$` filter.

