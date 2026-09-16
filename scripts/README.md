# Scripts

Run everything from the repo root.

| Script | Needs | Reads | Writes |
|---|---|---|---|
| `fetch_raw.sh` | curl, gh, unzip | the internet | `data/raw/` (see `data/raw/SOURCES.md`) |
| `extract_raw.py` | openpyxl, pdftotext | `data/raw/` | `data/extract/*.tsv` |
| `build_bands.py` | Python 3.10+ (stdlib) | `data/raw/nation/`, `data/extract/`, `vocab/subbands.csv` | `vocab/index.csv`, `data/dropped.txt` |
| `build_dict.py` | Python 3.10+ (stdlib) | `vocab/index.csv`, `data/extract/oewn_*.tsv` | `vocab/senses.csv`, `vocab/relations.csv`, `definition`/`example` in `vocab/index.csv` |
| `build_pseudowords.py` | Python 3.10+ (stdlib) | `vocab/index.csv`, `vocab/subbands.csv`, `data/extract/blp_nonwords.tsv`, `data/raw/nation/`, `data/extract/{subtlex_zipf,prevalence}.tsv` | `vocab/pseudowords.csv` |
| `audit_data.py` | Python 3.10+ (stdlib) | the above | `data/AUDIT.md` |
| `export_anki.py` | Python 3.10+ (stdlib; imports `app/`) | `vocab/{index,senses,relations,overrides,my-words}.csv`, `vocab/tests/sessions/` | `vocab/decks/<subband>.txt`, `<date>.txt` (`--batch N`) or `my-words.txt` (`--my-words`, which also sets `done` in `vocab/my-words.csv`); `--check` writes nothing |
| `report.py` | Python 3.10+ (stdlib; imports `app/`) | `vocab/tests/{sessions/,levels.csv,mocks.csv}`, `vocab/subbands.csv`; `results.csv` with `--check`; a copy of Anki's `collection.anki2` with `--anki PATH`; `practice/listen-and-type/sentences.csv` with `--sentences` | the block between the `<!-- generated:start -->` / `<!-- generated:end -->` markers of `vocab/progress.md` (`--out PATH` elsewhere, created with a stub when missing); `--print` writes nothing; `--sentences` prints the `b_text` histogram per band (the table in `docs/sentences.md`) and builds the sentence cache when missing |
| `passages.py` | Python 3.10+ (stdlib; imports `app/`); `fetch` needs the internet | `practice/read-and-complete/passages/*.md`, `vocab/index.csv`; `fetch`: `scripts/topics.txt` and the Simple English Wikipedia API | `fetch --n N --min 50 --max 80`: up to N new passages into `passages/incoming/` (gitignored) with `source:`, `licence:`, `fetched:` and the scores, every rejected article and its reason on stderr — read them, delete the bad ones, `git mv` the rest (issue #17); `score`: `b_text:`, `b_adjust:`, `features:` in each passage's front matter (`--print` writes nothing); `check`: exit 1 on a passage without source, licence, a current `b_text` or 6 blanks (#15) |

Order: `fetch_raw.sh` → `extract_raw.py` → `build_bands.py` → `extract_raw.py oewn` → `build_dict.py` → `build_pseudowords.py` → `audit_data.py`
(the OEWN extract is sliced to the families in `index.csv`, so it runs after the bands are built).
`export_anki.py` is separate: it runs whenever a deck is wanted and reads only the built files. Deck format and the
Anki note type: [docs/anki.md](../docs/anki.md), specified in [issue #8](https://github.com/nhanpc/DET/issues/8).
`report.py` runs after every level test, after typing a mock row and at the Sunday review (then commit
`vocab/progress.md`); it never touches the hand-written text above the markers and exits 1, writing nothing, when
a marker is missing or repeated, or when a `mocks.csv` row is malformed (the message names the line). Specified in
[issue #9](https://github.com/nhanpc/DET/issues/9) and, for the *Mock tests* section — chart, table, trend, next
week's focus and the booking verdict — [issue #11](https://github.com/nhanpc/DET/issues/11); the rendering lives
in `app/progress.py`, which the app also serves as `GET /api/progress`.

Grouping rules and the column meanings are specified in [issue #2](https://github.com/nhanpc/DET/issues/2);
the short version:

- `vocab/subbands.csv` is the source of truth for the sub-band cuts (rank ranges, CEFR, DET range, mastery %).
- `rank` = Nation's 1000-band × family frequency count inside the band. `pos_in_band` = order inside the sub-band
  by `zipf` desc, `prevalence` desc, headword asc.
- `zipf`, `prevalence`, `cefr`, `pos` are joined on the headword, falling back to the family members (British
  spellings, inflected headwords) — best value across members wins. Blank means no source has any member.
- `awl=1` marks Coxhead AWL families; there is no `awl` sub-band. AWL families outside Nation 1–6K are listed in
  `data/dropped.txt`.

Dictionary and links are specified in [issue #3](https://github.com/nhanpc/DET/issues/3); the short version:

- Source: Open English WordNet 2025 (CC BY 4.0). Text is copied verbatim; blank means OEWN has nothing.
- `vocab/senses.csv` — `family, sense, pos, definition, example, synset`. At most 3 senses per family, in OEWN
  order, the family's `pos` first. Sense 1 fills `definition`/`example` in `index.csv`.
- Lookup: headword → Capitalised headword (nouns: *Friday*) → OEWN inflected form (*media → medium*) → headword
  minus a regular inflection (*patients*, *presented*) → first member with the same part of speech.
- `vocab/relations.csv` — `family, relation, target, sense`. `synonym` = same synset, `antonym` = OEWN antonym,
  `similar` = OEWN similar-to. Both ends are families in `index.csv`; synonym/antonym are stored in both
  directions; `sense` is blank when the link belongs to a sense beyond the three kept.
- Targets that are only a family member (not a headword) are accepted only if that family has a kept sense with
  the same part of speech (so *big* is not linked to *mountain* via *mountainous*).

Pseudo-words are specified in [issue #4](https://github.com/nhanpc/DET/issues/4); the short version:

- Source: British Lexicon Project nonwords (`data/extract/blp_nonwords.tsv`: nonword, accuracy). Kept only when
  natives rejected them ≥ 95 % of the time, 4–11 letters, not in `index.csv` members, Nation 1–6K, SUBTLEX or the
  prevalence list, and not a listed word plus `-s/-es/-ed/-ing/-er/-ly`.
- `vocab/pseudowords.csv` — `pseudoword, subband, length, accuracy`. ~120 per sub-band; the length distribution
  mirrors that sub-band's alphabetic headwords (clamped to 4–11) so word length gives nothing away. Seeded, so the
  file is reproducible.
