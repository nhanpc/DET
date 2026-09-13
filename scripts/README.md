# Scripts

Run everything from the repo root.

| Script | Needs | Reads | Writes |
|---|---|---|---|
| `fetch_raw.sh` | curl, gh, unzip | the internet | `data/raw/` (see `data/raw/SOURCES.md`) |
| `extract_raw.py` | openpyxl, pdftotext | `data/raw/` | `data/extract/*.tsv` |
| `build_bands.py` | Python 3.10+ (stdlib) | `data/raw/nation/`, `data/extract/`, `vocab/bands/_index.csv` | `vocab/index.csv`, `vocab/bands/<subband>.csv`, `data/dropped.txt` |
| `audit_data.py` | Python 3.10+ (stdlib) | the above | `data/AUDIT.md` |

Grouping rules and the column meanings are specified in [issue #2](https://github.com/nhanpc/DET/issues/2);
the short version:

- `vocab/bands/_index.csv` is the source of truth for the sub-band cuts (rank ranges, CEFR, DET range, mastery %).
- `rank` = Nation's 1000-band × family frequency count inside the band. `pos_in_band` = order inside the sub-band
  by `zipf` desc, `prevalence` desc, headword asc.
- `zipf`, `prevalence`, `cefr`, `pos` are joined on the headword only; blank means the source has no entry.
- `awl=1` marks Coxhead AWL families wherever they land; the `awl` sub-band holds only the ones outside Nation 1–6K.
