"""One priority pool for every drill (issue #13): learn.priority_pool(), drills.pick_weighted(), the drills drawing
from it, the reason chip, the hits that close a my-words row, the counts on the practice page and in the report."""
import json
import random
from collections import Counter
from datetime import date

from fastapi.testclient import TestClient

from app import drills, learn, main, progress, store
from tests.test_adaptive import learner_at
from tests.test_dictation import row
from tests.test_drills import attempts, practice  # noqa: F401  (the fixture)
from tests.test_learn import fake_index, session


def test_priority_pool_is_the_study_list_with_tiers_and_weights():
    s = session("s", learner_at(5), 4, "2026-09-05T10:00:00")
    stats = learn.word_stats([s])
    front = s["result"]["frontier"]
    index = fake_index(set(stats) | {f"{front}-w{i}" for i in range(100)})
    missed = [w.word for w in stats.values() if w.status == "missed"]
    my_words = [{"date": "2026-09-06", "family": "serendipity", "source": "learn", "note": "", "done": ""},     # extra: no sentence
                {"date": "2026-09-06", "family": missed[0], "source": "listen-and-type", "note": "", "done": ""},
                {"date": "2026-09-07", "family": f"{front}-w3", "source": "listen-and-type:hearing", "note": "", "done": ""}]
    pool = learn.priority_pool(stats, my_words, index, front)
    assert "serendipity" not in pool and pool[missed[0]] == ("my-words", 6) and pool[f"{front}-w3"] == ("my-words", 6)
    study = learn.study_list(stats, front, index, {}, my_words, 10_000)
    assert [w["family"] for w in study if w["family"] != "serendipity"] == list(pool)          # same order, no cap
    reasons = {w["family"]: w["reason"] for w in study}
    assert all(pool[f][0] == ("my-words" if reasons[f] == learn.HEARD_WRONG else reasons[f]) for f in pool)
    assert {r for r, _ in pool.values()} <= set(learn.WEIGHTS) and all(w == learn.WEIGHTS[r] for r, w in pool.values())
    assert all(pool[m][0] == "missed" and pool[m][1] == 3 for m in missed[1:])
    assert not [f for f, (r, _) in pool.items() if r == "frontier"]              # fake families are not bank.WORD headwords
    c = learn.pool_counts(pool)
    assert c["total"] == c["my-words"] + c["repeat"] + c["missed"] + c["shaky"] == len(pool) and c["my-words"] == 2 and c["frontier"] == 0
    assert learn.reason_label(missed[0], "my-words", stats, my_words) == f"my-words · listen-and-type 2026-09-06"
    assert learn.reason_label(missed[1], "missed", stats) == "missed in the test" and learn.reason_label("x", "frontier", stats) == ""
    stats[missed[1]].missed = 2
    assert learn.reason_label(missed[1], "repeat", stats) == "missed 2× in the test" and learn.reason_label("x", "shaky", stats) == "shaky"
    # the real bank, no history: the frontier sub-band by rank, weight 1, never-shown words only
    real = learn.priority_pool(stats, [], main.BANK.index, "1k-a")
    frontier = [f for f, (r, _) in real.items() if r == "frontier"]
    assert frontier == [r["family"] for r in sorted(main.BANK.index.values(), key=lambda r: int(r["rank"]))
                        if r["subband"] == "1k-a" and learn.WORD.match(r["family"]) and r["family"] not in stats]
    assert all(real[f] == ("frontier", 1) for f in frontier) and learn.pool_counts(real)["frontier"] == len(frontier) > 300
    assert learn.pool_counts(real)["total"] == 0 and learn.priority_pool({}, [], {}, "1k-a") == {}


def test_pick_weighted():
    items = [{"id": f"p{i}", "w": 6} for i in range(10)] + [{"id": f"f{i}", "w": 1} for i in range(500)] + [{"id": "z", "w": 0}]
    rng = random.Random(13)
    draws = Counter(drills.pick_weighted(items, set(), lambda p: p["id"], lambda p: p["w"], rng)["id"] for _ in range(1000))
    per_priority = sum(draws[f"p{i}"] for i in range(10)) / 10
    per_frontier = sum(draws[f"f{i}"] for i in range(500)) / 500
    assert per_priority > 10 * per_frontier and draws["z"] == 0                       # a tier-1 item far likelier than a frontier one
    assert 400 < sum(draws[f"p{i}"] for i in range(10)) < 700                           # PRIORITY_SHARE 0.5 + the weighted union
    assert sum(draws[f"p{i}"] for i in range(10)) + sum(draws[f"f{i}"] for i in range(500)) == 1000
    # share 0: the weighted union only (10 · 6 / 560 ≈ 11 %); share 1: priority items only
    union = Counter(drills.pick_weighted(items, set(), lambda p: p["id"], lambda p: p["w"], rng, share=0)["id"] for _ in range(1000))
    assert 60 < sum(union[f"p{i}"] for i in range(10)) < 170
    assert all(drills.pick_weighted(items, set(), lambda p: p["id"], lambda p: p["w"], rng, share=1)["id"].startswith("p") for _ in range(50))
    # recent items are never drawn while others remain; once everything was shown, pick() over the weighted ones
    recent = {f"p{i}" for i in range(10)} | {f"f{i}" for i in range(499)}
    assert all(drills.pick_weighted(items, recent, lambda p: p["id"], lambda p: p["w"], rng)["id"] == "f499" for _ in range(20))
    recent.add("f499")
    seen = {drills.pick_weighted(items, recent, lambda p: p["id"], lambda p: p["w"], rng)["id"] for _ in range(200)}
    assert "z" not in seen and len(seen) > 20
    assert drills.pick_weighted([], set(), lambda p: p["id"], lambda p: p["w"], rng) is None
    assert drills.pick_weighted([{"id": "z", "w": 0}], set(), lambda p: p["id"], lambda p: p["w"], rng) is None


def test_drills_draw_from_the_pool(practice, monkeypatch):
    """Empty history: the frontier as today. A my-words row whose family has a sentence: with share 1 every
    dictation, Read Aloud and cloze item targets it, with the reason chip; the result marks the target."""
    c = TestClient(main.app)
    for task in ("listen-and-type", "read-aloud", "read-and-complete", "fill-in-the-blanks"):
        d = c.get(f"/api/drill/{task}/next?mode=sentence").json()
        assert d["subband"] == "1k-a" and d["reason"] == "" and d["reason_label"] == "" and d["family"] in main.BANK.index
        assert d["forms"][0] == d["family"] and set(d["forms"]) >= {d["family"]}
    assert c.get("/api/config").json()["pool"]["total"] == 0
    learn.add_my_word("tenant", "test", "", date(2026, 9, 14))                  # has a 6–14-word sentence in the bank
    monkeypatch.setattr(drills, "PRIORITY_SHARE", 1.0)
    cfg = c.get("/api/config").json()
    assert cfg["pool"] == {"total": 1, "my-words": 1, "repeat": 0, "missed": 0, "shaky": 0, "frontier": cfg["pool"]["frontier"]}
    assert cfg["pool"]["frontier"] > 100
    for task in ("listen-and-type", "read-aloud", "fill-in-the-blanks"):
        d = c.get(f"/api/drill/{task}/next").json()
        assert d["family"] == "tenant" and d["subband"] == "4k-a" and d["id"].startswith("tenant.")
        assert d["reason"] == "my-words" and d["reason_label"] == "my-words · test 2026-09-14" and "tenants" in d["forms"]
    d = c.get("/api/drill/read-and-complete/next?mode=sentence").json()
    family, sense, seed = drills.parse_cloze_id(d["id"], main.BANK.index)
    assert family == "tenant" and d["reason"] == "my-words" and d["reason_label"] == "my-words · test 2026-09-14"
    text = main.EXAMPLES[(family, str(sense))]
    gen = drills.cloze(text, seed, drills.forms_pattern(family, drills.members(main.BANK.index[family])))
    assert any(a.lower().startswith("tenan") for a in gen["answers"])                          # the target is damaged
    r = c.post(f"/api/drill/read-and-complete/{d['id']}", json={"attempt": d["attempt"], "typed": gen["answers"], "ms": 1000}).json()
    assert r["family"] == "tenant" and "tenants" in r["forms"] and r["score"] == 1.0 and r["done"] == []
    rows = attempts(practice / "attempts.csv")
    assert "tenant:hit" in rows[-1]["events"].split("|")                                        # the target's blank was right (#17: every blank)
    # a wrong target blank is a vocabulary miss for the family; the sentence itself is not drawn again this week
    r = c.post(f"/api/drill/read-and-complete/{d['id']}", json={"attempt": drills.attempt_id(), "typed": [], "ms": 1000}).json()
    assert "tenant:vocabulary" in attempts(practice / "attempts.csv")[-1]["events"].split("|") and r["score"] == 0
    assert not c.get("/api/drill/read-and-complete/next?mode=sentence").json()["id"].startswith("tenant.")
    # the pool line of the report and of /api/learn agree with /api/config
    p = c.get("/api/progress").json()
    assert p["pool"]["total"] >= 2 and p["pool"]["my-words"] >= 2 and p["pool"]["done"] == {"cards": 0, "practice": 0}   # the wrong blanks joined
    assert {k: v for k, v in p["pool"].items() if k != "done"} == c.get("/api/config").json()["pool"]
    assert c.get("/api/learn").json()["pool"] == p["pool"]


def test_hits_on_two_days_close_the_row(tmp_path, monkeypatch):
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "my-words.csv")
    learn.add_my_word("evict", "listen-and-type", "", date(2026, 9, 10))
    learn.add_my_word("tenant", "test", "", date(2026, 9, 10))
    learn.add_my_word("rent", "learn", "", date(2026, 9, 12))
    a = [row("2026-09-08", "listen-and-type", "a.1", 1.0, events="evict:hit|tenant:hit|rent:hit"),   # before the rows: not counted
         row("2026-09-11", "listen-and-type", "a.2", 1.0, events="evict:hit|tenant:hit|rent:hit"),
         row("2026-09-12", "read-and-complete", "b.1.1", 1.0, events="evict:hit|tenant:vocabulary"),
         row("2026-09-13", "listen-and-type", "a.3", 0.9, events="tenant:hit|rent:hit", hhmmss="090000")]
    hits = learn.drill_hits(a)
    assert hits["evict"] == {"2026-09-08", "2026-09-11", "2026-09-12"} and hits["tenant"] == {"2026-09-13"} and hits["rent"] == {"2026-09-08", "2026-09-11", "2026-09-13"}
    assert learn.drill_hits(a, "2026-09-12")["rent"] == {"2026-09-13"}
    done = learn.practice_done(learn.load_my_words(), a, date(2026, 9, 13))
    assert done == ["evict"]                                # tenant: a hit, then an error, then one day; rent: one day since its row
    rows = learn.load_my_words()
    assert [(r["family"], r["done"]) for r in rows] == [("evict", "2026-09-13 practice"), ("tenant", ""), ("rent", "")]
    assert learn.done_counts(rows) == {"cards": 0, "practice": 1} and [r["family"] for r in learn.open_my_words(rows)] == ["rent", "tenant"]
    a.append(row("2026-09-14", "listen-and-type", "a.4", 1.0, events="tenant:hit|rent:hit"))
    assert learn.practice_done(learn.load_my_words(), a, date(2026, 9, 14)) == ["tenant", "rent"]
    assert learn.practice_done(learn.load_my_words(), a, date(2026, 9, 14)) == []           # nothing open
    learn.mark_done(["nobody"], date(2026, 9, 14))
    learn.add_my_word("evict", "test", "", date(2026, 9, 15))
    learn.mark_done(["evict"], date(2026, 9, 16))
    assert learn.done_counts(learn.load_my_words()) == {"cards": 1, "practice": 3}


def test_practice_closes_rows_through_the_api(practice):
    s = session("s", learner_at(6), 4, "2026-09-10T10:00:00")
    store.SESSIONS.mkdir(parents=True, exist_ok=True)
    (store.SESSIONS / "s.json").write_text(json.dumps(s), encoding="utf-8")
    c = TestClient(main.app)
    d = c.get("/api/drill/listen-and-type/next").json()
    ref = next(r for r in drills.load_sentences() if r["id"] == d["id"])
    families = [e["family"] for e in drills.dictation_events(drills.dictation_score(ref["sentence"], ref["sentence"])["diff"], 6.0, main.LEX)]
    assert families
    for f in families:
        learn.add_my_word(f, "test", "", date(2026, 9, 1))
    # one hit day: still open; a second day (an older row on record) closes them
    r = c.post(f"/api/drill/listen-and-type/{d['id']}", json={"attempt": d["attempt"], "typed": ref["sentence"], "ms": 1000}).json()
    assert r["done"] == [] and all(not m["done"] for m in learn.load_my_words())
    store.append_attempt({"date": "2026-09-05", "attempt": "2026-09-05_101010_abcd", "task": "listen-and-type", "item": d["id"],
                          "score": 1.0, "events": "|".join(f"{f}:hit" for f in families)})
    d2 = c.get("/api/drill/read-aloud/next").json()
    r2 = c.post(f"/api/drill/read-aloud/{d2['id']}", json={"attempt": d2["attempt"], "ms": 1000, "rating": [3, 3, 3, 3]}).json()
    assert sorted(r2["done"]) == sorted(families)
    rows = learn.load_my_words()
    assert all(m["done"] == f"{date.today().isoformat()} practice" for m in rows)
    l = c.get("/api/learn").json()
    assert l["counts"]["done-practice"] == len(families) and l["counts"]["done-cards"] == 0 and l["counts"]["my-words"] == 0
    assert c.get("/api/progress").json()["pool"]["done"] == {"cards": 0, "practice": len(families)}


def test_report_pool_line():
    r = progress.build([], [], main.SUBBANDS, date(2026, 9, 18), index=main.BANK.index,
                       my_words=[{"date": "2026-09-10", "family": "evict", "source": "test", "note": "", "done": ""},
                                 {"date": "2026-09-10", "family": "rent", "source": "test", "note": "", "done": "2026-09-11"},
                                 {"date": "2026-09-10", "family": "tenant", "source": "test", "note": "", "done": "2026-09-12 practice"}])
    assert r["pool"]["total"] == r["pool"]["my-words"] == 1 and r["pool"]["done"] == {"cards": 1, "practice": 1}
    md = progress.render_markdown(r, main.SUBBANDS)
    assert f"\n\nPriority pool: 1 word (my-words 1 · repeat 0 · missed 0 · shaky 0) · frontier {r['pool']['frontier']} · my-words done: 1 by cards, 1 by practice\n\n### Listening" in md
