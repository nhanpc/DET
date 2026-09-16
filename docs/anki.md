# Anki: the `DET family` note type

Every deck this repo writes — the Learn-page batch (`vocab/decks/<date>.txt`),
a whole sub-band (`scripts/export_anki.py 4k-a` → `vocab/decks/4k-a.txt`) and
the words met in practice (`--my-words` → `vocab/decks/my-words.txt`) — is one
tab-separated file for Anki's text import, **one note per word family, two
cards per note**:

| Card | Front | Back | DET task it trains |
|------|-------|------|--------------------|
| 1 `Recognise` | the word | forms, definition, example, synonyms | Read and Select |
| 2 `Recall` | definition + the example with the word blanked out (or a hint when no example contains the word) + a typing box | the word, forms, the full example | Read and Complete (cloze), Listen and Type (typed answer, spelling checked by Anki) |

Anki's text import cannot create a note type, so the note type is set up
**once by hand** from this page. After that, importing any deck file from
`vocab/decks/` just works, and re-importing a regenerated file updates the
existing notes instead of duplicating them (`Word` is the first field, which
Anki uses to match notes).

## Fields

`Tools → Manage Note Types → Add → Add: Basic`, name it `DET family`, then
`Fields…` and set exactly these seven, in this order:

| # | Field | Content | Source in the export |
|---|-------|---------|----------------------|
| 1 | `Word` | headword | `family` |
| 2 | `Forms` | family members, comma-separated | `members` |
| 3 | `Definition` | sense 1 definition | `vocab/overrides.csv` → `vocab/index.csv` |
| 4 | `Example` | the example that contains the headword or a member (any of the 3 senses), else sense 1 | `overrides.csv` → `senses.csv` → the my-words `note` |
| 5 | `Gap` | `Example` with the matched word replaced by `_____`; blank when no example contains the word | derived (`learn.gap()`) |
| 6 | `Hint` | first letter + length: `u _ _ _ _` | derived |
| 7 | `Synonyms` | up to 5, comma-separated | `vocab/relations.csv` |

Column 8 of the file is the tag string `<subband> <reason>` (`4k-a new`,
`4k-a missed`, `extra my-words`, …) — an import column, not a field.

## Card templates

`Cards…` on the note type. Rename the first card type to `Recognise` and
add a second one (`Options → Add Card Type…`) named `Recall`. Paste the
templates below; the styling is shared by both.

### Card 1 — `Recognise`

Front template:

```html
<div class="word">{{Word}}</div>
```

Back template:

```html
{{FrontSide}}
<hr id="answer">
{{#Forms}}<div class="forms">{{Forms}}</div>{{/Forms}}
<div class="def">{{Definition}}</div>
{{#Example}}<div class="ex">{{Example}}</div>{{/Example}}
{{#Synonyms}}<div class="syn">= {{Synonyms}}</div>{{/Synonyms}}
```

### Card 2 — `Recall`

Front template — the gapped example when there is one, the hint otherwise,
and a typing box checked against `Word`:

```html
<div class="def">{{Definition}}</div>
{{#Gap}}<div class="ex">{{Gap}}</div>{{/Gap}}
{{^Gap}}<div class="hint">{{Hint}}</div>{{/Gap}}
{{type:Word}}
```

Back template:

```html
<div class="def">{{Definition}}</div>
{{#Gap}}<div class="ex">{{Gap}}</div>{{/Gap}}
{{^Gap}}<div class="hint">{{Hint}}</div>{{/Gap}}
{{type:Word}}
<hr id="answer">
<div class="word">{{Word}}</div>
{{#Forms}}<div class="forms">{{Forms}}</div>{{/Forms}}
{{#Example}}<div class="ex">{{Example}}</div>{{/Example}}
```

`{{type:Word}}` makes Anki show a text box on the front and, on the back,
the typed answer against `Word` with the differences marked, so a spelling
mistake counts as a mistake — the same rule as *Listen and Type*.

### Styling (both cards)

```css
.card { font-family: Georgia, serif; font-size: 22px; text-align: center; color: #1b1f24; background: #fff; padding: 12px; }
.word { font-size: 44px; margin: 12px 0; }
.forms { font-size: 16px; color: #667085; font-style: italic; margin-bottom: 12px; }
.def { font-family: -apple-system, "Segoe UI", sans-serif; font-size: 20px; margin: 10px 0; }
.ex { color: #3d444d; margin: 10px 0; }
.hint { font-family: Menlo, monospace; letter-spacing: .1em; color: #3a6ea5; margin: 10px 0; }
.syn { font-size: 16px; color: #667085; }
hr#answer { border: 0; border-top: 1px solid #e3e6ea; margin: 16px 0; }
input#typeans { font-size: 20px; }
```

## Importing a deck

Needs Anki ≥ 2.1.55 (the file header carries the import settings).

1. Write the file: *Export to Anki* on the Learn page, or from the repo root
   `python3 scripts/export_anki.py 4k-a` (see `scripts/README.md`).
2. Anki: `File → Import…`, pick `vocab/decks/<name>.txt`.
3. The header sets the separator, HTML, the note type `DET family`, the deck
   `DET::<name>` and the tag column; check that the seven columns are mapped
   to the seven fields by name and that *Existing notes* is **Update** (the
   default), then import.
4. `4k-a.txt` (498 notes) gives 996 cards, two per note: `Recall` is never
   empty because `Hint` never is. Importing the same file twice adds nothing
   — the notes are updated in place.

## Making better recall cards

A family has a real cloze card only when some example contains the headword
or one of its forms — 266 of the 498 `4k-a` families from `senses.csv`. The
others fall back to definition + hint. To fix one, add a row to
`vocab/overrides.csv` (`family, definition, example`, written by hand, never
touched by `scripts/build_dict.py`) with a short example that contains the
word; `python3 scripts/export_anki.py 4k-a --check` lists the families still
missing one. The override also replaces the definition on the test page and
the Learn page, so it is the place for a hand-simplified definition.
