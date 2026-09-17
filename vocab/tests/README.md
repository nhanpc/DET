# Level-test history

Written by the app (`app/store.py`) while a test runs — nothing to click —
and committed to git at the end of every block and of the test (`app/vcs.py`,
issue #20), plus one file, `mocks.csv`, typed by hand after each full DET
practice test (not committed by the app).
One session = one adaptive yes/no test; the `session` column joins the files.

| File | One row per | Written |
|------|-------------|---------|
| `sessions/<id>.json` | session (every block, item, answer, ms, the item's `b` and θ after each block) | after every answer; resumable if unfinished |
| `results.csv` | finished block | at the end of each block |
| `misses.csv` | wrong answer | at the end of each block |
| `levels.csv` | finished session | when the test stops |
| `mocks.csv` | full DET practice test | by hand, after the test (issue #11 owns the schema; `scripts/report.py` renders it) |

## `results.csv`

| column | meaning |
|--------|---------|
| `date` | session date, `YYYY-MM-DD` |
| `session` | session id (`<date>_<time>_<4 hex>`) |
| `subband` | the sub-band containing θ when the block was drawn (its label; since #14 the 10 real words come from `\|b − θ\| ≤ 1`, so up to three sub-bands — the invented words are from this one) |
| `n` | items in the block (10 real + 5 invented) |
| `hits` | real words answered *Yes* |
| `false_alarms` | invented words answered *Yes* |
| `score` | `hits/10 − false_alarms/5`; mastered at ≥ 0.85 (the block view; the level comes from θ) |

## `misses.csv`

| column | meaning |
|--------|---------|
| `date`, `session`, `subband` | as above |
| `word` | the item shown |
| `kind` | `miss` = real word answered *No* (a word to learn); `false_alarm` = invented word answered *Yes* |
| `ms` | answer time in milliseconds |

Only the word is stored; join `kind = miss` rows to `vocab/index.csv` on
`family` for rank, definition, members and so on.

## `levels.csv`

| column | meaning |
|--------|---------|
| `date`, `session` | as above |
| `level` | the sub-band containing θ − 1.16, where 85 % of the words are known (rows from before #14: highest sub-band with pooled score ≥ 0.85); empty = none |
| `det_low`, `det_high` | that sub-band's DET range from `vocab/subbands.csv` |
| `blocks`, `items` | how much was tested |
| `fa_rate` | overall false-alarm rate; > 0.25 → unreliable |
| `reliable` | `1` / `0` |
| `theta`, `se` | ability θ and its standard error at the end of the session ([docs/det-adaptive.md](../../docs/det-adaptive.md)); blank on rows written before #14 — the report replays those sessions to get their θ. The header gains the two columns the first time the app writes a row after the upgrade; older rows are padded with blanks |

## `mocks.csv`

Hand-typed, one row per full DET practice test (the baseline from issue #7
is row 1; the rules and the report that renders it are issue #11). The
header is `app/store.py:MOCKS_HEADER`; `scripts/report.py` validates every
row when it runs and stops with the line number on a bad one.

| column | meaning |
|--------|---------|
| `date` | test date, `YYYY-MM-DD` |
| `source` | `det-practice` (the official free practice test), `official` (a certified test), or the name of a third-party mock |
| `overall` | 10–160, steps of 5; required |
| `literacy`, `comprehension`, `conversation`, `production` | subscores, same scale; blank when the source gives none |
| `weakest` | one of the four subscore names, from the self-review after a test with no subscores: which tasks were slow or guessed; blank otherwise |
| `notes` | free text: what went wrong, tasks that felt slow, the full range the practice test reported |

The free practice test reports only an estimated **range** for the overall
score and no subscores: record the **low end** as `overall` (the booking rule
must not pass on the optimistic end), leave the four subscores blank, fill
`weakest` from the self-review and put the full range in `notes`. Retaking
the test on the same day gets a second row with the same date; the report
keeps the **last row per date and `source`**, so the retake replaces the
first attempt and an `official` row never replaces a `det-practice` row
taken the same day.

Subscores come only from `official` rows and third-party mocks that report
them. The certified test reports eight (Reading, Writing, Listening,
Speaking and the four integrated ones): store only the integrated four —
the focus rule needs one weakest subscore — and put the skill scores in
`notes`. An `official` row ≥ 120 means the goal is reached; below 120 it is
shown like any other row but left out of the booking verdict, which runs on
practice rows only (README § *Weekly cycle* has the three rules).

Validation (`app/progress.py:parse_mocks()`): `date` is `YYYY-MM-DD`,
`source` is non-empty, `overall` is required and a multiple of 5 in 10–160,
the four subscores are blank or the same, `weakest` is blank or one of
`literacy`, `comprehension`, `conversation`, `production`. Nothing is
skipped: a bad row fails the report (and `GET /api/progress`) with its line
number, the header being line 1.

## `sessions/<id>.json`

`id`, `started`, `finished`, `stop_reason`, `theta0` (the prior mean: the
last reliable session's θ, 6.0 before the first), `blocks[]` (`no`,
`subband`, `pos` = next item to answer, `hits`, `false_alarms`, `score`,
`theta_from` = the θ the block was drawn around, `theta` and `se` after its
last answer, `items[]` with `word`, `real`, `definition`, `answer`, `ms`, `b`
= the word's difficulty, `null` for an invented word) and, once finished,
`result` (the same object the result page shows: `level`, `frontier`,
`theta`, `se`, `det_estimate`, `det_range`, the pooled block scores, the
path, the misses). Files from before #14 have no `theta0`, `b`, `theta_from`,
`theta` or `se`; they load, and `learn.theta_history()` replays them through
the same estimator with the bank's `b`. An unfinished file is picked up when
the app starts and offered as *Resume* on the start page.
