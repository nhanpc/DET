# Level-test history

Written by the app (`app/store.py`) while a test runs — nothing to click.
One session = one adaptive yes/no test; the `session` column joins the files.

| File | One row per | Written |
|------|-------------|---------|
| `sessions/<id>.json` | session (every block, item, answer, ms) | after every answer; resumable if unfinished |
| `results.csv` | finished block | at the end of each block |
| `misses.csv` | wrong answer | at the end of each block |
| `levels.csv` | finished session | when the test stops |

## `results.csv`

| column | meaning |
|--------|---------|
| `date` | session date, `YYYY-MM-DD` |
| `session` | session id (`<date>_<time>_<4 hex>`) |
| `subband` | sub-band the block was drawn from |
| `n` | items in the block (10 real + 5 invented) |
| `hits` | real words answered *Yes* |
| `false_alarms` | invented words answered *Yes* |
| `score` | `hits/10 − false_alarms/5`; mastered at ≥ 0.85 |

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
| `level` | highest sub-band with pooled score ≥ 0.85; empty = none |
| `det_low`, `det_high` | estimated DET range for that sub-band |
| `blocks`, `items` | how much was tested |
| `fa_rate` | overall false-alarm rate; > 0.25 → unreliable |
| `reliable` | `1` / `0` |

## `sessions/<id>.json`

`id`, `started`, `finished`, `stop_reason`, `blocks[]` (`no`, `subband`,
`pos` = next item to answer, `hits`, `false_alarms`, `score`, `items[]` with
`word`, `real`, `definition`, `answer`, `ms`) and, once finished, `result`
(the same object the result page shows). An unfinished file is picked up when
the app starts and offered as *Resume* on the start page.
