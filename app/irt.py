"""One scale for words and learners (GitHub issue nhanpc/DET#14). Pure functions over `math`, no I/O.

Item difficulty  b = (order − 1) + pos_in_band / 500  — the continuous band index: `1k-a` = 0–1, …, `6k-b` = 11–12.
                 Written as b(rank) with rank = the word's position on the scale, 1 … 6000 (scale_rank()).
Model            Rasch, P(θ, b) = 1 / (1 + exp(−A · (θ − b))), A = 1.5 per band: a word one band below θ is
                 answered right ≈ 82 % of the time, and P crosses the old mastery line 0.85 at θ − b = MASTERY_GAP.
Ability          θ by expected-a-posteriori on GRID with a normal prior N(θ₀, PRIOR_SD²); eap() returns (θ, se).
                 A response is (b, score) with score in [0, 1]: 1 = right, 0 = wrong, a fraction enters the
                 likelihood as P^s · (1 − P)^(1 − s) (the partial-credit drills of #16 and #17).
Level            the sub-band containing θ − MASTERY_GAP (where the learner knows 85 %); frontier = the one containing θ.
DET estimate     piece-wise linear interpolation of the mastery point θ − MASTERY_GAP on the det_low / det_high
                 anchors of vocab/subbands.csv, so the estimate always falls inside the level's own range.
docs/det-adaptive.md has the formulas, the anchors and the differences to the real DET.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

from .bank import Subband

A = 1.5                       # discrimination per band
BAND = 500                    # words per sub-band: b moves by 1 across one sub-band
THETA0 = 6.0                  # prior mean of the first session (the middle of the scale)
PRIOR_SD = 1.5
GRID_LO, GRID_HI, GRID_STEP = 0.0, 13.0, 0.05
GRID = [round(GRID_LO + i * GRID_STEP, 2) for i in range(round((GRID_HI - GRID_LO) / GRID_STEP) + 1)]
MASTERY = 0.85                # the old block rule's mastery line, kept as the definition of "level"
MASTERY_GAP = math.log(MASTERY / (1 - MASTERY)) / A          # 1.156: θ − b where P = 0.85
SE_STOP = 0.35                # the test stops once the posterior sd is below this


# ---- items ------------------------------------------------------------------------------------------------

def scale_rank(order: int, pos_in_band: int) -> int:
    """Position on the scale, 1 … 6000: (order − 1) · 500 + pos_in_band. Not the Nation rank column of
    index.csv — pos_in_band orders the words inside a sub-band by Zipf frequency (scripts/build_bands.py)."""
    return (order - 1) * BAND + pos_in_band


def b(rank: int, adjust: float = 0.0) -> float:
    """Difficulty of the word at scale rank `rank`: rank 500 → 1.0 (the last 1k-a word), 501 → 1.002.
    `adjust` = the index.csv b_adjust column once responses refit it (default 0)."""
    return rank / BAND + adjust


def p(theta: float, b: float) -> float:
    """P(correct) under the Rasch model with discrimination A."""
    return 1.0 / (1.0 + math.exp(-A * (theta - b)))


def _loglik(theta: float, b: float, score: float) -> float:
    """score · log P + (1 − score) · log(1 − P), computed without ever taking log(0)."""
    x = A * (theta - b)
    log_p = -math.log1p(math.exp(-x)) if x > -30 else x
    log_q = log_p - x                                   # log(1 − P) = log P − x
    return score * log_p + (1.0 - score) * log_q


# ---- ability -------------------------------------------------------------------------------------------------

class Posterior:
    """The posterior of θ on GRID, updated one response at a time (a test replays 90 items in no time)."""

    def __init__(self, mean: float = THETA0, sd: float = PRIOR_SD):
        self.mean, self.sd = mean, sd
        self.logw = [-0.5 * ((t - mean) / sd) ** 2 for t in GRID]
        self.n = 0

    def add(self, b: float, score: float = 1.0) -> "Posterior":
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"score {score} outside [0, 1]")
        for i, t in enumerate(GRID):
            self.logw[i] += _loglik(t, b, score)
        self.n += 1
        return self

    def estimate(self) -> tuple[float, float]:
        """(θ, se): the posterior mean and standard deviation."""
        top = max(self.logw)
        w = [math.exp(x - top) for x in self.logw]
        z = sum(w)
        theta = sum(t * x for t, x in zip(GRID, w)) / z
        var = sum((t - theta) ** 2 * x for t, x in zip(GRID, w)) / z
        return round(theta, 4), round(math.sqrt(var), 4)


def eap(responses: Iterable[tuple[float, float]], mean: float = THETA0, sd: float = PRIOR_SD) -> tuple[float, float]:
    """Expected-a-posteriori ability from (b, score) pairs under the prior N(mean, sd²). No responses → the prior."""
    post = Posterior(mean, sd)
    for b_, score in responses:
        post.add(b_, score)
    return post.estimate()


# ---- the scale in sub-band terms ----------------------------------------------------------------------------

def band_at(x: float, subbands: Sequence[Subband]) -> Subband:
    """The sub-band whose b range [order − 1, order) contains x, clamped to the ends of the scale."""
    order = min(len(subbands), max(1, math.floor(x) + 1))
    return subbands[order - 1]


def item_band(b: float, subbands: Sequence[Subband]) -> Subband:
    """The sub-band a word of difficulty b belongs to: b runs over (order − 1, order], so the top word of 1k-a
    (b = 1.0) is 1k-a, not 1k-b."""
    return subbands[min(len(subbands), max(1, math.ceil(b))) - 1]


def frontier_of(theta: float, subbands: Sequence[Subband]) -> str:
    """The sub-band containing θ — the words the learner knows about half of, where the next block is drawn."""
    return band_at(theta, subbands).name


def level_of(theta: float, subbands: Sequence[Subband]) -> Optional[str]:
    """The sub-band containing θ − MASTERY_GAP, the point where P(correct) = 0.85; None below the scale."""
    x = theta - MASTERY_GAP
    return band_at(x, subbands).name if x >= 0 else None


def det_anchors(subbands: Sequence[Subband]) -> list[tuple[float, float, int, int]]:
    """(x_lo, x_hi, det_low, det_high) per run of consecutive sub-bands that share a DET range: the mastery point
    x = θ − MASTERY_GAP runs from the bottom of the first sub-band to the top of the last one."""
    out: list[tuple[float, float, int, int]] = []
    for sb in subbands:
        if out and out[-1][2:] == (sb.det_low, sb.det_high):
            lo, _, dl, dh = out[-1]
            out[-1] = (lo, float(sb.order), dl, dh)
        else:
            out.append((float(sb.order - 1), float(sb.order), sb.det_low, sb.det_high))
    return out


def det_estimate(theta: float, subbands: Sequence[Subband]) -> int:
    """The DET score for θ: linear inside each anchor run, det_low of the first run below the scale, det_high of
    the last one above it. The estimate of a level-X learner lies in X's own det_low–det_high."""
    x = theta - MASTERY_GAP
    anchors = det_anchors(subbands)
    if x < anchors[0][0]:
        return anchors[0][2]
    for lo, hi, dl, dh in anchors:
        if lo <= x < hi:
            return round(dl + (x - lo) / (hi - lo) * (dh - dl))
    return anchors[-1][3]


def det_range(theta: float, se: float, subbands: Sequence[Subband]) -> tuple[int, int]:
    """θ ± se mapped the same way."""
    return det_estimate(theta - se, subbands), det_estimate(theta + se, subbands)
