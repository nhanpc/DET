# Task drills

Every DET task type as a timed drill inside the app (issue #10, Phase 5):
open `http://localhost:8000`, pick a task under *Practise the tasks*, or go
straight to `#drill/<task>`. The real DET clock runs
([docs/det-format.md](../docs/det-format.md)); every attempt is one row of
`attempts.csv`, every draft and recording is kept under its attempt id, and
every word you got wrong or lacked lands in `vocab/my-words.csv` through
`learn.add_my_word()` so *What to learn* and the Anki export pick it up.

| `task` | Drill | Items from | Clock | Scored |
|---|---|---|---|---|
| `read-and-complete` | cloze: complete the damaged words (first letters given) | *sentence mode*: `vocab/senses.csv` examples of the frontier sub-band and the study list; *passage mode*: `read-and-complete/passages/*.md` | 1 min / 3 min | correct blanks ÷ blanks |
| `listen-and-type` | dictation: play ≤ 3 times, type the sentence | `listen-and-type/sentences.csv`, sentences with `b` near `θ` (frontier sub-band before the first test), edge-tts audio | 1 min | credit = 1 − character edits ÷ length; moves `θ` |
| `read-aloud` | read the sentence; record | the same sentence bank | 20 s | self-rating |
| `speak-photo` | describe a photo; record | `speaking/photos/` (any image; a `prompts.csv` row can give it a prompt) | 20 s prep, 30–90 s | self-rating |
| `read-then-speak` | speak on a written prompt; record | `speaking/prompts.csv` | 20 s prep, 30–90 s | self-rating |
| `listen-then-speak` | the prompt is read aloud (≤ 3 plays); record | `speaking/prompts.csv`, edge-tts | 20 s prep, 30–90 s | self-rating |
| `write-photo` | one or more sentences about a photo | `speaking/photos/` | 1 min | self-rating, words |
| `read-then-write` | write on a prompt | `writing/prompts.csv` | 5 min, 50 words | self-rating, words |
| `interactive-writing` | part 1, then the row's `follow_up` on what you wrote | `writing/prompts.csv` | 5 min + 3 min, 50 words | self-rating, words |

A sentence's `subband` is the band of the word it was fetched for, nothing
more; its difficulty is `b_text` (issue #15, `app/textdiff.py`) — predicted
from every word's `b`, the length and the off-list count on the same scale as
the learner's `θ`, stored next to the sentence in `sentences.csv` and
`cloze.csv` and in a passage's front matter, and returned as `b` with every
drill item. How the two relate is measured in
[docs/sentences.md](../docs/sentences.md), which also has the formula.

Read and Select is not drilled again: the weekly level test is that task.
Interactive Reading / Listening and the Samples: notes only, in
[interactive/README.md](interactive/README.md).

## The loop

```mermaid
flowchart LR
    F["Frontier sub-band<br/>learn.frontier · θ window (dictation)"] --> G[Item generator<br/>app/drills.py]
    S[(senses.csv<br/>index.csv members)] --> G
    P[(prompts.csv<br/>passages/, photos/)] --> G
    G --> D[Timed drill screen<br/>#drill/task]
    D -->|"every attempt"| A[(attempts.csv)]
    D -->|"drafts, recordings"| K[(writing/drafts<br/>speaking/recordings)]
    D -->|"wrong or lacked words → family<br/>learn.add_my_word(source = task)"| M[(vocab/my-words.csv)]
    M --> L[What to learn<br/>reason = my-words] --> X[Anki export<br/>tag = subband my-words]
```

## How each drill runs

**Read and Complete.** C-test damage, the rule of the real task: a word is
*eligible* when it is letters only, 3+ letters, not a capitalised word inside
a sentence (a name) and not a number; every second eligible word loses its
second half (`len // 2` letters kept, at least one: `skipped → ski____`,
`the → t__`). Sentence mode picks the parity that damages the target family's
form (headword or `members`), so the item always tests that family; passage
mode keeps the first sentence intact and damages the 2nd, 4th, … eligible
word after it. 2–5 blanks: an item that yields fewer is skipped, more are cut
to a window of 5 that contains the target, the window offset drawn from the
seed. Items are not stored — the id (`<family>.<sense>.<seed>` or
`<passage-slug>.<seed>`) regenerates them, and "not shown twice in a week" is
a lookup on the `item` column. Answer: exact match per blank, letters only,
case-insensitive. A wrong blank whose word maps to an `index.csv` family goes
to my-words with the sentence as the note (a proper noun in a passage is
logged in `attempts.csv` only).

Passages: paste 50–80 words into `read-and-complete/passages/<slug>.md`
(Simple English Wikipedia, CC BY-SA, is a good source) with a front-matter
block giving the `source:` URL and the `licence:`, then run
`.venv/bin/python scripts/passages.py score`, which writes `b_text:`,
`b_adjust:` (0 until own responses refit it) and `features:` into the same
block; `scripts/passages.py check` fails on a passage without source,
licence or a current `b_text`, or with fewer than 6 blanks. Three examples
are committed. Sentence-mode candidates are cached the same way in
`read-and-complete/cloze.csv` (gitignored, rebuilt on the first request).

**Listen and Type** (issue #16 — the drill on the DET scale of
[docs/det-adaptive.md](../docs/det-adaptive.md) § *Dictation*).
`listen-and-type/sentences.csv` (`id, family, subband, sentence, voice,
b_text, b_adjust, features`) is built from `senses.csv` on the first request
— one 6–14-word example per family whose text contains a form of the family,
one of four edge-tts accents per row, its `b_text` and the features behind it
as `k=v;…` — and is not committed, so the voices are local to the machine; a
file with an older header is rebuilt the same way. The MP3 is generated once
(needs the internet) and cached in `listen-and-type/audio/`; the second play
needs no network.

```mermaid
flowchart LR
    TH["θ, se<br/>last test + drills"] --> P["pool: |b − θ| ≤ 0.6<br/>widened by 0.3 until 10<br/>not shown in 7 days"]
    P --> S[sentence] --> V["edge-tts · ≤ 3 plays"] --> Y[typed]
    Y --> D["character-level<br/>edit distance"]
    D --> C["credit 0 … 1"] --> U["θ update<br/>fractional Rasch"]
    Y --> W["word-level diff<br/>difflib opcodes"]
    W --> E["events per content word<br/>hit · form · hearing · spelling · vocabulary"]
    E --> M["word_stats merge<br/>my-words · source task:kind"]
    C & E --> A[("attempts.csv<br/>score, theta, b, events")]
```

- **Selection.** After the first reliable test the item is drawn from the
  sentences whose `b` (`b_text + b_adjust`) lies within `±0.6` of the current
  `θ`, the window widened by `0.3` until it holds ten candidates; items shown
  in the last 7 days are skipped first. Before a test the pool is the
  frontier sub-band, as before. Read Aloud draws from the same pool.
- **Credit.** Both strings lower-cased, punctuation stripped, whitespace
  collapsed; `credit = 1 − d ÷ max(len(reference), len(typed))` with `d` the
  character-level Levenshtein distance — the DET's edit-distance similarity
  rather than all-or-nothing. Identical → 1; one letter off in 40 characters
  → 0.975; nothing typed → 0. This is the `score` column. The word-level
  diff (`difflib.SequenceMatcher` on the word lists: `delete` = missing
  reference words, `insert` = extra typed words, `replace` = the longer span)
  stays for the result screen and the `errors` column.
- **θ.** The row stores `theta` (the ability *before* the attempt) and `b`
  (the sentence's difficulty). The credit enters the learner's posterior as
  `P^s · (1 − P)^(1 − s)` after `irt.snap()`: `s ≥ 0.95` counts as right,
  `s ≤ 0.2` as wrong, anything between as the fraction it is. The result
  screen shows `θ` before → after. `θ_listen` — the same update from the
  dictation attempts only — sits next to the vocabulary `θ` in the report.
- **Events.** Every content word of the reference (a family that is not a
  function word and that the test can show) is one event, `family:kind`,
  stored `|`-separated in `events`:

  | Word-level opcode | Condition | Kind |
  |---|---|---|
  | `equal` | — | `hit` |
  | `replace` | the typed word is another member of the same family (`evicts` → `evict`) | `form` |
  | `replace` | the typed word is a *different word of the bank* with the same Metaphone key (`ward` → `word`) or within 2 letter edits (`went` → `rent`) | `hearing` |
  | `replace` | the typed form is unknown to the bank but has the reference's sound (`tennant` → `tenant`) or is within 2 edits | `spelling` |
  | `replace` | otherwise (`avoid` → `evict`) | `vocabulary` |
  | `delete` | the reference word's `b ≥ θ − 1` (a word the learner is not expected to know yet) | `vocabulary` |
  | `delete` | `b < θ − 1` (an easy word that was not caught) | `hearing` |
  | `insert` | — | not an event |

  The Metaphone key comes from `app/phon.py`, ~60 lines of the classic
  algorithm; the bank lookup is what tells `hearing` from `spelling`, since
  `ward`/`word` and `tennant`/`tenant` each share one key.
- **What the events do.** `learn.word_stats()` merges them with the test
  history in time order: a `vocabulary` miss counts like a "no" in the test
  (`missed`, then `repeat`); a `hit` on two different days with no later
  miss counts as a "yes" (`known`, or `learned` after an earlier miss);
  `form`, `hearing` and `spelling` never touch the vocabulary status — they
  set a `skill` note on the word. On the my-words side a `vocabulary` miss
  is added under `source = listen-and-type` as before, a skill slip under
  `source = listen-and-type:<kind>`; the Learn page lists those as *heard
  wrong* instead of *my-words*, and neither the Learn button nor
  `scripts/export_anki.py` turns them into cards (a spelling slip is a
  dictation matter, not a card).
- **Refit.** After every answer `textdiff.refit_bank()` re-estimates each
  sentence's `b_adjust` from the `(theta, score)` pairs on record (≥ 5
  attempts per sentence) and rewrites `sentences.csv` when something moved.

Both vocabulary drills map a wrong word to its family through `index.csv`
headwords and `members`; families the app never shows (two-letter ones such
as `be`, `a`, `to` — the same `bank.WORD` rule as the test) are logged in
`attempts.csv` but not added, so a blank dictation answer does not push
function words onto the study list.

**Speaking.** Prompt (or photo, or the prompt read by edge-tts), a 20 s
preparation countdown, then the browser's `MediaRecorder` starts by itself;
*Stop* unlocks after the 30 s minimum and the clock stops it at 90 s. The
recording is uploaded as `speaking/recordings/<attempt>.webm` and played back
next to the prompt on the result screen. Read Aloud has no preparation:
*Record* starts the 20 s clock on a sentence from the dictation bank.

**Writing.** Prompt (or photo), a textarea with a live word count against the
50-word minimum, a countdown; the draft is autosaved every 10 s and at the
end to `writing/drafts/<attempt>.md` (front matter `task, prompt, seconds,
words`, then the text under its prompt). Interactive Writing runs part 2 on
the `follow_up` prompt after part 1; one row, one draft file with two
sections, `seconds` and `words` summed. A second attempt at the same prompt
is a new file — nothing is overwritten.

**Self-rating and "words I lacked"** (speaking and writing): four lines, 1–5
each — task fulfilled, fluency, vocabulary range, grammar — stored as their
mean rounded half up in the `self` column. The *words I lacked* box takes a
comma-separated list; each word is looked up in `index.csv` (headword or
member) and added with `source = <task>` and `note = <prompt id>`; a word
outside the list is added under its own spelling and becomes an *extra* entry
on the study list (issue #8).

The start page shows today's attempts per task. Routine: one speaking and one
writing drill a day; cloze and dictation three times a week, 10 items each —
cloze from the frontier sub-band, dictation from the sentences near `θ`.

## `attempts.csv`

One row per finished or timed-out attempt, every task type. Written by
`app/store.py:append_attempt()`.

| column | meaning |
|---|---|
| `date` | `YYYY-MM-DD` |
| `attempt` | `<date>_<HHMMSS>_<4 hex>` — the same shape as a test session id; also the recording / draft file name |
| `task` | one of the ids in the table above |
| `item` | cloze id (`<family>.<sense>.<seed>` or `<slug>.<seed>`, replayable), sentence id (`<family>.<sense>`) or prompt id |
| `subband` | the sub-band the item targets; blank for passages and prompts |
| `seconds` | time used, both parts summed for Interactive Writing |
| `timed_out` | `1` when the clock ran out |
| `score` | 0–1 for `read-and-complete` (correct blanks ÷ blanks) and `listen-and-type` (the credit, since #16); blank otherwise |
| `self` | 1–5 self-rating for speaking and writing; blank otherwise |
| `words` | blanks (cloze), reference words (dictation), words written (writing); blank for speaking |
| `errors` | `;`-separated `expected>typed` pairs (`sentence>sentance`, `so>` for a missing word, `>extra` for an extra one); empty when none |
| `file` | draft or recording path relative to `practice/`; blank for cloze and dictation |
| `theta` | the learner's `θ` *before* the attempt (dictation, since #16); blank before the first reliable test and on older rows |
| `b` | the item's difficulty (`b_text + b_adjust`) at the time; blank on older rows |
| `events` | `family:kind|family:kind` — one per content word of a dictation sentence (`hit`, `form`, `hearing`, `spelling`, `vocabulary`); blank on older rows |

Rows written before #16 have no `theta`, `b` or `events`: they load with
blanks, the header is upgraded on the next write, and only rows with a `b`
feed `θ`.

## `speaking/prompts.csv` and `writing/prompts.csv`

| column | meaning |
|---|---|
| `id` | prompt id, the `item` of the attempt (`rts-01`, `lts-03`, `iw-02`, …) |
| `task` | the drill the row belongs to |
| `prompt` | the text shown (or read aloud for `listen-then-speak`) |
| `follow_up` | writing only: the part-2 prompt of an `interactive-writing` row; blank elsewhere |
| `photo` | file name in `speaking/photos/` for the photo tasks; blank elsewhere |
| `source` | where the prompt came from (`own`, `own photo`, a URL) |

Photo tasks draw from every image in `speaking/photos/` (jpg, png, webp);
a row is only needed to give a photo a prompt of its own. Photos, recordings,
the sentence cache and the audio cache are gitignored; drafts, prompts and
passages are committed.
