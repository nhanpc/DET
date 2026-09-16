"""The θ / b scale of issue #14: item difficulty, the EAP estimate, pseudo-words, level and frontier from θ,
the DET anchors, the three saved sessions replayed, old levels.csv rows, the docs."""
import csv
import random
import re
from datetime import datetime
from pathlib import Path

import pytest

from app import irt, learn, progress, store
from app.adaptive import Session
from app.bank import Bank, load_subbands
from tests.test_adaptive import FakeBank, run

ROOT = Path(__file__).resolve().parent.parent
SUBBANDS = load_subbands()
NAMES = [b.name for b in SUBBANDS]


def simulate(true_theta: float, bs: list[float], seed: int) -> list[tuple[float, float]]:
    rng = random.Random(seed)
    return [(b, 1.0 if rng.random() < irt.p(true_theta, b) else 0.0) for b in bs]


def test_b_is_monotone_in_rank_and_continuous_across_band_edges():
    assert irt.b(500) == 1.0 and irt.b(501) == 1.002 and irt.b(1) == 0.002 and irt.b(6000) == 12.0
    assert irt.scale_rank(1, 500) == 500 and irt.scale_rank(2, 1) == 501 and irt.scale_rank(12, 500) == 6000
    assert irt.b(500, adjust=0.3) == 1.3
    bs = [irt.b(r) for r in range(1, 6001)]
    assert all(y > x for x, y in zip(bs, bs[1:])) and max(y - x for x, y in zip(bs, bs[1:])) == pytest.approx(0.002)
    # the real bank: b follows (order, pos_in_band), every sub-band spans exactly (order − 1, order]
    bank = Bank()
    rows = sorted(bank.index.values(), key=lambda r: (NAMES.index(r["subband"]), int(r["pos_in_band"])))
    seq = [bank.b[r["family"]] for r in rows]
    assert all(y > x for x, y in zip(seq, seq[1:])) and len(seq) == 6000
    for sb in SUBBANDS:
        inside = [bank.b[r["family"]] for r in rows if r["subband"] == sb.name]
        assert min(inside) == sb.order - 1 + 0.002 and max(inside) == sb.order
    assert bank.b["you"] == 0.002 and bank.b["govern"] == 1.0
    assert all(w.b == bank.b[w.word] for w in bank.words) and bank.words == sorted(bank.words, key=lambda w: w.b)


def test_model_constants():
    assert irt.p(6.0, 5.0) == pytest.approx(0.8176, abs=1e-4)          # one band below θ ≈ 82 %
    assert irt.p(6.0, 6.0) == 0.5 and irt.p(6.0, 7.0) == pytest.approx(1 - irt.p(6.0, 5.0))
    assert irt.MASTERY_GAP == pytest.approx(1.156, abs=1e-3) and irt.p(irt.MASTERY_GAP, 0.0) == pytest.approx(0.85)
    assert irt.GRID[0] == 0.0 and irt.GRID[-1] == 13.0 and len(irt.GRID) == 261


def test_eap_recovers_theta_and_se_shrinks():
    """20 simulated answers from θ = 7.3 on words around it: θ̂ within 2·se; se shrinks with every answer."""
    bs = [6.3 + 0.1 * i for i in range(20)]
    theta, se = irt.eap(simulate(7.3, bs, seed=3))
    assert abs(theta - 7.3) < 2 * se, (theta, se)
    assert irt.eap([]) == pytest.approx((irt.THETA0, irt.PRIOR_SD), abs=0.01)
    ses = []
    post = irt.Posterior()
    for b, score in simulate(7.3, bs, seed=3):
        ses.append(post.add(b, score).estimate()[1])
    assert all(later < earlier for earlier, later in zip(ses, ses[1:])) and ses[-1] < 0.5 < ses[0]
    hits = sum(abs(irt.eap(simulate(7.3, bs, seed=k))[0] - 7.3) < 2 * irt.eap(simulate(7.3, bs, seed=k))[1]
               for k in range(60))
    assert hits >= 50                                              # ≈ 90 % of the runs (the prior at 6 pulls down)
    # fractional responses (#16, #17): 0.5 lies between right and wrong, out-of-range is refused
    up, mid, down = (irt.eap([(irt.THETA0, s)])[0] for s in (1.0, 0.5, 0.0))
    assert down < mid < up and abs(mid - irt.THETA0) < 0.05 and up - irt.THETA0 > 0.3
    with pytest.raises(ValueError):
        irt.Posterior().add(7.0, 1.5)
    # a custom prior; b far from every grid point does not overflow
    assert irt.eap([], mean=9.0)[0] == pytest.approx(9.0, abs=0.05)              # the grid ends at 13
    assert irt.eap([(40.0, 1.0), (-30.0, 0.0)])[0] == pytest.approx(irt.THETA0, abs=0.05)


def test_pseudo_word_yes_lowers_theta_and_no_leaves_it():
    base = [(6.0, 1.0), (6.5, 1.0), (5.5, 1.0), (7.0, 0.0)]
    theta, _ = irt.eap(base)
    lower, wider = irt.eap(base + [(theta, 0.0)])
    assert lower < theta and irt.eap(base + [(theta, 0.0)])[1] > 0
    # through the session: "no" to an invented word is not evidence, "yes" is a wrong answer at b = θ
    s = Session.create("t", SUBBANDS, FakeBank(), seed=9)
    pseudo = [it for it in s.block.items if not it.real]
    while not s.block.items[s.block.pos].real:                  # skip leading pseudo-words so θ has moved
        s.answer(False)
    s.answer(True)
    before = (s.theta, s.se, s.posterior.n)
    nxt = s.block.items[s.block.pos]
    if not nxt.real:
        s.answer(False)
        assert (s.theta, s.se, s.posterior.n) == before
    t = Session.create("t", SUBBANDS, FakeBank(), seed=9)
    for it in t.block.items:
        t.answer(True if it.real else it.word == pseudo[0].word)
    u = Session.create("t", SUBBANDS, FakeBank(), seed=9)
    for it in u.block.items:
        u.answer(it.real)
    assert t.theta < u.theta and t.posterior.n == u.posterior.n + 1


def test_level_frontier_and_det_anchors():
    assert learn.frontier(None, SUBBANDS) == (None, "1k-a")
    assert learn.frontier(6.4, SUBBANDS) == ("3k-b", "4k-a") and learn.frontier(7.1, SUBBANDS) == ("3k-b", "4k-b")
    assert learn.frontier(7.2, SUBBANDS) == ("4k-a", "4k-b")
    assert irt.level_of(1.0, SUBBANDS) is None and irt.level_of(1.16, SUBBANDS) == "1k-a"
    assert irt.frontier_of(-1.0, SUBBANDS) == "1k-a" and irt.frontier_of(12.9, SUBBANDS) == "6k-b"
    assert irt.level_of(13.0, SUBBANDS) == "6k-b"
    assert irt.det_anchors(SUBBANDS) == [(0.0, 2.0, 10, 30), (2.0, 4.0, 35, 55), (4.0, 6.0, 60, 85),
                                         (6.0, 8.0, 90, 105), (8.0, 10.0, 105, 115), (10.0, 12.0, 120, 160)]
    g = irt.MASTERY_GAP
    assert irt.det_estimate(g, SUBBANDS) == 10 and irt.det_estimate(2 + g, SUBBANDS) == 35
    assert irt.det_estimate(5 + g, SUBBANDS) == round(60 + 12.5) and irt.det_estimate(0.0, SUBBANDS) == 10
    assert irt.det_estimate(12 + g, SUBBANDS) == 160 and irt.det_estimate(13.0, SUBBANDS) == 157
    # the estimate of a level-X learner always lies in X's own det_low–det_high
    for tenths in range(12, 130, 3):
        theta = tenths / 10
        sb = next(b for b in SUBBANDS if b.name == irt.level_of(theta, SUBBANDS))
        assert sb.det_low <= irt.det_estimate(theta, SUBBANDS) <= sb.det_high, theta
    lo, hi = irt.det_range(6.77, 0.25, SUBBANDS)
    assert lo < irt.det_estimate(6.77, SUBBANDS) < hi and (lo, hi) == (77, 83)


def test_saved_sessions_replayed_through_the_scale():
    """The three sessions in vocab/tests/sessions/ (old block rule: levels 3k-b, 3k-b, 4k-a; pooled level 3k-b,
    frontier 4k-a). Replayed through the EAP, chaining each prior on the last θ: the θ level agrees with the
    old per-session level on all three; the θ frontier agrees on session 1 (4k-a) and is one sub-band higher on
    sessions 2 and 3, because the old frontier is the lowest *failed* sub-band while the θ frontier is where
    P = 0.5 (session 3: 18/20 at 4k-a and 8/10 at 4k-b puts θ at 8.13, in 5k-a). The current level and frontier
    from θ are therefore (4k-a, 5k-a), one sub-band above the old pooled (3k-b, 4k-a)."""
    sessions = store.load_sessions()
    levels = store.load_levels()
    bank = Bank()
    ids = ["2026-09-15_214411_04cc", "2026-09-15_220053_d7f7", "2026-09-15_220243_d33a"]
    assert [s["id"] for s in sessions][:3] == ids and [r["session"] for r in levels][:3] == ids
    hist = learn.theta_history(sessions[:3], SUBBANDS, bank.b)
    assert [h["level"] for h in hist] == [r["level"] for r in levels[:3]] == ["3k-b", "3k-b", "4k-a"]
    assert [h["frontier"] for h in hist] == ["4k-a", "4k-b", "5k-a"]
    assert [round(h["theta"], 2) for h in hist] == [6.77, 7.13, 8.13]
    assert all(0.25 <= h["se"] <= 0.34 and h["reliable"] for h in hist)
    assert [h["det"] for h in hist] == [80, 85, 97] and hist[0]["det_range"] == (77, 83)
    assert learn.current_theta(hist) == (hist[-1]["theta"], hist[-1]["se"])
    assert learn.frontier(hist[-1]["theta"], SUBBANDS) == ("4k-a", "5k-a")
    # the old rule on the same sessions, for the record
    scores = learn.subband_scores(sessions[:3], SUBBANDS)
    assert [(s["subband"], s["status"]) for s in scores if s["blocks"]] == \
        [("3k-a", "mastered"), ("3k-b", "mastered"), ("4k-a", "not yet"), ("4k-b", "not yet")]
    # Session.restore() and the replay agree given the same prior; the session's own theta0 wins when saved
    prior = irt.THETA0
    for d, h in zip(sessions[:3], hist):
        back = Session.restore(d, SUBBANDS, bank, theta0=prior)
        assert (back.theta, back.se) == (h["theta"], h["se"]) and back.finished and back.theta0 == prior
        prior = back.theta
    assert Session.restore(sessions[1], SUBBANDS, bank).theta0 == irt.THETA0
    assert learn.session_theta({**sessions[2], "theta0": 6.0}, SUBBANDS, bank.b, 9.0) == \
        learn.session_theta(sessions[2], SUBBANDS, bank.b, 6.0)
    # without the bank, the middle of each block's sub-band stands in for b
    rough = learn.theta_history(sessions[:3], SUBBANDS)
    assert all(abs(a["theta"] - b["theta"]) < 0.5 for a, b in zip(rough, hist))
    # an unreliable session gets a θ but is not the next prior
    guess = {**sessions[0], "id": "g", "started": "2026-09-16T10:00:00", "result": {**sessions[0]["result"], "reliable": False}}
    h2 = learn.theta_history(sessions[:3] + [guess], SUBBANDS, bank.b)
    assert not h2[-1]["reliable"] and learn.current_theta(h2) == learn.current_theta(hist)
    assert learn.session_theta(guess, SUBBANDS, bank.b, hist[-1]["theta"]) == (h2[-1]["theta"], h2[-1]["se"])


def test_old_levels_rows_load_and_the_header_is_upgraded(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "LEVELS", tmp_path / "levels.csv")
    old_header = store.LEVELS_HEADER[:9]
    store.LEVELS.write_text(",".join(old_header) + "\n2026-09-15,2026-09-15_214411_04cc,3k-b,60,85,5,75,0.0,1\n", encoding="utf-8")
    rows = store.load_levels()
    assert rows[0]["theta"] == "" and rows[0]["se"] == "" and rows[0]["level"] == "3k-b"
    hist, trend = learn.level_history(rows)
    assert hist[0]["theta"] is None and hist[0]["se"] is None and hist[0]["level"] == "3k-b" and trend == ""
    assert learn.retest_due(rows, "4k-a")["subband"] == "4k-a"
    r = progress.build([], rows, SUBBANDS)
    assert r["tests"] == 1 and r["theta"] is None and r["level"] is None and r["thetas"] == []
    md = progress.render_markdown(r, SUBBANDS)
    assert "No level yet · frontier 1k-a · 1 test (1 reliable) · last test 3k-b" in md and "_No reliable test yet" in md
    # the next finished session upgrades the header once; the old row is padded
    s = run(Session.create("2026-09-20_100000_abcd", SUBBANDS, FakeBank(), seed=1), lambda it: it.real and it.b < 7)
    s.started = datetime(2026, 9, 20, 10, 0, 0)
    store.save_result(s, s.result())
    with store.LEVELS.open(newline="", encoding="utf-8") as f:
        raw = list(csv.reader(f))
    assert raw[0] == store.LEVELS_HEADER and raw[1] == ["2026-09-15", "2026-09-15_214411_04cc", "3k-b", "60", "85", "5", "75", "0.0", "1", "", ""]
    assert raw[2][:3] == ["2026-09-20", "2026-09-20_100000_abcd", s.result().level] and float(raw[2][9]) == s.theta
    rows = store.load_levels()
    assert rows[0]["theta"] == "" and float(rows[1]["theta"]) == s.theta and float(rows[1]["se"]) == s.se
    hist, _ = learn.level_history(rows)
    assert hist[1]["theta"] == s.theta and hist[0]["theta"] is None
    store.save_result(s, s.result())                              # a second append: no second upgrade
    with store.LEVELS.open(newline="", encoding="utf-8") as f:
        assert len(list(csv.reader(f))) == 4


def test_docs_written_and_linked():
    doc = (ROOT / "docs" / "det-adaptive.md").read_text(encoding="utf-8")
    for needle in ("```mermaid", "1.156", "0.35", "det_low", "Rasch", "pos_in_band / 500", "A = 1.5"):
        assert needle in doc, needle
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/det-adaptive.md" in readme.split("## Test your level")[1].split("\n## ")[0]
    assert "det-adaptive.md" in (ROOT / "docs" / "sentences.md").read_text(encoding="utf-8")
    assert re.search(r"\| `theta`", (ROOT / "vocab" / "tests" / "README.md").read_text(encoding="utf-8"))
