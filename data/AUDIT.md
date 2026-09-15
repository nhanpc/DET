# Data audit

## 1. Sources

| source | file | rows | used for |
|---|---|---|---|
| Nation BNC/COCA 1-6K | data/raw/nation/basewrd1-6.txt | 6,000 families | family, rank, members |
| Nation official headword PDFs | data/raw/nation/official-pdf/*.pdf | 6,003 headwords | verification of the mirror only |
| Coxhead AWL | data/extract/awl_families.tsv | 570 families | awl flag |
| SUBTLEX-US (Zipf + PoS) | data/extract/subtlex_zipf.tsv | 74,286 words | zipf, pos fallback |
| Brysbaert 2019 prevalence | data/extract/prevalence.tsv | 61,855 lemmas | prevalence (Pknown) |
| Oxford 3000/5000 | data/extract/oxford_cefr.tsv | 4,952 words | cefr, pos |
| Pearson GSE | - | 0 | not obtainable without licence; column left blank |
| Open English WordNet 2025 | data/extract/oewn_senses.tsv, oewn_relations.tsv | 53,207 senses, 122,578 links | definition, example, synonym/antonym/similar links |

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
| 1k-a | 500 | 100% | 100% | 100% | 100% | 99% | 100% |
| 1k-b | 500 | 100% | 100% | 100% | 100% | 98% | 100% |
| 2k-a | 500 | 100% | 100% | 100% | 100% | 100% | 100% |
| 2k-b | 500 | 100% | 100% | 100% | 100% | 85% | 100% |
| 3k-a | 500 | 100% | 100% | 100% | 100% | 98% | 100% |
| 3k-b | 500 | 100% | 100% | 100% | 100% | 94% | 100% |
| 4k-a | 500 | 100% | 100% | 100% | 100% | 66% | 100% |
| 4k-b | 500 | 100% | 100% | 100% | 100% | 52% | 100% |
| 5k-a | 500 | 100% | 100% | 100% | 100% | 19% | 100% |
| 5k-b | 500 | 100% | 100% | 99% | 99% | 13% | 100% |
| 6k-a | 500 | 100% | 100% | 99% | 99% | 2% | 100% |
| 6k-b | 500 | 100% | 100% | 100% | 100% | 3% | 100% |
| **all** | 6000 | 100% | 100% | 100% | 100% | 61% | 100% |

`+members` = coverage if a missing headword falls back to any family member (e.g. British *privatise* -> member *privatize*); the build applies this fallback.

## 5. Oxford CEFR tag by sub-band

| subband | A1 | A2 | B1 | B2 | C1 | untagged | A1+A2 (1-2k) / B2+C1 (3k+) of tagged |
|---|---|---|---|---|---|---|---|
| 1k-a | 374 | 100 | 17 | 6 | 0 | 3 | 95% |
| 1k-b | 226 | 167 | 65 | 29 | 2 | 11 | 80% |
| 2k-a | 80 | 148 | 137 | 122 | 11 | 2 | 46% |
| 2k-b | 59 | 80 | 87 | 139 | 58 | 77 | 33% |
| 3k-a | 10 | 68 | 78 | 263 | 73 | 8 | 68% |
| 3k-b | 7 | 25 | 48 | 180 | 211 | 29 | 83% |
| 4k-a | 3 | 10 | 18 | 79 | 218 | 172 | 91% |
| 4k-b | 4 | 11 | 14 | 64 | 168 | 239 | 89% |
| 5k-a | 2 | 5 | 1 | 16 | 73 | 403 | 92% |
| 5k-b | 3 | 4 | 2 | 13 | 45 | 433 | 87% |
| 6k-a | 1 | 0 | 2 | 4 | 3 | 490 | 70% |
| 6k-b | 0 | 0 | 0 | 7 | 10 | 483 | 100% |

Oxford covers ~5,000 words, so most 5k-6k rows are untagged; the last column is computed over tagged rows only.

## 6. Difficulty gradient per sub-band

| subband | zipf min | zipf median | zipf max | prevalence median | prevalence < 0.90 |
|---|---|---|---|---|---|
| 1k-a | 3.09 | 5.36 | 7.62 | 0.996 | 1 |
| 1k-b | 3.03 | 4.80 | 6.07 | 0.997 | 1 |
| 2k-a | 2.47 | 4.30 | 5.32 | 0.996 | 1 |
| 2k-b | 2.07 | 4.20 | 5.48 | 0.995 | 1 |
| 3k-a | 1.99 | 3.80 | 4.84 | 0.995 | 2 |
| 3k-b | 1.77 | 3.72 | 5.45 | 0.995 | 1 |
| 4k-a | 1.77 | 3.53 | 5.02 | 0.995 | 3 |
| 4k-b | 1.77 | 3.57 | 4.79 | 0.995 | 3 |
| 5k-a | 1.59 | 3.39 | 4.99 | 0.992 | 10 |
| 5k-b | 1.59 | 3.35 | 5.35 | 0.992 | 5 |
| 6k-a | 1.59 | 3.12 | 5.02 | 0.988 | 18 |
| 6k-b | 1.59 | 3.08 | 5.13 | 0.989 | 21 |

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

563 of 570 AWL families are inside Nation 1-6K and flagged awl=1; the rest are in data/dropped.txt.

## 8. Oxford 3000/5000 words not covered by Nation 1-6K (headword or member)

| cefr | missing |
|---|---|
| A1 | 22 |
| A2 | 13 |
| B1 | 6 |
| B2 | 28 |
| C1 | 49 |

118 of 4,952 Oxford words are outside Nation 1-6K, e.g. ah, airport, app, artwork, atrocity, backdrop, backup, bathroom, battlefield, bedroom, benchmark, birthday, blog, boyfriend, breakdown, breakthrough, broadband, businessman, cd, classroom, decision-making, desktop, download, downstairs, downtown ...

## 9. Dropped entries

7 entries in `data/dropped.txt` (`bandN` = failed the `^[a-z][a-z'-]*$` filter, `awl` = AWL family not in Nation 1-6K): awl administrate, awl deduce, awl negate, awl append, awl widespread, awl ethic, awl so-called

## 10. Dictionary and links per sub-band (Open English WordNet)

| subband | rows | ≥1 sense | ≥1 example | ≥1 synonym | ≥1 antonym | ≥1 similar |
|---|---|---|---|---|---|---|
| 1k-a | 500 | 93% | 87% | 78% | 33% | 33% |
| 1k-b | 500 | 99% | 85% | 78% | 21% | 28% |
| 2k-a | 500 | 99% | 88% | 81% | 17% | 26% |
| 2k-b | 500 | 100% | 73% | 75% | 8% | 17% |
| 3k-a | 500 | 100% | 84% | 79% | 14% | 23% |
| 3k-b | 500 | 100% | 80% | 72% | 9% | 21% |
| 4k-a | 500 | 100% | 69% | 66% | 10% | 20% |
| 4k-b | 500 | 99% | 67% | 61% | 5% | 12% |
| 5k-a | 500 | 100% | 63% | 64% | 6% | 18% |
| 5k-b | 500 | 100% | 67% | 59% | 4% | 15% |
| 6k-a | 500 | 99% | 66% | 57% | 3% | 18% |
| 6k-b | 500 | 99% | 64% | 57% | 3% | 14% |
| **all** | 6000 | 99% | 74% | 69% | 11% | 21% |

15,180 senses (≤3 per family), 17,956 links (antonym 816, similar 3,362, synonym 13,778). Synonym and antonym links are stored in both directions.

62 families have no OEWN entry (headword or member): against, al, albeit, alps, although, amid, among, and, aye, barracks, because, beside, bobbed, could, during, et, for, from, how, ibid, if, into, ipad, it, lo, mega, nor, of, or, oscar, ought, per, shall, she, should, since, sued, than, that, the, they, this, to, toward, unless, until, upon, versus, via, we, what, when, whereas, whereby, whether, which, who, wifi, with, without, would, you.

Link targets outside Nation 1-6K (dropped): 16,444 distinct lemmas, most frequent: find out (34), take in (32), make out (28), break up (28), give up (26), set up (26), wad (26), chromatic (24), take out (24), put up (23), give way (22), bear on (21), bring up (21), slew (21), take on (20), break down (20), uprise (20), turn down (20), vex (19), see to it (19).

