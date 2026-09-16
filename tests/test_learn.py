"""The learner (issue #6): word statuses, frontier, study order, Anki export, API.
Phase 3 (issue #8): recall-card gaps, my-words, sub-band decks, the re-test date."""
import csv
from datetime import date, datetime, timedelta

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
    words = learn.study_list(stats, front, index, syn, [], 200)
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
    assert len(learn.study_list(stats, front, index, syn, [], 7)) == 7

    path = learn.export_anki("2026-09-12", words[:3], tmp_path / "decks")
    lines = path.read_text().splitlines()
    assert path.name == "2026-09-12.txt" and lines[0] == "#separator:tab"
    assert "#notetype:DET family" in lines and "#deck:DET::2026-09-12" in lines and "#tags column:8" in lines
    header = len([l for l in lines if l.startswith("#")])
    assert len(lines) == header + 3 and all(len(l.split("\t")) == 8 for l in lines[header:])
    word, forms, definition, example, gap, hint, synonyms, tags = lines[header].split("\t")
    assert word == words[0]["family"] and forms == f"{word}s, {word}ed" and definition == f"def {word}"
    assert example == "" and gap == "" and hint == learn.hint(word) and synonyms == "alpha, beta"
    assert tags == f"{words[0]['subband']} {words[0]['reason']}"


def test_gap_and_hint():
    members = ["unutterable", "utterance", "uttered", "uttering", "utters"]
    assert learn.gap("utter", members, "an arrant fool") == ""                  # sense 1: no form in it
    assert learn.gap("utter", members, "utter seriousness") == "_____ seriousness"
    assert learn.gap("utter", members, "He uttered utter nonsense") == "He _____ utter nonsense"   # longest form, once
    assert learn.gap("utter", members, "Utterly lost") == ""                    # whole words only
    assert learn.gap("utter", members, "UTTER chaos") == "_____ chaos"
    assert learn.hint("utter") == "u _ _ _ _" and learn.hint("go") == "g _"
    # card_entry scans the examples in order and falls back to the note, then to sense 1 + hint only
    index = {"utter": {"family": "utter", "subband": "4k-a", "rank": "3060", "members": "|".join(members),
                       "definition": "complete", "example": "an arrant fool"}}
    ex = {"utter": ["an arrant fool", "utter seriousness", "She expressed her anger"]}
    e = learn.card_entry("utter", "new", index, {}, {}, examples=ex)
    assert e["example"] == "utter seriousness" and e["gap"] == "_____ seriousness" and e["hint"] == "u _ _ _ _"
    e = learn.card_entry("utter", "my-words", index, {}, {}, note="an utter mess", examples={"utter": ["an arrant fool"]})
    assert e["example"] == "an utter mess" and e["gap"] == "an _____ mess" and e["note"] == "an utter mess"
    e = learn.card_entry("utter", "new", index, {}, {}, examples={})
    assert e["example"] == "an arrant fool" and e["gap"] == "" and e["rank"] == 3060
    # an extra word: no index row → subband extra, no rank, the note is the definition
    e = learn.card_entry("serendipity", "my-words", index, {}, {}, note="a happy accident")
    assert e["subband"] == "extra" and e["rank"] is None and e["definition"] == "a happy accident"
    assert e["members"] == [] and e["synonyms"] == [] and e["example"] == "" and e["gap"] == ""


def test_my_words_in_study_list_and_export(tmp_path):
    s = session("s", learner_at(5), 4, "2026-09-05T10:00:00")
    stats = learn.word_stats([s])
    _, front = learn.frontier(learn.subband_scores([s], SUBBANDS))
    index = fake_index(set(stats) | {f"{front}-w{i}" for i in range(100)})
    known = next(w.word for w in stats.values() if w.status == "known")
    my_words = [{"date": "2026-09-06", "family": "serendipity", "source": "learn", "note": "a happy accident", "done": ""},
                {"date": "2026-09-06", "family": known, "source": "test", "note": "", "done": ""}]
    words = learn.study_list(stats, front, index, {}, my_words, 200)
    reasons = [w["reason"] for w in words]
    assert reasons == sorted(reasons, key=["repeat", "my-words", "missed", "shaky", "frontier"].index)
    mine = [w for w in words if w["reason"] == "my-words"]
    assert [w["family"] for w in mine] == ["serendipity", known]                  # a known word still shows when pinned
    assert mine[0]["subband"] == "extra" and mine[0]["rank"] is None and mine[0]["definition"] == "a happy accident"
    assert mine[1]["subband"] == known.split("-w")[0] and mine[1]["note"] == ""
    # one entry per family: a pinned miss is listed once, as my-words (repeat would still come first)
    missed = next(w.word for w in stats.values() if w.status == "missed")
    again = learn.study_list(stats, front, index, {}, [{"family": missed, "note": "", "done": ""}], 200)
    assert [w["reason"] for w in again if w["family"] == missed] == ["my-words"] and len(again) == len(words) - 2
    # the my-words deck and the tags
    path = learn.export_anki("my-words", learn.my_words_entries(my_words, index, stats, {}), tmp_path / "decks")
    rows = [l.split("\t") for l in path.read_text().splitlines() if not l.startswith("#")]
    assert path.name == "my-words.txt" and len(rows) == 2 and all(len(r) == 8 for r in rows)
    assert rows[0][0] == "serendipity" and rows[0][2] == "a happy accident" and rows[0][7] == "extra my-words"
    assert rows[1][7] == f"{known.split('-w')[0]} my-words"


def test_add_my_word_dedupe_and_done(tmp_path):
    path = tmp_path / "my-words.csv"
    assert learn.load_my_words(path) == []
    assert learn.add_my_word("Utter ", "test", "", date(2026, 9, 10), path)
    assert not learn.add_my_word("utter", "learn", "again", date(2026, 9, 11), path)         # open row → no-op
    assert learn.add_my_word("serendipity", "read-and-complete", "a  happy\naccident", date(2026, 9, 11), path)
    rows = learn.load_my_words(path)
    assert [r["family"] for r in rows] == ["utter", "serendipity"] and rows[1]["note"] == "a happy accident"
    assert rows[0] == {"date": "2026-09-10", "family": "utter", "source": "test", "note": "", "done": ""}
    assert [r["family"] for r in learn.open_my_words(rows)] == ["serendipity", "utter"]    # newest first
    assert learn.mark_done(["utter", "nothere"], date(2026, 9, 12), path) == 1
    rows = learn.load_my_words(path)
    assert rows[0]["done"] == "2026-09-12" and rows[1]["done"] == ""
    assert [r["family"] for r in learn.open_my_words(rows)] == ["serendipity"]
    assert learn.add_my_word("utter", "learn", "", date(2026, 9, 13), path)                  # done → can be added again
    assert learn.mark_done(["nothere"], date(2026, 9, 13), path) == 0
    with path.open(newline="") as f:
        assert next(csv.reader(f)) == learn.MY_WORDS_HEADER


def test_subband_deck_from_the_real_bank(tmp_path):
    """4k-a: 498 families (ms, et fail WORD), 8 columns, unshown families tagged new, utter from overrides.csv."""
    bank, syn = main.BANK, main.SYNONYMS
    entries = learn.subband_entries("4k-a", bank.index, {}, syn, bank.examples)
    assert len(entries) == 498 and "ms" not in {e["family"] for e in entries}
    assert [e["rank"] for e in entries] == sorted(e["rank"] for e in entries)
    assert all(e["reason"] == "new" and e["subband"] == "4k-a" for e in entries)
    utter = next(e for e in entries if e["family"] == "utter")
    assert utter["definition"] == "complete and absolute" and utter["gap"] == "It was an _____ failure."
    assert bank.examples["utter"][1:] == ["an arrant fool", "utter seriousness", "She expressed her anger"]
    assert sum(1 for e in entries if not e["gap"]) == 232
    stats = {"utter": learn.WordStat("utter", "4k-a", shown=1, missed=1, last="miss")}
    assert learn.subband_entries("4k-a", bank.index, stats, syn, bank.examples)[entries.index(utter)]["reason"] == "missed"
    path = learn.export_anki("4k-a", entries, tmp_path / "decks")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:4] == ["#separator:tab", "#html:true", "#notetype:DET family", "#deck:DET::4k-a"]
    rows = [l.split("\t") for l in lines if not l.startswith("#")]
    assert len(rows) == 498 and all(len(r) == 8 for r in rows) and len({r[0] for r in rows}) == 498
    assert all(r[5] and r[7] == "4k-a new" for r in rows)


def test_retest_due():
    rows = [{"date": "2026-09-01", "reliable": "1"}, {"date": "2026-09-03", "reliable": "0"}]
    assert learn.retest_due([], "4k-a", date(2026, 9, 5)) is None
    assert learn.retest_due(rows[1:], "4k-a", date(2026, 9, 5)) is None                  # unreliable: no clock
    assert learn.retest_due(rows, "4k-a", date(2026, 9, 5)) == {"subband": "4k-a", "last": "2026-09-01",
                                                                 "due": "2026-09-08", "days": 3}
    assert learn.retest_due(rows, "3k-b", date(2026, 9, 8))["days"] == 0
    assert learn.retest_due(rows, "3k-b", date(2026, 9, 10))["days"] == -2


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
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "my-words.csv")
    c = TestClient(main.app)
    empty = c.get("/api/learn").json()
    assert empty["sessions"] == 0 and empty["level"] is None and empty["frontier"] == "1k-a"
    assert empty["words"] and all(w["reason"] == "frontier" for w in empty["words"]) and len(empty["words"]) == 20
    assert empty["counts"]["my-words"] == 0 and c.get("/api/config").json()["retest"] is None

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
    deck = (tmp_path / "decks" / f"{date.today().isoformat()}.txt").read_text(encoding="utf-8").splitlines()
    assert "#notetype:DET family" in deck and all(len(l.split("\t")) == 8 for l in deck if not l.startswith("#"))

    # the re-test line: one reliable test → due RETEST_DAYS later, in the frontier sub-band
    r = c.get("/api/config").json()["retest"]
    assert r == {"subband": "1k-b", "last": date.today().isoformat(),
                 "due": (date.today() + timedelta(days=learn.RETEST_DAYS)).isoformat(), "days": learn.RETEST_DAYS}
    # the Re-test button: block 1 in the frontier sub-band, even though it is below the middle of the scale
    sid2 = c.post("/api/session?start=1k-b").json()["session"]
    for _ in range(15):
        c.post(f"/api/session/{sid2}/answer", json={"yes": False, "ms": 800})
    with (tmp_path / "results.csv").open(newline="") as f:
        first = next(r for r in csv.DictReader(f) if r["session"] == sid2)
    assert first["subband"] == "1k-b" and main.SESSIONS[sid2].blocks[0].subband == "1k-b"
    assert c.post("/api/session?start=9k-z").status_code == 400
    del main.SESSIONS[sid2]


def test_my_words_api(tmp_path, monkeypatch):
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    monkeypatch.setattr(learn, "DECKS", tmp_path / "decks")
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "my-words.csv")
    c = TestClient(main.app)
    assert c.get("/api/my-words").json() == {"open": 0, "words": []}
    r = c.post("/api/my-words", json={"family": "Utter", "source": "test"})
    assert r.status_code == 200 and r.json() == {"family": "utter", "source": "test", "note": "", "in_index": True}
    assert c.post("/api/my-words", json={"family": "utter", "source": "learn", "note": "x"}).status_code == 409
    assert c.post("/api/my-words", json={"family": "  ", "source": "learn"}).status_code == 422
    r = c.post("/api/my-words", json={"family": "serendipity", "source": "learn", "note": "a happy accident"})
    assert r.status_code == 200 and not r.json()["in_index"]
    mw = c.get("/api/my-words").json()
    assert mw["open"] == 2 and [w["family"] for w in mw["words"]] == ["serendipity", "utter"]

    d = c.get("/api/learn?n=5").json()
    assert d["counts"]["my-words"] == 2 and [w["reason"] for w in d["words"]] == ["my-words"] * 2 + ["frontier"] * 3
    extra, utter = d["words"][:2]
    assert extra["subband"] == "extra" and extra["rank"] is None and extra["definition"] == "a happy accident"
    assert utter["subband"] == "4k-a" and utter["definition"] == "complete and absolute" and utter["gap"]
    # the Learn button writes the batch and marks its my-words entries done; they drop out of the list
    e = c.post("/api/learn/export?n=5").json()
    assert e["cards"] == 5 and e["my_words_done"] == 2
    rows = [l.split("\t") for l in (tmp_path / "decks" / f"{date.today().isoformat()}.txt").read_text().splitlines()
            if not l.startswith("#")]
    assert rows[0][:3] == ["serendipity", "", "a happy accident"] and rows[0][7] == "extra my-words" and rows[1][7] == "4k-a my-words"
    assert c.get("/api/my-words").json()["open"] == 0
    assert all(w["reason"] == "frontier" for w in c.get("/api/learn?n=5").json()["words"])
    assert c.post("/api/my-words", json={"family": "utter", "source": "test"}).status_code == 200   # done → open again
