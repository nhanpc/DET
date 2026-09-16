"""Simulated learners against the adaptive test on the θ / b scale (issues #4 'done when' and #14)."""
import random
from collections import Counter

import pytest

from app import irt
from app.adaptive import MAX_BLOCKS, MAX_PER_SUBBAND, PSEUDO_PER_BLOCK, REAL_PER_BLOCK, WINDOW, Session, compose
from app.bank import Bank, RealWord, load_subbands

SUBBANDS = load_subbands()
NAMES = [b.name for b in SUBBANDS]


class FakeBank(Bank):
    """Plenty of words per sub-band, b spread evenly through each band; real words are tagged with their sub-band."""

    def __init__(self, per_band: int = 60):
        self.real = {b.name: [RealWord(f"{b.name}-w{i}", b.name, "def", round(b.order - 1 + (i + 1) / per_band, 4))
                              for i in range(per_band)] for b in SUBBANDS}
        self.words = sorted((w for ws in self.real.values() for w in ws), key=lambda w: w.b)
        self.b = {w.word: w.b for w in self.words}
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
    """Knows every real word up to the middle of sub-band `level_idx` (b ≤ level_idx + 0.5), rejects the rest."""
    return lambda item: item.real and item.b <= level_idx + 0.5


def rasch_learner(theta: float, seed: int = 0):
    """Answers "yes" to a real word with P(θ, b), "no" to every invented word."""
    rng = random.Random(seed)
    return lambda item: item.real and rng.random() < irt.p(theta, item.b)


@pytest.mark.parametrize("k", range(1, len(NAMES)))
def test_step_learner_puts_theta_at_its_step(k):
    """A learner who knows exactly the words below k + 0.5: θ lands there, the frontier is that sub-band and the
    level (θ − 1.16) the one below — the test measures where P = 0.5, the level is where P = 0.85."""
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=k), learner_at(k))
    r = s.result()
    assert abs(r.theta - (k + 0.5)) < 2 * r.se + 0.1, (r.theta, r.se, [p["subband"] for p in r.path], s.stop_reason)
    assert r.frontier == NAMES[k] and r.level == NAMES[k - 1]
    assert r.reliable and len(s.blocks) <= MAX_BLOCKS
    assert (r.det_low, r.det_high) == (SUBBANDS[k - 1].det_low, SUBBANDS[k - 1].det_high)
    assert r.det_low <= r.det_estimate <= r.det_high and r.det_range[0] <= r.det_estimate <= r.det_range[1]


@pytest.mark.parametrize("true_theta", [3.0, 7.3, 9.5])
def test_rasch_learner_is_recovered(true_theta):
    for seed in range(3):
        r = run(Session.create("t", SUBBANDS, FakeBank(), seed=seed), rasch_learner(true_theta, seed)).result()
        assert abs(r.theta - true_theta) < 3 * r.se, (seed, r.theta, r.se)          # the prior pulls toward 6
        assert r.se < irt.SE_STOP and r.reliable


def test_learner_who_knows_nothing_has_no_level():
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=0), lambda item: False)
    r = s.result()
    assert r.level is None and r.frontier == "1k-a" and r.theta < irt.MASTERY_GAP
    assert r.cefr == "" and r.det_low is None and r.det_estimate == SUBBANDS[0].det_low
    assert r.lowest_failed == r.pooled[0].subband and r.reliable
    assert [p["subband"] for p in r.path][0] == "4k-a" and [p["subband"] for p in r.path][-1] == "1k-a"


def test_guesser_is_unreliable():
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=1), lambda item: True)
    r = s.result()
    assert not r.reliable
    assert r.fa_rate == 1.0
    assert all(not p.mastered for p in r.pooled)          # the pooled view: every block scored 1 − 1 = 0


def test_block_shape_no_repeats_and_composition():
    """Every block: 10 real + 5 invented, no word twice, every real word within |b − θ| ≤ WINDOW of the θ the
    block was composed from, at most MAX_PER_SUBBAND per sub-band, none from `recent`."""
    recent = {f"4k-a-w{i}" for i in range(20)} | {"3k-b-w59", "4k-b-w0"}
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=3, recent=recent), learner_at(7))
    words = [i.word for b in s.blocks for i in b.items]
    assert len(words) == len(set(words)) and not set(words) & recent
    for b in s.blocks:
        assert b.n_real == REAL_PER_BLOCK and b.n_pseudo == PSEUDO_PER_BLOCK
        real = [i for i in b.items if i.real]
        assert all(abs(i.b - b.theta_from) <= WINDOW for i in real)
        per = {}
        for i in real:
            per[band_of(i.word)] = per.get(band_of(i.word), 0) + 1
        # the cap holds unless the window touches too few sub-bands or one of them runs short (then topped up)
        avail = Counter(w.subband for w in FakeBank().in_window(b.theta_from, WINDOW, recent))
        cap = max(MAX_PER_SUBBAND, -(-REAL_PER_BLOCK // len(avail)))
        short = max(0, REAL_PER_BLOCK - sum(min(cap, c) for c in avail.values()))
        assert max(per.values()) <= cap + short and len(per) >= 2, (per, avail)
        assert b.subband == irt.frontier_of(b.theta_from, SUBBANDS)
        assert all(i.word.startswith(b.subband) for i in b.items if not i.real)


def test_compose_relaxes_the_cap_only_when_it_must():
    rng = random.Random(0)
    words = [RealWord(f"a{i}", "1k-a", "", 0.5) for i in range(8)] + [RealWord(f"b{i}", "1k-b", "", 1.5) for i in range(8)]
    picked = compose(words, 10, rng)
    assert len(picked) == 10 and sum(w.subband == "1k-a" for w in picked) == 5      # two sub-bands: 5 + 5, not 4 + 4
    assert compose(words[:9], 10, rng) == []
    assert sum(w.subband == "1k-a" for w in compose(words[:2] + words[8:], 10, rng)) == 2   # topped up from 1k-b
    three = words[:4] + words[8:12] + [RealWord(f"c{i}", "2k-a", "", 2.5) for i in range(4)]
    assert all(sum(w.subband == sb for w in compose(three, 10, rng)) <= MAX_PER_SUBBAND for sb in ("1k-a", "1k-b", "2k-a"))


def test_theta_moves_after_every_word_and_pseudo_words():
    """θ changes after a real answer; a pseudo-word "no" leaves it untouched; a pseudo-word "yes" lowers it."""
    s = Session.create("t", SUBBANDS, FakeBank(), seed=5)
    assert (s.theta, s.se, s.theta0) == (irt.THETA0, irt.PRIOR_SD, irt.THETA0)
    b = s.block
    seen = []
    for it in b.items:
        before = s.theta
        yes = it.real                                  # right on every real word, "no" to the invented ones
        s.answer(yes)
        seen.append((it.real, before, s.theta))
    assert all(after > before for real, before, after in seen if real)
    assert all(after == before for real, before, after in seen if not real)
    assert b.theta == s.theta and b.se == s.se and b.theta_from == irt.THETA0
    # the same block answered with "yes" to an invented word: θ lower than without it
    t = Session.create("t", SUBBANDS, FakeBank(), seed=5)
    said_yes = False
    for it in t.block.items:
        yes = it.real or not said_yes
        said_yes = said_yes or not it.real
        t.answer(yes)
    assert t.theta < s.theta and t.block.false_alarms == 1


def test_stop_rules():
    """se < SE_STOP ends a session; a small bank ends it as 'bank exhausted'; all-correct climbs to the top."""
    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=2), learner_at(6))
    assert s.stop_reason == f"se < {irt.SE_STOP}" and s.se < irt.SE_STOP and s.blocks[-2].se >= irt.SE_STOP
    small = run(Session.create("t", SUBBANDS, FakeBank(per_band=6), seed=2), rasch_learner(6.0, 1))
    assert small.stop_reason == "bank exhausted" and len(small.blocks) <= 2 and small.se >= irt.SE_STOP
    top = run(Session.create("t", SUBBANDS, FakeBank(), seed=2), lambda item: item.real)
    r = top.result()
    assert r.frontier == "6k-b" and r.level == "6k-b" and r.theta > 12
    assert [p["subband"] for p in r.path][-1] == "6k-b" and all(p["mastered"] for p in r.path)
    assert r.det_estimate >= SUBBANDS[-1].det_low


def test_prior_and_start_band():
    """θ₀ from the last session (the Re-test button) composes block 1 around it; `start_band` = its middle."""
    s = Session.create("t", SUBBANDS, FakeBank(), seed=1, theta0=9.2)
    assert s.theta0 == 9.2 and s.block.subband == "5k-b" and s.block.theta_from == 9.2
    s = Session.create("t", SUBBANDS, FakeBank(), seed=1, start_band="1k-b")
    assert s.theta0 == 1.5 and s.block.subband == "1k-b"
    assert Session.create("t", SUBBANDS, FakeBank(), seed=1).theta0 == irt.THETA0
    with pytest.raises(ValueError):
        Session.create("t", SUBBANDS, FakeBank(), seed=1, recent={w.word for w in FakeBank().words})


def test_real_bank_has_enough_words_for_a_full_session():
    bank = Bank()
    for name in NAMES:
        assert bank.has(name, REAL_PER_BLOCK * 3, PSEUDO_PER_BLOCK * 3, set()), name
    for tenths in range(0, 121, 5):
        assert len(bank.in_window(tenths / 10, WINDOW, set())) >= REAL_PER_BLOCK * MAX_BLOCKS, tenths / 10
    s = run(Session.create("t", SUBBANDS, bank, seed=4), rasch_learner(7.3, 4))
    assert s.result().reliable and all(0 < i.b <= 12 for b in s.blocks for i in b.items if i.real)


def test_restore_replays_the_posterior():
    from app.store import session_dict

    s = run(Session.create("t", SUBBANDS, FakeBank(), seed=4, theta0=7.0), rasch_learner(7.5, 2))
    back = Session.restore(session_dict(s), SUBBANDS, FakeBank())
    assert back.finished and back.stop_reason == s.stop_reason and back.theta0 == 7.0
    assert (back.theta, back.se) == (s.theta, s.se)
    assert back.result() == s.result()
    assert session_dict(back) == session_dict(s)
    # a file from before #14: no theta0, no b on the items → the bank's b and the default prior
    d = session_dict(s)
    del d["theta0"]
    for b in d["blocks"]:
        for k in ("theta_from", "theta", "se"):
            del b[k]
        for i in b["items"]:
            del i["b"]
    old = Session.restore(d, SUBBANDS, FakeBank())
    assert old.theta0 == irt.THETA0 and old.finished and old.blocks[0].items[0].b == s.blocks[0].items[0].b
