"""Simulated learners against the staircase (issue #4 'done when')."""
import random

import pytest

from app.adaptive import MAX_BLOCKS, PSEUDO_PER_BLOCK, REAL_PER_BLOCK, Session
from app.bank import Bank, RealWord, Subband, load_subbands

SUBBANDS = load_subbands()
NAMES = [b.name for b in SUBBANDS]


class FakeBank(Bank):
    """Plenty of words per sub-band; real words are tagged with their sub-band index."""

    def __init__(self, per_band: int = 60):
        self.real = {b.name: [RealWord(f"{b.name}-w{i}", b.name, "def") for i in range(per_band)] for b in SUBBANDS}
        self.pseudo = {b.name: [f"{b.name}-x{i}" for i in range(per_band)] for b in SUBBANDS}
        self.index = {}


def band_of(word: str) -> int:
    return NAMES.index(word.split("-w")[0].split("-x")[0])


def run(session: Session, knows) -> Session:
    """`knows(item) -> bool` is the learner's answer ('yes, real word')."""
    while not session.finished:
        b = session.block
        while not b.done:
            session.answer(knows(b.items[b.pos]))
        if not session.finished:
            session.next_block()
    return session


def learner_at(level_idx: int):
    """Knows every real word up to and including sub-band `level_idx`, rejects everything else."""
    return lambda item: item.real and band_of(item.word) <= level_idx


@pytest.mark.parametrize("k", range(1, len(NAMES)))
def test_perfect_learner_lands_on_its_level(k):
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=k), learner_at(k))
    r = s.result()
    assert r.level == NAMES[k], (r.level, [p["subband"] for p in r.path], s.stop_reason)
    assert r.reliable
    assert len(s.blocks) <= MAX_BLOCKS
    assert r.det_low == SUBBANDS[k].det_low


def test_learner_below_1k_b_has_no_mastered_band():
    # Six blocks down from the middle end at 1k-b: 1k-a itself is never reached.
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=0), learner_at(0))
    r = s.result()
    assert r.level is None
    assert r.lowest_failed == "1k-b"
    assert s.stop_reason == "max blocks"


def test_guesser_is_unreliable():
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=1), lambda item: True)
    r = s.result()
    assert not r.reliable
    assert r.fa_rate == 1.0
    assert r.level is None


def test_block_shape_and_no_repeats():
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=3), learner_at(7))
    words = [i.word for b in s.blocks for i in b.items]
    assert len(words) == len(set(words))
    for b in s.blocks:
        assert b.n_real == REAL_PER_BLOCK and b.n_pseudo == PSEUDO_PER_BLOCK


def test_worked_example_from_issue():
    """4k-a pass, 4k-b fail (8 hits, 1 false alarm), 4k-a pass → level 4k-a, 45 items."""
    s = Session.create("t", SUBBANDS, FakeBank(), seed=5)
    plan = iter([(9, 0), (8, 1), (9, 0)])
    hits, fa = next(plan)
    while not s.finished:
        b = s.block
        seen_real = seen_pseudo = 0
        while not b.done:
            it = b.items[b.pos]
            if it.real:
                seen_real += 1
                s.answer(seen_real <= hits)
            else:
                seen_pseudo += 1
                s.answer(seen_pseudo <= fa)
        if not s.finished:
            hits, fa = next(plan)
            s.next_block()
    r = s.result()
    assert [p["subband"] for p in r.path] == ["4k-a", "4k-b", "4k-a"]
    assert r.level == "4k-a" and r.items == 45 and r.reliable
    assert [(p.subband, p.score) for p in r.pooled] == [("4k-a", 0.9), ("4k-b", 0.6)]
    assert [m.word for m in r.misses] and len(r.misses) == 4


def test_real_bank_has_enough_words_for_a_full_session():
    bank = Bank()
    for name in NAMES:
        assert bank.has(name, REAL_PER_BLOCK * 3, PSEUDO_PER_BLOCK * 3, set()), name


def test_stops_at_top_of_scale():
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=2, start_band="6k-a"), learner_at(11))
    assert s.result().level == "6k-b"
    assert s.stop_reason == "end of scale"
