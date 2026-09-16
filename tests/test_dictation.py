"""Listen and Type on the DET scale (issue #16): the θ window, partial credit, the fractional θ update, the error
kinds, the word-status merge, the my-words skill rows and the Anki export, the attempts.csv columns, the API."""
import csv
import importlib.util
import json
import random
from datetime import date

from fastapi.testclient import TestClient

from app import drills, irt, learn, main, progress, store, textdiff, tts
from tests.test_adaptive import learner_at
from tests.test_drills import attempts, practice  # noqa: F401  (the fixture)
from tests.test_learn import session

LEX = main.LEX
ROOT = store.VOCAB.parent


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row(day, task, item, score, theta="", b="", events="", hhmmss="101010"):
    return {"date": day, "attempt": f"{day}_{hhmmss}_abcd", "task": task, "item": item, "subband": "", "seconds": "10",
            "timed_out": "0", "score": str(score), "self": "", "words": "", "errors": "", "file": "",
            "theta": str(theta), "b": str(b), "events": events}


# ---- selection --------------------------------------------------------------------------------------------

def test_window_widens_until_ten_candidates():
    rows = [{"id": str(i), "b_text": 3 + i * 0.5, "b_adjust": 0} for i in range(20)]          # b = 3, 3.5, … 12.5
    pool, width = drills.in_window(rows, 6.0, textdiff.b_of)
    assert width == 0.6 and [r["b_text"] for r in pool] == [5.5, 6.0, 6.5] or len(pool) >= 10
    assert all(abs(textdiff.b_of(r) - 6.0) <= width for r in pool)
    # 0.6 holds 3, 0.9 holds 3, 1.2 holds 5, 1.5 holds 7, 1.8 holds 7, 2.1 holds 9, 2.4 holds 9, 2.7 holds 11 → stop
    assert len(pool) == 11 and width == 2.7 and all(abs(textdiff.b_of(r) - 6.0) <= 2.7 for r in pool)
    # a window that already holds ten stays at 0.6; the whole list is the ceiling
    dense = [{"id": str(i), "b_text": 6 + (i % 10) * 0.1, "b_adjust": 0} for i in range(30)]
    pool, width = drills.in_window(dense, 6.5, textdiff.b_of)
    assert width == 0.6 and len(pool) == 30
    pool, width = drills.in_window(rows[:3], 20.0, textdiff.b_of)
    assert len(pool) == 3 and drills.in_window([], 6.0, textdiff.b_of) == ([], 0.6)
    # on the committed bank: every θ from 3 to 11 finds ten sentences inside ±1.5
    bank = textdiff.sentence_bank(main.SENSES, main.BANK.index, LEX, rng=random.Random(1)) if not drills.load_sentences() else drills.load_sentences()
    for theta in (3.0, 6.0, 8.1, 11.0):
        pool, width = drills.in_window(bank, theta, textdiff.b_of)
        assert len(pool) >= drills.DRILL_MIN and width <= 1.5 and all(abs(textdiff.b_of(r) - theta) <= width for r in pool)


# ---- credit and the θ update ---------------------------------------------------------------------------------

def test_credit():
    ref = "the landlord can evict a tenant who does not pay"       # 48 characters
    assert drills.credit(ref, ref) == 1.0 and drills.credit("Don't run — you'll be out!", "dont run youll be out") < 1
    assert drills.credit(ref, "The landlord can evict a tenant who does not pay.") == 1.0    # case and punctuation
    one_off = "the landlord can evict a tenent who does not pay"
    assert drills.credit(ref, one_off) == round(1 - 1 / 48, 4) == 0.9792
    assert abs(drills.credit("a" * 40, "a" * 39 + "b") - 0.975) < 1e-9                       # one letter in 40
    assert drills.credit(ref, "") == 0.0 and drills.credit("", "") == 0.0
    assert drills.credit(ref, "the landlord can evict") == round(1 - 26 / 48, 4)             # half typed ≈ half credit
    assert drills.levenshtein("kitten", "sitting") == 3 and drills.levenshtein("", "abc") == 3


def test_fractional_update_and_snap():
    theta, se = 6.0, 0.4
    up = irt.eap([(6.0, 1.0)], theta, se)[0]
    down = irt.eap([(6.0, 0.0)], theta, se)[0]
    half = irt.eap([(6.0, 0.5)], theta, se)[0]
    assert up > theta > down and abs(half - theta) <= 0.05
    assert irt.snap(1.0) == irt.snap(0.975) == irt.snap(0.95) == 1.0 and irt.snap(0.0) == irt.snap(0.2) == irt.snap(0.1) == 0.0
    assert irt.snap(0.5) == 0.5 and irt.snap(0.94) == 0.94
    # drill_theta: the test's posterior updated by the scored drill rows since it, dictation only for θ_listen
    history = [{"id": "t", "started": "2026-09-10T10:00:00", "theta": 6.0, "se": 0.4, "reliable": True}]
    assert learn.drill_theta(history, []) == (6.0, 0.4) and learn.drill_theta([], []) is None
    rows = [row("2026-09-11", "listen-and-type", "evict.1", 1.0, 6.0, 6.2),
            row("2026-09-12", "read-and-complete", "skip.1.4", 1.0, 6.0, 6.2),
            row("2026-09-09", "listen-and-type", "old.1", 0.0, 5.0, 6.0),           # before the test: not counted
            row("2026-09-13", "listen-and-type", "rent.1", 0.9, "", "")]            # no b (an old row): skipped
    shared = learn.drill_theta(history, rows)
    listen = learn.drill_theta(history, rows, ("listen-and-type",))
    assert shared[0] > listen[0] > 6.0 and shared[1] < listen[1] < 0.4
    assert learn.drill_responses(rows, "2026-09-10T10:00:00") == [(6.2, 1.0), (6.2, 1.0)]
    assert learn.attempt_time(rows[0]) == "2026-09-11T10:10:10"


# ---- event kinds -----------------------------------------------------------------------------------------------

def test_event_kinds_on_fixed_pairs():
    assert drills.error_kind("word", "ward", "word", LEX) == "hearing"
    assert drills.error_kind("tenant", "tennant", "tenant", LEX) == "spelling"
    assert drills.error_kind("evict", "evicts", "evict", LEX) == "form"
    assert drills.error_kind("evict", "avoid", "evict", LEX) == "vocabulary"
    assert drills.error_kind("rent", "went", "rent", LEX) == "hearing"          # a bank word one edit away: heard, not misspelt
    assert drills.error_kind("tenant", "tenent", "tenant", LEX) == "spelling"   # not a word, the reference's sound
    ref = "the landlord can evict a tenant who does not pay the rent"
    diff = drills.dictation_score(ref, "the landlord can a tenant who does not pay the")["diff"]
    ev = drills.dictation_events(diff, 6.0, LEX)
    kinds = {e["word"]: e["kind"] for e in ev}
    assert kinds["evict"] == "vocabulary" and kinds["rent"] == "hearing"       # missing: b ≥ θ − 1 / b < θ − 1
    assert kinds["landlord"] == kinds["tenant"] == kinds["pay"] == "hit"
    assert "the" not in kinds and "can" not in kinds and "who" not in kinds and "a" not in kinds   # function words
    assert "does" not in kinds and kinds["not"] == "hit"                        # `do` is not a test word (bank.WORD); `not` is
    assert [e["family"] for e in ev] == ["landlord", "evict", "tenant", "not", "pay", "rent"] and ev[1]["typed"] == ""
    diff = drills.dictation_score(ref, "the landlord can avoid a tennant who does not pay the rent extra")["diff"]
    kinds = {e["word"]: e["kind"] for e in drills.dictation_events(diff, 6.0, LEX)}
    assert kinds["evict"] == "vocabulary" and kinds["tenant"] == "spelling" and "extra" not in kinds
    assert drills.dictation_events(drills.dictation_score(ref, ref)["diff"], 6.0, LEX)[0]["kind"] == "hit"
    col = drills.events_column([{"family": "evict", "kind": "vocabulary"}, {"family": "rent", "kind": "hit"}])
    assert col == "evict:vocabulary|rent:hit" and drills.parse_events(col) == [("evict", "vocabulary"), ("rent", "hit")]
    assert drills.parse_events("") == [] and drills.parse_events("x:nope|y") == []


# ---- word_stats merge and my-words -------------------------------------------------------------------------------

def test_word_stats_merge():
    index = {"evict": {"subband": "6k-b"}, "rent": {"subband": "1k-b"}, "tenant": {"subband": "4k-a"}}
    a = [row("2026-09-11", "listen-and-type", "x.1", 0.5, events="evict:vocabulary|rent:hit|tenant:hearing")]
    st = learn.word_stats([], attempts=a, index=index)
    assert st["evict"].status == "missed" and st["evict"].missed == 1 and st["evict"].subband == "6k-b"
    assert st["rent"].status == "" and st["rent"].hits == ["2026-09-11"] and st["rent"].shown == 1
    assert st["tenant"].status == "" and st["tenant"].skill == "hearing" and st["tenant"].shown == 0
    assert learn.status_counts(st) == {"repeat": 0, "missed": 1, "learned": 0, "shaky": 0, "known": 0, "seen": 1}
    # a second hit the same day is still one day; on another day the word is known
    a.append(row("2026-09-11", "listen-and-type", "x.2", 1.0, events="rent:hit", hhmmss="120000"))
    assert learn.word_stats([], attempts=a, index=index)["rent"].status == ""
    a.append(row("2026-09-12", "listen-and-type", "x.3", 1.0, events="rent:hit|evict:vocabulary"))
    st = learn.word_stats([], attempts=a, index=index)
    assert st["rent"].status == "known" and st["rent"].shown == 3 and st["evict"].status == "repeat"
    # a later miss resets the hit days: two more hit days are needed
    a.append(row("2026-09-13", "listen-and-type", "x.4", 0.3, events="rent:vocabulary"))
    assert learn.word_stats([], attempts=a, index=index)["rent"].status == "missed"
    a.append(row("2026-09-14", "listen-and-type", "x.5", 1.0, events="rent:hit"))
    assert learn.word_stats([], attempts=a, index=index)["rent"].status == "missed"
    a.append(row("2026-09-15", "listen-and-type", "x.6", 1.0, events="rent:hit"))
    assert learn.word_stats([], attempts=a, index=index)["rent"].status == "learned"     # missed before, right now
    # the merge is chronological with the test: a test miss after the drill hits wins
    s = session("s", learner_at(5), 4, "2026-09-20T10:00:00")
    word = next(i["word"] for b in s["blocks"] for i in b["items"] if i["real"] and i["answer"] is False)
    a.append(row("2026-09-18", "listen-and-type", "y.1", 1.0, events=f"{word}:hit"))
    a.append(row("2026-09-19", "listen-and-type", "y.2", 1.0, events=f"{word}:hit"))
    st = learn.word_stats([s], attempts=a)
    assert st[word].status == "missed" and st[word].shown == 3 and st[word].hits == []
    assert learn.word_stats([s], attempts=a[:-1])[word].status == "missed"
    # rows from before #16 (no events) change nothing
    assert learn.word_stats([s], attempts=[{"date": "2026-09-01", "attempt": "2026-09-01_101010_aaaa", "task": "listen-and-type"}]) == learn.word_stats([s])


def test_skill_rows_show_as_heard_wrong_and_are_never_exported(tmp_path, monkeypatch):
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "my-words.csv")
    monkeypatch.setattr(learn, "DECKS", tmp_path / "decks")
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS", "ATTEMPTS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    assert learn.skill_source("listen-and-type:hearing") and learn.skill_source("read-and-complete:spelling")
    assert not learn.skill_source("listen-and-type") and not learn.skill_source("test") and not learn.skill_source("a:b")
    learn.add_my_word("tenant", "listen-and-type:hearing", "the landlord can evict a tenant", date(2026, 9, 14))
    learn.add_my_word("evict", "listen-and-type", "the landlord can evict a tenant", date(2026, 9, 14))
    learn.add_my_word("utter", "test", "", date(2026, 9, 14))
    open_ = learn.open_my_words(learn.load_my_words())
    words = learn.study_list({}, "4k-a", main.BANK.index, main.SYNONYMS, open_, 5)
    assert [(w["family"], w["reason"]) for w in words[:3]] == [("utter", "my-words"), ("evict", "my-words"), ("tenant", learn.HEARD_WRONG)]
    assert [e["family"] for e in learn.card_entries(words)] == ["utter", "evict"] + [w["family"] for w in words[3:]]
    assert [e["family"] for e in learn.my_words_entries(open_, main.BANK.index, {}, {})] == ["utter", "evict"]
    # scripts/export_anki.py --my-words: two notes, the hearing row neither exported nor marked done
    export = load_script("export_anki")
    assert export.main(["--my-words"]) == 0
    deck = (tmp_path / "decks" / "my-words.txt").read_text().splitlines()
    assert [l.split("\t")[0] for l in deck if not l.startswith("#")] == ["utter", "evict"]
    rows = learn.load_my_words()
    assert [(r["family"], bool(r["done"])) for r in rows] == [("tenant", False), ("evict", True), ("utter", True)]
    # the Learn button (--batch) the same way
    learn.add_my_word("evict", "listen-and-type", "again", date(2026, 9, 15))
    assert export.main(["--batch", "3"]) == 0
    batch = (tmp_path / "decks" / f"{date.today().isoformat()}.txt").read_text().splitlines()
    assert "tenant" not in [l.split("\t")[0] for l in batch] and "evict" in [l.split("\t")[0] for l in batch]
    assert [r["family"] for r in learn.open_my_words(learn.load_my_words())] == ["tenant"]
    c = TestClient(main.app)
    d = c.get("/api/learn?n=3").json()
    assert d["words"][0]["reason"] == learn.HEARD_WRONG and d["counts"]["my-words"] == 1
    assert c.post("/api/learn/export?n=3").json()["cards"] == 2                  # the heard-wrong row is not a card


# ---- attempts.csv and the report ------------------------------------------------------------------------------------

def test_old_attempts_rows_load_and_the_report_checks(tmp_path, monkeypatch):
    path = tmp_path / "attempts.csv"
    monkeypatch.setattr(store, "ATTEMPTS", path)
    old = store.ATTEMPTS_HEADER[:12]
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(old)
        w.writerow(["2026-09-16", "2026-09-16_101010_abcd", "listen-and-type", "rent.1", "1k-b", "20", "0", "0.8", "", "10", "rent>went", ""])
    rows = store.load_attempts()
    assert rows[0]["score"] == "0.8" and rows[0]["theta"] == "" and rows[0]["b"] == "" and rows[0]["events"] == ""
    store.append_attempt({"date": "2026-09-17", "attempt": "2026-09-17_101010_abcd", "task": "listen-and-type", "item": "rent.1",
                          "score": 0.9, "theta": 6.1, "b": 5.9, "events": "rent:hit"})
    text = path.read_text().splitlines()
    assert text[0] == ",".join(store.ATTEMPTS_HEADER) and text[1].endswith("rent>went,,,,") and text[2].endswith("0.9,,,,,6.1,5.9,rent:hit")
    rows = store.load_attempts()
    assert learn.drill_responses(rows) == [(5.9, 0.9)] and drills.recent_items(rows, "listen-and-type", date(2026, 9, 18)) == {"rent.1"}
    # the report: the listening line and the counts from the same rows; --check unaffected
    r = progress.build([], [], main.SUBBANDS, date(2026, 9, 18), attempts=rows, index=main.BANK.index)
    assert r["listening"] == {"attempts": 2, "kinds": {"form": 0, "hearing": 0, "spelling": 0, "vocabulary": 0}, "days": 14}
    assert r["theta"] is None and r["theta_listen"] is None and r["counts"]["seen"] == 0     # a hit on one day: no status yet
    md = progress.render_markdown(r, main.SUBBANDS)
    assert "\n### Listening\n\n2 dictations · errors in the last 14 days: form 0 · hearing 0 · spelling 0 · vocabulary 0\n\n### Ability" in md
    empty = progress.build([], [], main.SUBBANDS, date(2026, 9, 18))
    assert "### Listening\n\n_No dictation yet" in progress.render_markdown(empty, main.SUBBANDS)
    assert progress.check([], [], []) == []


# ---- the API ----------------------------------------------------------------------------------------------------------

def test_dictation_api_on_the_scale(practice):
    """With a reliable test on disk: the item comes from the θ window, the answer is scored as credit, θ moves,
    the row carries theta / b / events, the my-words sources say the kind, θ_listen and the report follow."""
    s = session("s", learner_at(6), 4, "2026-09-10T10:00:00")
    store.SESSIONS.mkdir(parents=True, exist_ok=True)
    (store.SESSIONS / "s.json").write_text(json.dumps(s), encoding="utf-8")
    c = TestClient(main.app)
    cfg = c.get("/api/config").json()
    theta0 = cfg["theta"]
    assert theta0 is not None and cfg["theta_test"] == theta0 and cfg["theta_listen"] == theta0 and 5.5 < theta0 < 7.5
    d = c.get("/api/drill/listen-and-type/next").json()
    assert d["theta"] == theta0 and d["window"] >= 0.6 and abs(d["b"] - theta0) <= d["window"]
    ra = c.get("/api/drill/read-aloud/next").json()
    assert abs(ra["b"] - theta0) <= ra["window"]
    row = next(r for r in drills.load_sentences() if r["id"] == d["id"])
    # right: credit 1, θ up, every content word a hit, nothing added
    r = c.post(f"/api/drill/listen-and-type/{d['id']}", json={"attempt": d["attempt"], "typed": row["sentence"].upper(), "ms": 20000, "plays": 1}).json()
    assert r["credit"] == r["score"] == 1.0 and r["theta"] == theta0 and r["theta_after"] > theta0 and r["se_after"] < cfg["se"]
    assert r["events"] and all(e["kind"] == "hit" for e in r["events"]) and r["added"] == [] and r["family"] == row["family"]
    rows = attempts(practice / "attempts.csv")
    assert float(rows[-1]["theta"]) == theta0 and float(rows[-1]["b"]) == d["b"] == textdiff.b_of(row)
    assert rows[-1]["events"] == drills.events_column(r["events"]) and rows[-1]["score"] == "1.0"
    cfg2 = c.get("/api/config").json()
    assert cfg2["theta"] == r["theta_after"] and cfg2["theta_listen"] == r["theta_after"] and cfg2["theta_test"] == theta0
    # wrong: the last content word dropped → a vocabulary or hearing miss with its source, θ down from where it was
    d2 = c.get("/api/drill/listen-and-type/next").json()
    assert d2["theta"] == cfg2["theta"]
    ref = next(x for x in drills.load_sentences() if x["id"] == d2["id"])["sentence"]
    words = drills.normalise(ref)
    r2 = c.post(f"/api/drill/listen-and-type/{d2['id']}", json={"attempt": d2["attempt"], "typed": "", "ms": 60000, "plays": 3, "timed_out": True}).json()
    assert r2["credit"] == 0.0 and r2["theta_after"] < r2["theta"] == cfg2["theta"]
    assert r2["events"] and all(e["kind"] in ("vocabulary", "hearing") and e["typed"] == "" for e in r2["events"])
    first = {}
    for e in r2["events"]:
        first.setdefault(e["family"], e["kind"])
    assert [(a["family"], a["kind"]) for a in r2["added"]] == list(first.items())          # once per family, its kind
    mine = {m["family"]: m["source"] for m in learn.load_my_words()}
    for e in r2["events"]:
        assert mine[e["family"]] == ("listen-and-type" if e["kind"] == "vocabulary" else f"listen-and-type:{e['kind']}")
    assert len(words) == r2["words"]
    # the report and the progress endpoint carry θ_listen and the error kinds
    p = c.get("/api/progress").json()
    assert p["theta_listen"] == r2["theta_after"] and p["theta_test"] == theta0 and p["listening"]["attempts"] == 2
    assert sum(p["listening"]["kinds"].values()) == len(r2["events"])
    # the refit: after five attempts at one sentence the bank's b_adjust moves and is written back
    store.ATTEMPTS.unlink()
    for i in range(6):
        store.append_attempt({"date": f"2026-09-1{i}", "attempt": f"2026-09-1{i}_101010_abcd", "task": "listen-and-type",
                              "item": d["id"], "score": 1.0, "theta": theta0 - 2, "b": d["b"]})
    d3 = c.get("/api/drill/listen-and-type/next").json()
    r3 = c.post(f"/api/drill/listen-and-type/{d3['id']}", json={"attempt": d3["attempt"], "typed": "", "ms": 100}).json()
    assert r3["credit"] == 0.0
    again = next(x for x in drills.load_sentences() if x["id"] == d["id"])
    assert float(again["b_adjust"]) < 0 and float(again["b_text"]) == float(row["b_text"])
    assert (practice / "sentences.csv").read_text().splitlines()[0] == ",".join(drills.SENTENCES_HEADER)
