"""The learner (issue #6): word statuses, frontier, study order, Anki export, API."""
from datetime import date, datetime

from fastapi.testclient import TestClient

from app import learn, main, store
from app.adaptive import Session
from app.bank import load_subbands
from tests.test_adaptive import NAMES, FakeBank, learner_at, run

SUBBANDS = load_subbands()


def session(sid: str, knows, seed: int, when: str) -> dict:
    s = run(Session.create(sid, SUBBANDS, FakeBank(), seed=seed), knows)
    s.started = datetime.fromisoformat(when)
    return store.session_dict(s)


def fake_index(words):
    return {w: {"family": w, "subband": w.split("-w")[0], "rank": str(1000 + i), "members": f"{w}s|{w}ed",
                "definition": f"def {w}", "example": ""} for i, w in enumerate(sorted(words))}


def test_word_status_over_two_sessions():
    """A word missed once then answered right is learned; missed twice is repeat; slow answers are shaky."""
    bank = FakeBank()
    a = session("a", learner_at(5), 1, "2026-09-01T10:00:00")
    b = session("b", learner_at(5), 1, "2026-09-08T10:00:00")     # same seed → same words
    # session a: every 6k-a.. word is missed (learner_at(5) rejects them); make one 5k word missed in a only
    target = next(i["word"] for blk in a["blocks"] for i in blk["items"] if i["real"] and i["answer"])
    for blk in a["blocks"]:
        for i in blk["items"]:
            i["ms"] = 1000
            if i["word"] == target:
                i["answer"] = False
    for blk in b["blocks"]:
        for i in blk["items"]:
            i["ms"] = 1000
            if i["word"] == target:
                i["ms"] = 5000                                       # right this time, but slow
    stats = learn.word_stats([b, a])                                 # order on disk must not matter
    assert stats[target].status == "learned" and stats[target].missed == 1 and stats[target].slow
    missed_twice = [w for w in stats.values() if w.missed == 2]
    assert missed_twice and all(w.status == "repeat" for w in missed_twice)
    only_b = {w.word for w in stats.values() if w.shown == 1}
    assert not only_b                                                # same items in both sessions
    # a quick correct word is known; make one slow
    quick = next(w for w in stats.values() if w.status == "known")
    for blk in b["blocks"]:
        for i in blk["items"]:
            if i["word"] == quick.word:
                i["ms"] = 2001
    assert learn.word_stats([a, b])[quick.word].status == "shaky"


def blocks_session(sid: str, when: str, blocks, reliable=True) -> dict:
    """A finished session from (subband, hits, false_alarms) triples."""
    def block(no, sb, hits, fa):
        items = [{"word": f"{sb}-w{no}{i}", "real": True, "definition": "", "answer": i < hits, "ms": 1000} for i in range(10)]
        items += [{"word": f"{sb}-x{no}{i}", "real": False, "definition": "", "answer": i < fa, "ms": 1000} for i in range(5)]
        return {"no": no, "subband": sb, "pos": 15, "hits": hits, "false_alarms": fa, "score": hits / 10 - fa / 5, "items": items}
    return {"id": sid, "started": when, "finished": True, "stop_reason": "test",
            "blocks": [block(n + 1, *b) for n, b in enumerate(blocks)], "result": {"reliable": reliable}}


def test_frontier_and_recency_weighting():
    old = blocks_session("old", "2026-09-01T10:00:00", [("4k-a", 7, 0), ("3k-b", 9, 0), ("4k-a", 7, 1)])
    new = blocks_session("new", "2026-09-10T10:00:00", [("4k-a", 10, 0), ("4k-b", 6, 0), ("4k-a", 10, 0)])
    by = {s["subband"]: s for s in learn.subband_scores([old, new], SUBBANDS)}
    assert by["4k-a"]["blocks"] == 4 and by["3k-b"]["status"] == "mastered" and by["4k-b"]["status"] == "not yet"
    assert by["4k-a"]["score"] == round((7 * .5 + 7 * .5 + 10 + 10) / (20 * .5 + 20) - (1 * .5) / (10 * .5 + 10), 4)
    assert by["4k-a"]["status"] == "mastered"                        # 0.867: the two old 7/10 blocks weigh half
    assert by["5k-a"]["status"] == "untested"
    assert learn.frontier(learn.subband_scores([old, new], SUBBANDS)) == ("4k-a", "4k-b")
    # the old session alone: 4k-a not mastered → frontier 4k-a
    assert learn.frontier(learn.subband_scores([old], SUBBANDS)) == ("3k-b", "4k-a")
    # unreliable sessions are ignored; a failed sub-band below the level is the frontier
    guess = blocks_session("g", "2026-09-11T10:00:00", [("4k-a", 10, 5)], reliable=False)
    assert learn.frontier(learn.subband_scores([guess], SUBBANDS)) == (None, "1k-a")
    gap = blocks_session("gap", "2026-09-12T10:00:00", [("3k-a", 6, 0), ("3k-b", 9, 0)])
    assert learn.frontier(learn.subband_scores([gap], SUBBANDS)) == ("3k-b", "3k-a")
    weak = session("w", learner_at(0), 1, "2026-09-11T10:00:00")
    assert learn.frontier(learn.subband_scores([weak], SUBBANDS)) == (None, "1k-b")


def test_study_list_order_and_export(tmp_path):
    s = session("s", learner_at(5), 4, "2026-09-05T10:00:00")
    for blk in s["blocks"]:
        for i in blk["items"]:
            i["ms"] = 1000
    stats = learn.word_stats([s])
    scores = learn.subband_scores([s], SUBBANDS)
    level, front = learn.frontier(scores)
    assert level == NAMES[5] and front == NAMES[6]
    shown = set(stats)
    index = fake_index(shown | {f"{front}-w{i}" for i in range(100)})
    syn = {w: ["alpha", "beta"] for w in shown}
    words = learn.study_list(stats, front, index, syn, 200)
    reasons = [w["reason"] for w in words]
    assert reasons == sorted(reasons, key=["repeat", "missed", "shaky", "frontier"].index)
    missed = [w for w in words if w["reason"] == "missed"]
    assert missed and missed[0]["subband"] == front                  # frontier misses first
    assert all(w["reason"] == "frontier" and w["family"] not in stats for w in words if w["subband"] == front and w["reason"] == "frontier")
    frontier_rest = [w["rank"] for w in words if w["reason"] == "frontier"]
    assert frontier_rest == sorted(frontier_rest)
    assert {w["family"] for w in words} >= {w.word for w in stats.values() if w.status == "missed"}
    assert not any(w["family"] in {x.word for x in stats.values() if x.status in ("known", "learned")} for w in words)
    assert words[0]["synonyms"] == ["alpha", "beta"] and words[0]["members"] == [words[0]["family"] + "s", words[0]["family"] + "ed"]
    assert len(learn.study_list(stats, front, index, syn, 7)) == 7

    path = learn.export_anki(words[:3], date(2026, 9, 12), tmp_path / "decks")
    lines = path.read_text().splitlines()
    assert path.name == "2026-09-12.txt" and lines[0] == "#separator:tab"
    assert len(lines) == 3 + 3
    front_, back, tags = lines[3].split("\t")
    assert front_ == words[0]["family"] and "def " in back and "= alpha, beta" in back and tags.endswith(words[0]["reason"])


def test_level_history_trend():
    rows = [{"date": "2026-09-01", "session": "a", "level": "3k-b", "det_low": "60", "det_high": "85", "reliable": "1"},
            {"date": "2026-09-02", "session": "b", "level": "", "det_low": "", "det_high": "", "reliable": "0"},
            {"date": "2026-09-08", "session": "c", "level": "4k-a", "det_low": "90", "det_high": "105", "reliable": "1"}]
    hist, trend = learn.level_history(rows)
    assert trend == "up" and hist[1]["level"] is None and not hist[1]["reliable"]
    assert learn.level_history(rows[:1])[1] == ""


def test_learn_api_and_export(tmp_path, monkeypatch):
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    monkeypatch.setattr(learn, "DECKS", tmp_path / "decks")
    c = TestClient(main.app)
    empty = c.get("/api/learn").json()
    assert empty["sessions"] == 0 and empty["level"] is None and empty["frontier"] == "1k-a"
    assert empty["words"] and all(w["reason"] == "frontier" for w in empty["words"]) and len(empty["words"]) == 20

    sid = c.post("/api/session").json()["session"]
    status = None
    while status != "finished":
        r = c.post(f"/api/session/{sid}/answer", json={"yes": False, "ms": 800}).json()
        status = r["status"]
        if status == "block_done":
            c.post(f"/api/session/{sid}/next")
    d = c.get("/api/learn?n=15").json()
    assert d["sessions"] == 1 and d["counts"]["missed"] == 60 and d["level"] is None and d["frontier"] == "1k-b"
    assert len(d["words"]) == 15 and all(w["reason"] == "missed" for w in d["words"])
    assert d["words"][0]["subband"] == "1k-b"
    e = c.post("/api/learn/export?n=15").json()
    assert e["cards"] == 15 and e["file"].endswith(".txt") and (tmp_path / "decks").exists()
