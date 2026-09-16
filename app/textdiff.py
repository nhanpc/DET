"""Text difficulty on the θ / b scale (GitHub issue nhanpc/DET#15): one `b_text` per sentence or passage, predicted
from its words, length and coverage before anyone answers it — the DET's approach to C-test passages, dictation
sentences and read-aloud items (Settles, LaFlair & Hagiwara 2020), on the item scale of app/irt.py (#14).

Tokens      drills.words() → family through form_index() → irt.b (the Bank's `b` map). Not scored: a capitalised
            token inside a sentence (a name), a token with a digit, and the function half of a contraction
            (`doesn't` → `doesn` is scored through the `do` family, `t` is dropped). A lower-case alphabetic token
            with no family is OFF-LIST. A scored token is a CONTENT word unless its family is a function word —
            first `pos` tag in FUNCTION_POS (the, a, of, who, can, and …) — so the features describe the words
            that carry the meaning, not the grammar around them.
Features    b90     the 90th percentile (nearest rank) of the content words' b: the max up to nine content words,
                    the second-highest from ten — the hardest words decide whether a text is understood
                    (lexical coverage, Nation 2006), one odd word in a passage does not.
            load    content words with b > b90 − 1: one hard word is a lookup, three are a wall.
            length  tokens, counted up to LENGTH_CAP: working memory in dictation, blanks in a C-test. The cap
                    keeps a 50–80-word passage on the scale — past twenty tokens a text is read, not held.
            off     off-list content tokens: unknown to the bank = unknown to the learner.
            zipf    mean SUBTLEX Zipf of the content words: rare-on-average text is harder even when no single
                    word is.
Formula     b_text = b90 + W_LOAD · max(0, load − 1) + W_LENGTH · max(0, min(length, LENGTH_CAP) − LENGTH_FREE)
                     + W_OFF · off + W_ZIPF · max(0, ZIPF_EASY − zipf)
            The weights order sentences on the b scale; they do not claim to predict a DET score.
Refit       refit(): after ≥ MIN_ATTEMPTS own responses an item's b is re-estimated by a Rasch item update —
            Newton steps on the item log-likelihood given the θ at each attempt (bounded, ± MAX_SHIFT) — and
            shrunk toward the prediction with weight MIN_ATTEMPTS / (MIN_ATTEMPTS + n). Stored as `b_adjust`;
            the item's difficulty is b_text + b_adjust.
docs/sentences.md § How difficulty is computed has the worked example.
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import irt
from .bank import PRACTICE
from .drills import _sentence_start, form_index, TOKEN

# ---- the weights (the intent is in the module docstring) --------------------------------------------------
W_LOAD = 0.15          # per content word within one band of the hardest, beyond the first
W_LENGTH = 0.08        # per token beyond LENGTH_FREE
LENGTH_FREE = 8        # a sentence up to 8 tokens costs nothing for length
LENGTH_CAP = 20        # tokens beyond the 20th cost nothing more (+0.96 at most): passages stay on the scale
W_OFF = 0.6            # per off-list content token
W_ZIPF = 0.4           # per Zipf point the content words average below ZIPF_EASY
ZIPF_EASY = 4.5        # SUBTLEX Zipf 4.5 ≈ 30 per million: the everyday-word line
PERCENTILE = 0.9       # b90

# ---- the refit --------------------------------------------------------------------------------------------
MIN_ATTEMPTS = 5       # no refit below this many attempts; also the prior's weight in the shrinkage
MAX_SHIFT = 3.0        # the item likelihood may move b by at most three bands before shrinkage
NEWTON_STEPS = 12
STEP_MAX = 1.0         # one Newton step is bounded to one band (all-correct has no finite maximum)

FUNCTION_POS = {"det", "pron", "prep", "conj", "modal v", "auxiliary v", "definite article", "number", "exclam",
                "infinitive marker"}
CLITICS = {"t", "s", "re", "ve", "ll", "d", "m"}            # the function half of a contraction or a possessive
FEATURE_KEYS = ("b90", "load", "length", "off", "zipf", "top")
CLOZE = PRACTICE / "read-and-complete" / "cloze.csv"
CLOZE_HEADER = ["key", "family", "sense", "subband", "example", "b_text", "b_adjust", "features"]
_DIGIT = re.compile(r"\d")


class Lexicon:
    """What the scorer needs from index.csv: form → family, family → b, Zipf and the function-word flag."""

    def __init__(self, index: dict[str, dict], b: dict[str, float]):
        self.forms = form_index(index)
        self.b = b
        self.zipf: dict[str, Optional[float]] = {f: float(r["zipf"]) if r.get("zipf") else None for f, r in index.items()}
        self.function: set[str] = {f for f, r in index.items() if _is_function(r.get("pos", ""))}

    def family(self, form: str) -> Optional[str]:
        return self.forms.get(form.lower().replace("’", "'"))


def _is_function(pos: str) -> bool:
    return pos.split("|", 1)[0].strip() in FUNCTION_POS


def tokens(text: str, lex: Lexicon) -> list[dict]:
    """One dict per drills.words() token: `kind` = content | function | name | number | contraction | off, and
    `family`, `b` for the scored ones. A capital inside a sentence is a name and is never looked up; a
    sentence-initial capital is looked up like a lower-case word and, unknown, counts as a name too."""
    out = []
    for m in TOKEN.finditer(text):
        w = m.group()
        if _DIGIT.search(w):
            out.append({"word": w, "kind": "number"})
            continue
        if w[0].isupper() and not _sentence_start(text, m.start()):
            out.append({"word": w, "kind": "name"})
            continue
        stem, clitic = w, ""
        if "'" in w or "’" in w:
            stem, _, clitic = w.replace("’", "'").partition("'")
        family = lex.family(stem)
        if family is None and clitic and clitic.lower() not in CLITICS:
            family = lex.family(w)                                    # o'clock: the whole form, when the bank has it
        if family is None:
            out.append({"word": w, "kind": "name" if w[0].isupper() else ("contraction" if clitic else "off")})
            continue
        kind = "function" if family in lex.function else "content"
        out.append({"word": w, "kind": kind, "family": family, "b": lex.b[family]})
    return out


def percentile(values: list[float], q: float = PERCENTILE) -> float:
    """Nearest-rank percentile: the ⌈q · n⌉-th smallest value (the max while n ≤ 9 at q = 0.9)."""
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(1, math.ceil(q * len(s))) - 1]


def features(text: str, lex: Lexicon) -> dict:
    """The raw numbers behind b_text(): b90, load, length, off, zipf, plus `top` (the family at b90), `content`
    (content words) and `n` (scored tokens) for the report."""
    toks = tokens(text, lex)
    content = [t for t in toks if t["kind"] == "content"]
    bs = [t["b"] for t in content]
    b90 = percentile(bs)
    zipfs = [lex.zipf[t["family"]] for t in content if lex.zipf.get(t["family"]) is not None]
    top = next((t["family"] for t in content if t["b"] == b90), "")
    return {"b90": round(b90, 4),
            "load": sum(1 for b in bs if b > b90 - 1),
            "length": len(toks),
            "off": sum(1 for t in toks if t["kind"] == "off"),
            "zipf": round(sum(zipfs) / len(zipfs), 3) if zipfs else ZIPF_EASY,
            "top": top,
            "content": len(content),
            "n": sum(1 for t in toks if "b" in t)}


def combine(f: dict) -> float:
    """The formula on a features() dict."""
    return round(f["b90"] + W_LOAD * max(0, f["load"] - 1) + W_LENGTH * max(0, min(f["length"], LENGTH_CAP) - LENGTH_FREE)
                 + W_OFF * f["off"] + W_ZIPF * max(0.0, ZIPF_EASY - f["zipf"]), 4)


def b_text(text: str, lex: Lexicon) -> float:
    return combine(features(text, lex))


def features_column(f: dict) -> str:
    """`b90=11.66;load=2;length=11;off=0;zipf=4.1;top=evict` — the FEATURE_KEYS as k=v."""
    return ";".join(f"{k}={f[k]}" for k in FEATURE_KEYS)


def parse_features(s: str) -> dict:
    out: dict = {}
    for part in (s or "").split(";"):
        k, _, v = part.partition("=")
        if not _:
            continue
        out[k] = v if k == "top" else (int(v) if v.lstrip("-").isdigit() else float(v))
    return out


def score_rows(rows: Iterable[dict], lex: Lexicon, text_key: str = "sentence") -> list[dict]:
    """Add b_text, features (and b_adjust = 0 when absent) to each row, in place."""
    out = []
    for r in rows:
        f = features(r[text_key], lex)
        r["b_text"] = combine(f)
        r["features"] = features_column(f)
        r.setdefault("b_adjust", 0)
        out.append(r)
    return out


def sentence_bank(senses: Iterable[dict], index: dict[str, dict], lex: Lexicon, path: Optional[Path] = None,
                  rng=None) -> list[dict]:
    """practice/listen-and-type/sentences.csv: loaded, or built from senses.csv (drills.build_sentences), scored
    and written when the file is missing or has the header from before #15."""
    from .drills import build_sentences, load_sentences, write_sentences
    rows = load_sentences(path)
    if not rows:
        rows = score_rows(build_sentences(senses, index, rng), lex)
        write_sentences(rows, path)
    return rows


def b_of(row: dict) -> float:
    """The item's difficulty from a sentences.csv / cloze.csv row or a passage: b_text + b_adjust."""
    return round(float(row.get("b_text") or 0) + float(row.get("b_adjust") or 0), 4)


# ---- own-response refinement ---------------------------------------------------------------------------------

def refit(b_text: float, attempts: list[tuple[float, float]], min_n: int = MIN_ATTEMPTS) -> float:
    """b_adjust from (θ, credit) pairs: 0 below `min_n` attempts; else the Rasch item maximum-likelihood estimate
    by bounded Newton steps from b_text (credit in [0, 1] enters as P^s · (1 − P)^(1 − s), like irt.eap), the
    shift capped at ± MAX_SHIFT, then shrunk toward b_text with weight min_n / (min_n + n):
    b_adjust = (1 − w) · (b_mle − b_text). All right from a θ below the item → negative; all wrong → positive."""
    n = len(attempts)
    if n < min_n:
        return 0.0
    b = b_text
    for _ in range(NEWTON_STEPS):
        ps = [irt.p(theta, b) for theta, _ in attempts]
        grad = sum(s - p_ for (_, s), p_ in zip(attempts, ps))         # ∂ℓ/∂b = −A · Σ (s − P)
        hess = sum(p_ * (1 - p_) for p_ in ps)                         # −∂²ℓ/∂b² = A² · Σ P (1 − P)
        step = grad / (irt.A * hess) if hess > 1e-9 else math.copysign(STEP_MAX, grad)
        step = max(-STEP_MAX, min(STEP_MAX, step))
        b = max(b_text - MAX_SHIFT, min(b_text + MAX_SHIFT, b - step))
        if abs(step) < 1e-4 or abs(b - b_text) >= MAX_SHIFT:
            break
    w = min_n / (min_n + n)
    return round((1 - w) * (b - b_text), 4)


def item_attempts(attempts: Iterable[dict], task: str, key: Callable[[str], str] = lambda item: item) -> dict[str, list[tuple[float, float]]]:
    """attempts.csv rows of `task` → item key → [(θ, credit)]: `theta` is the ability at the time of the attempt
    (written by the drills from #16 on); rows without it, or without a score, are skipped. `key` strips the
    cloze seed (`skip.1.42` → `skip.1`, `<slug>.42` → `<slug>`)."""
    out: dict[str, list[tuple[float, float]]] = {}
    for r in attempts:
        if r.get("task") != task or not r.get("theta") or r.get("score") in (None, ""):
            continue
        out.setdefault(key(r["item"]), []).append((float(r["theta"]), float(r["score"])))
    return out


def strip_seed(item: str) -> str:
    return item.rpartition(".")[0]


def refit_bank(rows: list[dict], attempts: Iterable[dict], task: str, key: Callable[[str], str] = lambda item: item,
               id_key: str = "id") -> int:
    """Set `b_adjust` on every row from its attempts (refit(); 0 without enough of them). Returns how many rows
    changed. The dictation bank: refit_bank(rows, attempts, "listen-and-type"); the cloze pool:
    refit_bank(rows, attempts, "read-and-complete", strip_seed, "key"); passages likewise with id_key="slug"."""
    per = item_attempts(attempts, task, key)
    changed = 0
    for r in rows:
        new = refit(float(r["b_text"]), per.get(r[id_key], []))
        if abs(new - float(r.get("b_adjust") or 0)) > 1e-9:
            r["b_adjust"] = new
            changed += 1
    return changed


# ---- the cloze cache ------------------------------------------------------------------------------------------

def load_cloze(path: Optional[Path] = None) -> list[dict]:
    """practice/read-and-complete/cloze.csv rows; [] when missing or written with another header (→ rebuilt)."""
    path = path or CLOZE
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows if reader.fieldnames == CLOZE_HEADER else []


def write_cloze(rows: list[dict], path: Optional[Path] = None) -> Path:
    path = path or CLOZE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, CLOZE_HEADER, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


# ---- passages ----------------------------------------------------------------------------------------------

def passage_front_matter(path: Path) -> tuple[dict, str]:
    """(meta, body) of a passage file; meta keeps the key order of the file."""
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        for line in head.splitlines():
            k, sep, v = line.partition(":")
            if sep:
                meta[k.strip()] = v.strip()
    else:
        body = text
    return meta, body


def score_passage(path: Path, lex: Lexicon) -> tuple[dict, bool]:
    """Write b_text, b_adjust (kept, default 0) and features into the front matter. Returns (meta, changed)."""
    meta, body = passage_front_matter(path)
    f = features(" ".join(body.split()), lex)
    new = {**meta, "b_text": str(combine(f)), "b_adjust": meta.get("b_adjust") or "0", "features": features_column(f)}
    if new == meta:
        return meta, False
    head = "\n".join(f"{k}: {v}" for k, v in new.items())
    path.write_text(f"---\n{head}\n---\n{body.lstrip(chr(10))}", encoding="utf-8")
    return new, True


def set_passage_adjust(path: Path, b_adjust: float) -> bool:
    """Write a refit `b_adjust` into a passage's front matter (the rest of the file untouched); False when the
    value already stands there."""
    meta, body = passage_front_matter(path)
    value = str(round(float(b_adjust), 4)).rstrip("0").rstrip(".") if float(b_adjust) else "0"
    if meta.get("b_adjust", "0") == value:
        return False
    new = {**meta, "b_adjust": value}
    head = "\n".join(f"{k}: {v}" for k, v in new.items())
    path.write_text(f"---\n{head}\n---\n{body.lstrip(chr(10))}", encoding="utf-8")
    return True


# ---- the report ---------------------------------------------------------------------------------------------

BINS = [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10), (10, 12), (12, 99)]


def histogram(rows: Iterable[dict], order: list[str], key: str = "subband") -> list[dict]:
    """Per source band: n, the count per b_text bin (BINS) and the median — the docs/sentences.md table."""
    per: dict[str, list[float]] = {}
    for r in rows:
        per.setdefault(r[key], []).append(float(r["b_text"]))
    out = []
    for band in order:
        vals = sorted(per.get(band, []))
        n = len(vals)
        median = 0.0 if not n else (vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2)
        out.append({"band": band, "n": n, "median": round(median, 2),
                    "bins": [sum(1 for v in vals if lo <= v < hi) for lo, hi in BINS]})
    return out


def histogram_table(hist: list[dict]) -> str:
    labels = [f"{lo}–{hi}" if hi < 99 else f"≥ {lo}" for lo, hi in BINS]
    lines = ["| Band | Sentences | " + " | ".join(labels) + " | Median b_text |",
             "|---|---|" + "---|" * len(BINS) + "---|"]
    for h in hist:
        lines.append(f"| {h['band']} | {h['n']} | " + " | ".join(str(c) for c in h["bins"]) + f" | {h['median']:.2f} |")
    return "\n".join(lines)
