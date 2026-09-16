# Progress

Target: DET **120** by **2027-03-03** (not booked). Set on 2026-09-16, see #7.

Everything above the first HTML-comment marker is hand-written. Everything
between the two markers belongs to `scripts/report.py` (Phase 4, #9) and is
rewritten from `vocab/tests/`; do not edit it by hand. Mock scores live in
`vocab/tests/mocks.csv` (#11), not here.

## Baseline

### DET practice test

_TODO: take the free DET practice test (englishtest.duolingo.com → Practice
test, test conditions, one sitting), type it as row 1 of
`vocab/tests/mocks.csv` (`source = det-practice`, `overall` = low end of the
range, subscores blank, `weakest` from the self-review, full range in
`notes`), then replace this paragraph with:_

Row 1 of `vocab/tests/mocks.csv`: `YYYY-MM-DD`, `det-practice`, overall `__`
(range `__–__`), weakest: `__`. The generated block below charts it.

### Outside vocabulary estimates

_TODO: take LexTALE (lextale.com, 60 items, ~4 min; required) and optionally
testyourvocab.com, then fill in the rows. LexTALE bands: 80–100 % ≈ C1–C2,
60–80 % ≈ B2, < 60 % ≈ B1 and below. LexTALE % ≡ `50 + 50 × score` on our
block scale, so our 0.85 mastery threshold ≡ 92.5 % and a LexTALE result
converts back as `(pct − 50) / 50`._

| date | test | result | notes |
|------|------|--------|-------|
| `YYYY-MM-DD` | LexTALE | `__ %` | _TODO: band (C1–C2 / B2 / ≤ B1)_ |
| `YYYY-MM-DD` | testyourvocab.com | `__ words` | optional; counts words, not families |

### Our level test

Rows copied from `vocab/tests/levels.csv` as stored (same columns and values).

| date | session | level | det_low | det_high | blocks | items | fa_rate | reliable |
|------|---------|-------|---------|----------|--------|-------|---------|----------|
| 2026-09-15 | 2026-09-15_214411_04cc | 3k-b | 60 | 85 | 5 | 75 | 0.0 | 1 |
| 2026-09-15 | 2026-09-15_220053_d7f7 | 3k-b | 60 | 85 | 3 | 45 | 0.0667 | 1 |
| 2026-09-15 | 2026-09-15_220243_d33a | 4k-a | 90 | 105 | 3 | 45 | 0.0667 | 1 |

_TODO: all three runs are from one evening. Take one more reliable session
on a later day (expected `3k-b`, `4k-a` or an adjacent sub-band), append its
`levels.csv` row here and refresh the pooled table below from `GET /api/learn`
(4 dp), since a fourth session changes every pooled score._

Pooled (What to learn, 2026-09-16): level **3k-b**, frontier **4k-a**,
trend up. Words: 73 known / 20 missed / 15 shaky.

| sub-band | blocks | pooled score | status |
|----------|--------|--------------|--------|
| 3k-a | 1 | 0.9 | mastered |
| 3k-b | 3 | 0.925 | mastered |
| 4k-a | 6 | 0.7857 | not yet |
| 4k-b | 1 | 0.6 | not yet |

### Compare

_TODO, two or three sentences once the numbers above are in: does the
practice DET range overlap our `det_low–det_high` for `3k-b`/`4k-a`
(60–105)? Does LexTALE's band agree with B1/B2? If the practice score is
clearly above our range, vocabulary is not the bottleneck and the mapping in
`vocab/subbands.csv` is conservative; if below, the other skills are, and the
task drills (#10) should start earlier than planned._

<!-- generated:start -->
_Nothing yet: `scripts/report.py` (Phase 4) writes here._
<!-- generated:end -->
