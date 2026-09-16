# The DET format

The Duolingo English Test is one adaptive test of about one hour, taken at home:
roughly 45 minutes of graded questions, then a 10-minute *Writing Sample* and
*Speaking Sample* that are sent to institutions with the score, plus a 5-minute
introduction and ID check. Tasks are **mixed, not grouped in sections**; the
difficulty of the next item follows your answers.

Written for the Phase 5 drills (issue #10): the timings in the table below are
what `app/drills.py:TASKS` runs, taken from Duolingo's public test guide and
checked against it on 2026-09-16. See the note on the **July 2025 update** at the
end — two of the drilled tasks are no longer on the real test.

## Scores

| Item | Value |
|---|---|
| Overall | 10–160, in steps of 5 |
| Subscores | Literacy (reading + writing), Comprehension (reading + listening), Conversation (listening + speaking), Production (writing + speaking); since July 2024 also Reading, Writing, Listening, Speaking |
| Target here | **120** = the first C1 score (README) |

## Task types and timings

Skill columns: R reading, L listening, W writing, S speaking. *Drill* is the
`task` id in `practice/attempts.csv` and the `#drill/<task>` screen.

| Task | Skills | Prep | Answer | Minimum | Items per test | Drill |
|---|---|---|---|---|---|---|
| Read and Select — is it a real English word? | R | – | 5 s per word, ~1 min per set | – | several sets of 15–18 words | the weekly level test (`app/adaptive.py`, issue #4) |
| Read and Complete — a C-test passage: first letters given, complete the words | R | – | 3 min per passage | – | 3–6 passages | `read-and-complete` (passage mode 3 min, the default; sentence mode 1 min) |
| Fill in the Blanks — one sentence, complete the missing word | R | – | 20 s per sentence | – | 6–9 sentences | `fill-in-the-blanks` (issue #17: the first third of the word given, 20 s) |
| Listen and Type — type the sentence you hear | L | – | 1 min per sentence, audio ≤ 3 plays | – | 6–9 sentences | `listen-and-type` |
| Listen and Complete — fill the blanks of an audio scenario | L | – | ~3 min per passage | – | 3–4 questions per Interactive Listening set | not drilled (notes in `practice/interactive/README.md`) |
| Interactive Reading — a passage with several question types | R | – | 7–8 min per set | – | 2 sets | not drilled |
| Interactive Listening — a conversation, then a summary | L, W | – | 6 min 30 s for the questions, 75 s for the summary | – | 2 sets | not drilled |
| Read Aloud — read a sentence into the microphone | S | – | 20 s | – | *removed July 2025* | `read-aloud` (kept: pronunciation practice on the dictation bank) |
| Speak About the Photo | S | 20 s | 30–90 s | 30 s (dropped in 2025) | 1 | `speak-photo` |
| Read Then Speak — speak on a written prompt | S | 20 s | 30–90 s | 30 s (dropped in 2025) | 1 | `read-then-speak` |
| Listen Then Speak — speak on a spoken prompt | L, S | 20 s | 30–90 s | 30 s | *removed July 2025* | `listen-then-speak` (kept: the nearest drill to Interactive Speaking) |
| Interactive Speaking — 6–8 questions in a simulated conversation | L, S | – | 35 s per answer | – | 2 sets | not drilled yet; use `listen-then-speak` with a short timer |
| Write About the Photo | W | – | 1 min | – | 3 photos | `write-photo` |
| Read Then Write — write on a written prompt | W | – | 5 min | 50 words | *removed July 2025* | `read-then-write` (kept: the shape of the Writing Sample) |
| Interactive Writing — part 1, then a follow-up on what you wrote | W | – | 5 min + 3 min | 50 words | 1 set | `interactive-writing` |
| Writing Sample — ungraded, sent to institutions | W | 30 s | 5 min | – | 1 | use `read-then-write` |
| Speaking Sample — ungraded, sent to institutions | S | 30 s | 3 min | – | 1 | use `read-then-speak` with the long timer |

The drills keep the 30-second speaking minimum as a target even though the
real test no longer enforces it: 30 seconds is the shortest answer that can
show a range of vocabulary and grammar.

## How the graded tasks are scored

- **Read and Select**, **Read and Complete**, **Fill in the Blanks**, **Listen
  and Type**, **Listen and Complete**: automatic, per word or per blank — a
  spelling mistake is a wrong answer, so the drills score them the same way
  (cloze: exact match per blank; dictation: word-level edit distance, see
  `practice/README.md`).
- **Writing** and **speaking** tasks: machine-graded on four dimensions that
  the drills mirror as the four self-rating lines — task fulfilment (did you
  answer the prompt, at length), fluency (pace, pauses / sentence flow),
  vocabulary range and accuracy, grammar range and accuracy. The drill stores
  the mean of the four lines as one 1–5 number in `attempts.csv`.
- Nothing is deducted for guessing; leaving an item blank scores the same as a
  wrong answer, so the drills submit whatever is there when the clock runs
  out.

## The July 2025 update

On 1 July 2025 Duolingo replaced *Listen Then Speak* with **Interactive
Speaking** (6–8 questions, 35 s each, chosen from your earlier answers),
dropped **Read Aloud** to keep the test length, added **Listen and Complete**
at the start of each Interactive Listening set, and removed the minimum
speaking time from *Speak About the Photo* and *Read Then Speak*. Third-party
guides list **Fill in the Blanks** (20 s per sentence) among the reading tasks
and no longer list *Read Then Write* or *Listen and Select*.

Issue #10 fixed the drill ids before this was checked, and issue #11's
weakest-subscore review names them, so the drills keep the old ids: they still
train the underlying skills (reading a sentence aloud, answering a spoken
prompt, writing 50 words on a prompt in five minutes). When a drill for
Interactive Speaking or Listen and Complete is added, it gets its own `task`
id; nothing in `attempts.csv` needs to change.

Sources: Duolingo's test guide (`englishtest.duolingo.com`, *Test format*) and
its test-taker update notes for July 2025; third-party summaries for the
per-item counts.
