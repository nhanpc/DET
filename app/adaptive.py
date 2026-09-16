"""The level test as a computerised adaptive test on the θ / b scale of app/irt.py (GitHub issues nhanpc/DET#4
and #14). Pure logic, no I/O.

One block = REAL_PER_BLOCK real words with |b − θ| ≤ WINDOW (at most MAX_PER_SUBBAND from one sub-band, none
shown in this session or in the last RECENT_DAYS days) + PSEUDO_PER_BLOCK invented words from the sub-band
containing θ, shuffled. θ is updated after every answer (a pseudo-word "yes" counts as a wrong answer at
b = θ; a pseudo-word "no" is not evidence); the next block is composed from the updated θ. The session stops
when se < SE_STOP, after MAX_BLOCKS blocks, or when the bank has no block left near θ.
Level = the sub-band containing θ − MASTERY_GAP, frontier = the one containing θ, the DET estimate is
interpolated from vocab/subbands.csv; block score, pooled scores and the false-alarm reliability rule are the
old block rule's numbers, kept as a second view.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import irt
from .bank import Bank, RealWord, Subband

REAL_PER_BLOCK = 10
PSEUDO_PER_BLOCK = 5
MAX_BLOCKS = 6
FA_LIMIT = 0.25          # overall false-alarm rate above this → result flagged unreliable
WINDOW = 1.0             # real words come from |b − θ| ≤ WINDOW
MAX_PER_SUBBAND = 4      # a 2-band window touches 3 sub-bands: 3 × 3 < 10, so the cap is 4 (issue #14 said 3)
RECENT_DAYS = 30         # a word shown in a session this recent is not drawn again


@dataclass
class Item:
    word: str
    real: bool
    definition: str = ""
    answer: Optional[bool] = None     # True = "real word", False = "not a word"
    ms: Optional[int] = None
    b: Optional[float] = None         # difficulty on the scale; None for a pseudo-word

    @property
    def correct(self) -> bool:
        return self.answer == self.real


@dataclass
class Block:
    no: int                           # 1-based
    subband: str                      # the sub-band containing θ when the block was composed
    items: list[Item]
    pos: int = 0                      # next item to answer
    theta_from: float = irt.THETA0    # θ the block was composed from
    theta: Optional[float] = None     # θ and se after the last answer of the block
    se: Optional[float] = None

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
    level: Optional[str]              # the sub-band containing θ − MASTERY_GAP; None below the scale
    lowest_failed: Optional[str]      # pooled view: the lowest tested sub-band under the mastery line
    cefr: str
    det_low: Optional[int]            # the level's own range from vocab/subbands.csv
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
    theta: float
    se: float
    frontier: str                     # the sub-band containing θ
    det_estimate: int
    det_range: tuple[int, int]        # θ ± se on the DET scale


def compose(words: list[RealWord], n: int, rng: random.Random, cap: int = MAX_PER_SUBBAND) -> list[RealWord]:
    """`n` words drawn at random from `words` (already filtered to the window and unseen), at most `cap` per
    sub-band — raised to ceil(n / sub-bands present) when the window touches too few sub-bands (at a band edge
    or the ends of the scale), and topped up regardless when a sub-band has too few words. [] when `words`
    has fewer than n."""
    pool = list(words)
    rng.shuffle(pool)
    cap = max(cap, -(-n // max(1, len({w.subband for w in pool}))))
    picked, spare, per = [], [], {}
    for w in pool:
        if per.get(w.subband, 0) < cap:
            picked.append(w)
            per[w.subband] = per.get(w.subband, 0) + 1
        else:
            spare.append(w)
        if len(picked) == n:
            return picked
    picked += spare[: n - len(picked)]
    return picked if len(picked) == n else []


@dataclass
class Session:
    id: str
    subbands: list[Subband]
    bank: Bank
    rng: random.Random
    started: datetime = field(default_factory=datetime.now)
    theta0: float = irt.THETA0             # prior mean: the last session's θ (the Re-test button), 6.0 the first time
    recent: set[str] = field(default_factory=set)   # words shown in the last RECENT_DAYS days: never drawn
    blocks: list[Block] = field(default_factory=list)
    used: set[str] = field(default_factory=set)
    posterior: irt.Posterior = field(default_factory=irt.Posterior)
    theta: float = irt.THETA0
    se: float = irt.PRIOR_SD
    finished: bool = False
    stop_reason: str = ""

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def create(cls, sid: str, subbands: list[Subband], bank: Bank, seed: Optional[int] = None,
               start_band: Optional[str] = None, theta0: Optional[float] = None,
               recent: Optional[set[str]] = None) -> "Session":
        """`theta0` = prior mean; `start_band` = the middle of that sub-band instead (the old API); default 6.0."""
        if start_band:
            theta0 = next(b.order for b in subbands if b.name == start_band) - 0.5
        s = cls(sid, subbands, bank, random.Random(seed), theta0=theta0 if theta0 is not None else irt.THETA0,
                recent=set(recent or ()))
        s._reset_posterior()
        s._new_block()
        return s

    @classmethod
    def restore(cls, d: dict, subbands: list[Subband], bank: Bank, seed: Optional[int] = None,
                recent: Optional[set[str]] = None, theta0: Optional[float] = None) -> "Session":
        """Rebuild a session from store.session_dict(); every answer is replayed through the posterior. Items
        saved without `b` (files from before #14) take the bank's b, or the middle of the block's sub-band; a
        file without `theta0` takes the `theta0` argument (learn.theta_history's chained prior), else 6.0."""
        s = cls(d["id"], subbands, bank, random.Random(seed), started=datetime.fromisoformat(d["started"]),
                theta0=d.get("theta0", theta0 if theta0 is not None else irt.THETA0), recent=set(recent or ()))
        s._reset_posterior()
        by = {b.name: b for b in subbands}
        for bd in d["blocks"]:
            items = []
            for i in bd["items"]:
                it = Item(**i)
                if it.real and it.b is None:
                    it.b = bank.b.get(it.word, by[bd["subband"]].order - 0.5)
                items.append(it)
            s.used.update(i.word for i in items)
            blk = Block(bd["no"], bd["subband"], items, 0, bd.get("theta_from", s.theta))
            s.blocks.append(blk)
            for it in items[: bd.get("pos", len(items))]:
                s._record(blk, it)
            if blk.done:
                s._after_block()
        return s

    def _reset_posterior(self) -> None:
        self.posterior = irt.Posterior(self.theta0)
        self.theta, self.se = self.theta0, irt.PRIOR_SD          # the prior itself, until the first answer

    @property
    def frontier(self) -> str:
        return irt.frontier_of(self.theta, self.subbands)

    @property
    def block(self) -> Block:
        return self.blocks[-1]

    def _draw(self) -> tuple[list[RealWord], str]:
        """(real words, sub-band of the pseudo-words) for a block at the current θ; [] when the bank is dry."""
        name = self.frontier
        if not self.bank.has_pseudo(name, PSEUDO_PER_BLOCK, self.used):
            return [], name
        return compose(self.bank.in_window(self.theta, WINDOW, self.used | self.recent), REAL_PER_BLOCK, self.rng), name

    def _new_block(self) -> Block:
        real, name = self._draw()
        if not real:
            raise ValueError(f"no block left near θ = {self.theta}")
        pseudo = self.bank.draw_pseudo(name, PSEUDO_PER_BLOCK, self.used, self.rng)
        items = [Item(w.word, True, w.definition, b=w.b) for w in real] + [Item(p, False) for p in pseudo]
        self.rng.shuffle(items)
        self.used.update(i.word for i in items)
        self.blocks.append(Block(len(self.blocks) + 1, name, items, 0, self.theta))
        return self.block

    def _record(self, b: Block, it: Item) -> None:
        """One answered item into the posterior: a real word at its b, a pseudo-word "yes" as a wrong answer at b = θ."""
        b.pos += 1
        if it.real:
            self.posterior.add(it.b, 1.0 if it.answer else 0.0)
        elif it.answer:
            self.posterior.add(self.theta, 0.0)
        else:
            return
        self.theta, self.se = self.posterior.estimate()

    def answer(self, yes: bool, ms: Optional[int] = None) -> str:
        """Record one answer. Returns 'next', 'block_done' or 'finished'."""
        if self.finished:
            raise ValueError("session is finished")
        b = self.block
        if b.done:
            raise ValueError("block is complete; call next_block()")
        it = b.items[b.pos]
        it.answer, it.ms = yes, ms
        self._record(b, it)
        if not b.done:
            return "next"
        self._after_block()
        return "finished" if self.finished else "block_done"

    def _after_block(self) -> None:
        b = self.block
        b.theta, b.se = self.theta, self.se
        if self.se < irt.SE_STOP:
            self.stop_reason = f"se < {irt.SE_STOP}"
        elif len(self.blocks) >= MAX_BLOCKS:
            self.stop_reason = "max blocks"
        elif not self._draw()[0]:
            self.stop_reason = "bank exhausted"
        if self.stop_reason:
            self.finished = True

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
        failed = [p for p in pooled if not p.mastered]
        level = irt.level_of(self.theta, self.subbands)
        n_pseudo = sum(p.n_pseudo for p in pooled)
        fa = sum(p.false_alarms for p in pooled)
        fa_rate = round(fa / n_pseudo, 4) if n_pseudo else 0.0
        done = [b for b in self.blocks if b.done]
        return Result(
            level=level,
            lowest_failed=failed[0].subband if failed else None,
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
            path=[{"block": b.no, "subband": b.subband, "score": b.score, "mastered": b.score >= by[b.subband].mastery,
                   "theta": b.theta, "se": b.se} for b in done],
            misses=[i for b in done for i in b.misses],
            theta=self.theta,
            se=self.se,
            frontier=self.frontier,
            det_estimate=irt.det_estimate(self.theta, self.subbands),
            det_range=irt.det_range(self.theta, self.se, self.subbands),
        )
