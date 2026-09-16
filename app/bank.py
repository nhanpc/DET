"""Item bank: real words from vocab/index.csv, invented words from vocab/pseudowords.csv.
vocab/overrides.csv (hand-written definition / example, issue #8) is applied on top of the index rows;
vocab/senses.csv gives up to 3 examples per family for the recall cards."""
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


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_subbands(path: Path = VOCAB / "subbands.csv") -> list[Subband]:
    rows = sorted(read_csv(path), key=lambda r: int(r["order"]))
    return [Subband(r["subband"], int(r["order"]), r["cefr"], int(r["det_low"]), int(r["det_high"]),
                    int(r["mastery_pct"]) / 100) for r in rows]


class Bank:
    """Draws words for one sub-band, never repeating a word inside a session."""

    def __init__(self, index_path: Path = VOCAB / "index.csv", pseudo_path: Path = VOCAB / "pseudowords.csv",
                 senses_path: Path = VOCAB / "senses.csv", overrides_path: Path = VOCAB / "overrides.csv"):
        overrides = {r["family"]: r for r in read_csv(overrides_path)} if overrides_path.exists() else {}
        # examples per family: the override first (when it has one), then the senses in order, blanks dropped
        self.examples: dict[str, list[str]] = {f: [o["example"]] for f, o in overrides.items() if o["example"]}
        if senses_path.exists():
            for r in read_csv(senses_path):
                if r["example"]:
                    self.examples.setdefault(r["family"], []).append(r["example"])
        self.real: dict[str, list[RealWord]] = {}
        self.index: dict[str, dict] = {}
        for r in read_csv(index_path):
            o = overrides.get(r["family"])
            if o:
                r["definition"] = o["definition"] or r["definition"]
                r["example"] = o["example"] or r["example"]
            self.index[r["family"]] = r
            if WORD.match(r["family"]):
                self.real.setdefault(r["subband"], []).append(RealWord(r["family"], r["subband"], r["definition"]))
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
