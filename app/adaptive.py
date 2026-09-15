"""The staircase: pure logic, no I/O. Rules from GitHub issue nhanpc/DET#4.

One block = REAL_PER_BLOCK real words from the current sub-band + PSEUDO_PER_BLOCK invented
words, shuffled. Block score = hits/real − false_alarms/pseudo (LexTALE / Meara correction).
Score ≥ mastery → move up one sub-band, else down. Stop on the second direction reversal,
after MAX_BLOCKS blocks, when the walk is clamped at an end, or when the bank runs dry.
Level = highest sub-band whose pooled score over the session is ≥ mastery.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .bank import Bank, RealWord, Subband

REAL_PER_BLOCK = 10
PSEUDO_PER_BLOCK = 5
MAX_BLOCKS = 6
FA_LIMIT = 0.25          # overall false-alarm rate above this → result flagged unreliable


@dataclass
class Item:
    word: str
    real: bool
    definition: str = ""
    answer: Optional[bool] = None     # True = "real word", False = "not a word"
    ms: Optional[int] = None

    @property
    def correct(self) -> bool:
        return self.answer == self.real


@dataclass
class Block:
    no: int                           # 1-based
    subband: str
    items: list[Item]
    pos: int = 0                      # next item to answer

    @property
    def done(self) -> bool:
        return self.pos >= len(self.items)

    @property
    def hits(self) -> int:
        return sum(1 for i in self.items if i.real and i.answer is True)

    @property
    def false_alarms(self) -> int:
        return sum(1 for i in self.items if not i.real and i.answer is True)

    @property
    def n_real(self) -> int:
        return sum(1 for i in self.items if i.real)

    @property
    def n_pseudo(self) -> int:
        return sum(1 for i in self.items if not i.real)

    @property
    def score(self) -> float:
        return round(self.hits / self.n_real - self.false_alarms / self.n_pseudo, 4)

    @property
    def misses(self) -> list[Item]:
        return [i for i in self.items if i.real and i.answer is False]


@dataclass
class PooledScore:
    subband: str
    hits: int
    n_real: int
    false_alarms: int
    n_pseudo: int
    score: float
    mastered: bool


@dataclass
class Result:
    level: Optional[str]              # None = no tested sub-band was mastered
    lowest_failed: Optional[str]
    cefr: str
    det_low: Optional[int]
    det_high: Optional[int]
    blocks: int
    items: int
    false_alarms: int
    n_pseudo: int
    fa_rate: float
    reliable: bool
    pooled: list[PooledScore]
    path: list[dict]
    misses: list[Item]


@dataclass
class Session:
    id: str
    subbands: list[Subband]
    bank: Bank
    rng: random.Random
    started: datetime = field(default_factory=datetime.now)
    band_idx: int = 0
    blocks: list[Block] = field(default_factory=list)
    used: set[str] = field(default_factory=set)
    last_dir: int = 0
    reversals: int = 0
    finished: bool = False
    stop_reason: str = ""

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def create(cls, sid: str, subbands: list[Subband], bank: Bank, seed: Optional[int] = None,
               start_band: Optional[str] = None) -> "Session":
        s = cls(sid, subbands, bank, random.Random(seed))
        names = [b.name for b in subbands]
        s.band_idx = names.index(start_band) if start_band else len(subbands) // 2
        s._new_block()
        return s

    @classmethod
    def restore(cls, d: dict, subbands: list[Subband], bank: Bank, seed: Optional[int] = None) -> "Session":
        """Rebuild a session from store.session_dict(); the staircase is replayed from the saved blocks."""
        s = cls(d["id"], subbands, bank, random.Random(seed), started=datetime.fromisoformat(d["started"]))
        names = [b.name for b in subbands]
        s.band_idx = names.index(d["blocks"][0]["subband"])
        for bd in d["blocks"]:
            items = [Item(**i) for i in bd["items"]]
            s.used.update(i.word for i in items)
            s.blocks.append(Block(bd["no"], bd["subband"], items, bd.get("pos", len(items))))
            if s.block.done:
                s._after_block()
        return s

    @property
    def band(self) -> Subband:
        return self.subbands[self.band_idx]

    @property
    def block(self) -> Block:
        return self.blocks[-1]

    def _new_block(self) -> Block:
        name = self.band.name
        real = self.bank.draw_real(name, REAL_PER_BLOCK, self.used, self.rng)
        pseudo = self.bank.draw_pseudo(name, PSEUDO_PER_BLOCK, self.used, self.rng)
        items = [Item(w.word, True, w.definition) for w in real] + [Item(p, False) for p in pseudo]
        self.rng.shuffle(items)
        self.used.update(i.word for i in items)
        self.blocks.append(Block(len(self.blocks) + 1, name, items))
        return self.block

    def answer(self, yes: bool, ms: Optional[int] = None) -> str:
        """Record one answer. Returns 'next', 'block_done' or 'finished'."""
        if self.finished:
            raise ValueError("session is finished")
        b = self.block
        if b.done:
            raise ValueError("block is complete; call next_block()")
        it = b.items[b.pos]
        it.answer, it.ms = yes, ms
        b.pos += 1
        if not b.done:
            return "next"
        self._after_block()
        return "finished" if self.finished else "block_done"

    def _after_block(self) -> None:
        b = self.block
        direction = 1 if b.score >= self.band.mastery else -1
        if self.last_dir and direction != self.last_dir:
            self.reversals += 1
        self.last_dir = direction
        nxt = min(len(self.subbands) - 1, max(0, self.band_idx + direction))
        if self.reversals >= 2:
            self.stop_reason = "second reversal"
        elif len(self.blocks) >= MAX_BLOCKS:
            self.stop_reason = "max blocks"
        elif nxt == self.band_idx:
            self.stop_reason = "end of scale"
        elif not self.bank.has(self.subbands[nxt].name, REAL_PER_BLOCK, PSEUDO_PER_BLOCK, self.used):
            self.stop_reason = "bank exhausted"
        if self.stop_reason:
            self.finished = True
        else:
            self.band_idx = nxt

    def next_block(self) -> Block:
        if self.finished or not self.block.done:
            raise ValueError("no next block now")
        return self._new_block()

    # ---- result ----------------------------------------------------------
    def result(self) -> Result:
        by: dict[str, Subband] = {b.name: b for b in self.subbands}
        agg: dict[str, list[int]] = {}
        for b in self.blocks:
            if not b.done:
                continue
            a = agg.setdefault(b.subband, [0, 0, 0, 0])
            a[0] += b.hits; a[1] += b.n_real; a[2] += b.false_alarms; a[3] += b.n_pseudo
        pooled = []
        for sb in self.subbands:
            if sb.name not in agg:
                continue
            h, nr, fa, npd = agg[sb.name]
            score = round(h / nr - fa / npd, 4)
            pooled.append(PooledScore(sb.name, h, nr, fa, npd, score, score >= sb.mastery))
        mastered = [p for p in pooled if p.mastered]
        failed = [p for p in pooled if not p.mastered]
        level = mastered[-1].subband if mastered else None
        lowest_failed = failed[0].subband if failed else None
        n_pseudo = sum(p.n_pseudo for p in pooled)
        fa = sum(p.false_alarms for p in pooled)
        fa_rate = round(fa / n_pseudo, 4) if n_pseudo else 0.0
        done = [b for b in self.blocks if b.done]
        return Result(
            level=level,
            lowest_failed=lowest_failed,
            cefr=by[level].cefr if level else "",
            det_low=by[level].det_low if level else None,
            det_high=by[level].det_high if level else None,
            blocks=len(done),
            items=sum(len(b.items) for b in done),
            false_alarms=fa,
            n_pseudo=n_pseudo,
            fa_rate=fa_rate,
            reliable=fa_rate <= FA_LIMIT,
            pooled=pooled,
            path=[{"block": b.no, "subband": b.subband, "score": b.score,
                   "mastered": b.score >= by[b.subband].mastery} for b in done],
            misses=[i for b in done for i in b.misses],
        )
