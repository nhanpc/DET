"""Item bank: real words from vocab/index.csv, invented words from vocab/pseudowords.csv.
vocab/overrides.csv (hand-written definition / example, issue #8) is applied on top of the index rows;
vocab/senses.csv gives up to 3 examples per family for the recall cards. Every family gets its difficulty
`b` on the scale of issue #14 (app/irt.py): the sub-band's order − 1 + pos_in_band / 500 (+ b_adjust)."""
from __future__ import annotations

import csv
import random
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOCAB = ROOT / "vocab"
PRACTICE = ROOT / "practice"      # the task drills (issue #10): attempts.csv, prompts, drafts, recordings

WORD = re.compile(r"^[a-z]{3,}$")   # headwords shown in the test: plain lower-case, 3+ letters


@dataclass(frozen=True)
class Subband:
    name: str
    order: int
    cefr: str
    det_low: int
    det_high: int
    mastery: float          # 0.85


@dataclass(frozen=True)
class RealWord:
    word: str
    subband: str
    definition: str
    b: float = 0.0          # difficulty on the θ scale (irt.b)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_subbands(path: Path = VOCAB / "subbands.csv") -> list[Subband]:
    rows = sorted(read_csv(path), key=lambda r: int(r["order"]))
    return [Subband(r["subband"], int(r["order"]), r["cefr"], int(r["det_low"]), int(r["det_high"]),
                    int(r["mastery_pct"]) / 100) for r in rows]


def word_b(row: dict, orders: dict[str, int]) -> float:
    """irt.b() of one index.csv row: scale rank from the sub-band's order and pos_in_band, plus the optional
    b_adjust column (a later refit on responses; blank or missing = 0)."""
    from . import irt                                   # irt imports Subband from here
    adjust = float(row["b_adjust"]) if row.get("b_adjust") else 0.0
    return round(irt.b(irt.scale_rank(orders[row["subband"]], int(row["pos_in_band"])), adjust), 4)


class Bank:
    """Draws words for the test: by sub-band (has/draw_real, the pooled view) or around an ability θ on the
    scale (in_window), never repeating a word inside a session."""

    def __init__(self, index_path: Path = VOCAB / "index.csv", pseudo_path: Path = VOCAB / "pseudowords.csv",
                 senses_path: Path = VOCAB / "senses.csv", overrides_path: Path = VOCAB / "overrides.csv",
                 subbands_path: Path = VOCAB / "subbands.csv"):
        orders = {sb.name: sb.order for sb in load_subbands(subbands_path)}
        overrides = {r["family"]: r for r in read_csv(overrides_path)} if overrides_path.exists() else {}
        # examples per family: the override first (when it has one), then the senses in order, blanks dropped
        self.examples: dict[str, list[str]] = {f: [o["example"]] for f, o in overrides.items() if o["example"]}
        if senses_path.exists():
            for r in read_csv(senses_path):
                if r["example"]:
                    self.examples.setdefault(r["family"], []).append(r["example"])
        self.real: dict[str, list[RealWord]] = {}
        self.words: list[RealWord] = []                # every word the test can show, by b
        self.b: dict[str, float] = {}                  # family → difficulty, every family of index.csv
        self.index: dict[str, dict] = {}
        for r in read_csv(index_path):
            o = overrides.get(r["family"])
            if o:
                r["definition"] = o["definition"] or r["definition"]
                r["example"] = o["example"] or r["example"]
            self.index[r["family"]] = r
            b = word_b(r, orders)
            self.b[r["family"]] = b
            if WORD.match(r["family"]):
                w = RealWord(r["family"], r["subband"], r["definition"], b)
                self.real.setdefault(r["subband"], []).append(w)
                self.words.append(w)
        self.words.sort(key=lambda w: w.b)
        self.pseudo: dict[str, list[str]] = {}
        for r in read_csv(pseudo_path):
            self.pseudo.setdefault(r["subband"], []).append(r["pseudoword"])

    def has(self, subband: str, n_real: int, n_pseudo: int, used: set[str]) -> bool:
        real = [w for w in self.real.get(subband, []) if w.word not in used]
        pseudo = [p for p in self.pseudo.get(subband, []) if p not in used]
        return len(real) >= n_real and len(pseudo) >= n_pseudo

    def draw_real(self, subband: str, n: int, used: set[str], rng: random.Random) -> list[RealWord]:
        pool = [w for w in self.real[subband] if w.word not in used]
        return rng.sample(pool, n)

    def draw_pseudo(self, subband: str, n: int, used: set[str], rng: random.Random) -> list[str]:
        pool = [p for p in self.pseudo[subband] if p not in used]
        return rng.sample(pool, n)

    def has_pseudo(self, subband: str, n: int, used: set[str]) -> bool:
        return sum(1 for p in self.pseudo.get(subband, []) if p not in used) >= n

    def in_window(self, theta: float, width: float, used: set[str]) -> list[RealWord]:
        """The unseen words with |b − θ| ≤ width, by b."""
        return [w for w in self.words if abs(w.b - theta) <= width and w.word not in used]
