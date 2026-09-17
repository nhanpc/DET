"""The task drills (issue #10): the cloze damage rule and item ids, the dictation score, the sentence bank,
the self-rating, the attempt log, wrong word → family → my-words, and the drill API end to end (TTS stubbed)."""
import csv
import random
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import drills, irt, learn, main, store, tts

SKIP = drills.forms_pattern("skip", ["skipped", "skipping", "skips"])
SENTENCE = "He skipped a row in the text and so the sentence was incomprehensible"


def test_damage_keeps_half_the_letters_min_one():
    assert drills.damage("skipped") == "ski____" and drills.damage("the") == "t__"
    assert drills.damage("incomprehensible") == "incompre________" and drills.damage("text") == "te__"
    assert drills.damage("ab") == "a_"                                   # odd, even and the minimum of one


def test_eligible_words():
    text = "Mary saw the Eiffel tower in Paris. It was 300 m tall, wasn't it? Yes."
    spans = drills.eligible(text)
    words = [text[a:b] for a, b in spans]
    assert words == ["Mary", "saw", "the", "tower", "was", "tall", "Yes"]  # names, numbers, apostrophes, <3 letters out
    after_first = [text[a:b] for a, b in drills.eligible(text, keep_first_sentence=True)]
    assert after_first == ["was", "tall", "Yes"]                          # passage mode: the first sentence is intact


def test_cloze_worked_example_and_parity():
    item = drills.cloze(SENTENCE, 7, SKIP)
    assert item["damaged"] == "He ski____ a row in t__ text a__ so the sent____ was incompre________"
    assert item["answers"] == ["skipped", "the", "and", "sentence", "incomprehensible"] and item["blanks"] == 5
    assert item["pieces"][0] == "He " and item["pieces"][1] == {"keep": "ski", "missing": 4}
    # the other parity: a target on an odd eligible index damages row, text, so, was
    row = drills.cloze(SENTENCE, 7, drills.forms_pattern("row", ["rows"]))
    assert row["answers"] == ["row", "text", "the", "was"]
    # target not eligible (two letters, a name) or fewer than two blanks → no item
    assert drills.cloze(SENTENCE, 1, drills.forms_pattern("in", [])) is None
    assert drills.cloze("Chaos was all that Utter saw", 1, drills.forms_pattern("utter", [])) is None
    assert drills.cloze("Just utter nonsense", 1, drills.forms_pattern("utter", [])) is None   # one blank only
    # pieces reassemble the text; the seed does not matter at ≤ 5 blanks
    assert all(drills.cloze(SENTENCE, s, SKIP) == item for s in (0, 1, 99))


def test_cloze_window_is_seeded_and_contains_the_target():
    text = "the quick brown fox jumps over the lazy dog while the small grey cat sleeps under the old wooden table"
    target = drills.forms_pattern("lazy", [])
    seen = set()
    for seed in range(40):
        it = drills.cloze(text, seed, target)
        assert it["blanks"] == 5 and "lazy" in it["answers"]
        assert it == drills.cloze(text, seed, target)                     # deterministic → replayable from the id
        seen.add(tuple(it["answers"]))
    assert len(seen) > 1                                                  # the seed moves the window
    # passage mode: first sentence intact, the 2nd, 4th… eligible word after it, a seeded window of 5
    passage = "Honey is a food made by bees. " + text + ". " + text + "."
    it = drills.cloze(passage, 3, passage=True)
    assert it["blanks"] == 5 and it["damaged"].startswith("Honey is a food made by bees. ")
    assert it == drills.cloze(passage, 3, passage=True)
    assert len({drills.cloze(passage, s, passage=True)["damaged"] for s in range(30)}) > 1
    assert drills.cloze("Only one sentence here.", 1, passage=True) is None


def test_score_cloze_and_ids():
    answers = ["skipped", "the", "and", "sentence", "incomprehensible"]
    score, wrong = drills.score_cloze(answers, ["skiped", "THE", "and", "sentance", "incomprehensible"])
    assert score == 0.6 and wrong == [("skipped", "skiped"), ("sentence", "sentance")]
    assert drills.score_cloze(answers, ["skipped"])[1] == [("the", ""), ("and", ""), ("sentence", ""), ("incomprehensible", "")]
    assert drills.score_cloze(answers, ["ski-pped!", "the", "and", "sentence", "incomprehensible"])[0] == 1.0   # letters only
    index = {"skip": {}, "sentence": {}}
    assert drills.parse_cloze_id(drills.cloze_id("skip", 1, 42), index) == ("skip", 1, 42)
    assert drills.parse_cloze_id(drills.passage_id("honey-bees", 7), index) == ("honey-bees", None, 7)
    assert drills.parse_cloze_id("a.b.7", index) == ("a.b", None, 7)      # not a family → a dotted slug
    with pytest.raises(ValueError):
        drills.parse_cloze_id("nodot", index)
    assert drills.errors_column([("sentence", "sentance"), ("the", ""), ("a;b", "x>y")]) == "sentence>sentance;the>;a b>x y"


def test_dictation_score_opcodes_and_clamp():
    """The word-level diff of #10 (`word_score`, `errors`, `wrong`, `diff`); `score` is the credit since #16."""
    r = drills.dictation_score("Don't run — you'll be out of breath.", "don't run you'll be out of breath")
    assert r["score"] == r["word_score"] == 1.0 and r["errors"] == [] and r["words"] == 7
    r = drills.dictation_score(SENTENCE, "he skiped a row in the text and the sentance was incomprehensible")
    assert r["errors"] == [("skipped", "skiped"), ("so", ""), ("sentence", "sentance")] and r["wrong"] == ["skipped", "so", "sentence"]
    assert r["word_score"] == round(1 - 3 / 13, 4) and 0.9 < r["score"] < 1      # 5 of 69 characters off
    # replace spans of unequal length count the longer side; a long wrong answer is clamped at 0
    r = drills.dictation_score("one two three", "one four five six three")
    assert r["errors"] == [("two", "four"), ("", "five"), ("", "six")] and r["word_score"] == 0.0
    r = drills.dictation_score("one two three", "a b c d e f g h")
    assert r["word_score"] == 0.0 and r["words"] == 3 and r["wrong"] == ["one", "two", "three"] and r["score"] < 0.2
    assert drills.dictation_score("one two three", "")["score"] == 0.0
    assert [d["op"] for d in drills.dictation_score("one two three", "one three")["diff"]] == ["equal", "delete", "equal"]


def test_sentence_bank_from_the_real_data():
    """Cloze candidates: ≥ 6 words with a family form; dictation bank: 6–14 words, one per family, sorted by rank."""
    bank = main.BANK
    cands = drills.sentence_candidates(main.SENSES, bank.index)
    per = {}
    for c in cands:
        per.setdefault(c["subband"], set()).add(c["family"])
    assert 120 <= len(per["4k-a"]) <= 170 and 120 <= len(per["6k-a"]) <= 130           # the counts in issue #10
    assert any(c["family"] == "skip" and c["example"] == SENTENCE for c in cands)
    assert all(drills.word_count(c["example"]) >= drills.MIN_WORDS for c in cands)
    rows = drills.build_sentences(main.SENSES, bank.index, random.Random(1))
    assert len(rows) == len({r["family"] for r in rows}) and all(r["id"] == f"{r['family']}." + r["id"].split(".")[1] for r in rows)
    assert all(drills.MIN_WORDS <= drills.word_count(r["sentence"]) <= drills.MAX_WORDS for r in rows)
    assert {r["voice"] for r in rows} <= set(drills.VOICES)
    assert 115 <= sum(1 for r in rows if r["subband"] == "4k-b") <= 170
    skip = next(r for r in rows if r["family"] == "skip")
    assert skip["id"] == "skip.1" and skip["sentence"] == SENTENCE and skip["subband"] == "4k-b"
    ranks = [int(bank.index[r["family"]]["rank"]) for r in rows if r["subband"] == "4k-a"]
    assert ranks == sorted(ranks)


def test_self_rating_draft_and_lacked():
    assert drills.self_rating([3, 3, 2, 2]) == 3 and drills.self_rating([3, 2, 2, 2]) == 2      # 2.5 → 3, 2.25 → 2
    assert drills.self_rating([5, 5, 5, 5]) == 5 and drills.self_rating([1, 1, 1, 2]) == 1
    with pytest.raises(ValueError):
        drills.self_rating([0, 3, 3, 3])
    md = drills.draft_text("interactive-writing", "iw-01", 480, 120, [("Write about a hobby.", "I like chess."), ("Follow up?", "It is hard.")])
    assert md.startswith("---\ntask: interactive-writing\nprompt: iw-01\nseconds: 480\nwords: 120\n---\n")
    assert "## Write about a hobby.\n\nI like chess.\n" in md and md.endswith("## Follow up?\n\nIt is hard.\n")
    assert drills.lacked_words(" Serendipity, uttered;\nutter,,") == ["serendipity", "uttered", "utter"]
    assert drills.lacked_words("") == []
    assert re.match(r"^\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{4}$", drills.attempt_id())


def test_family_lookup_and_recent_items():
    forms = drills.form_index(main.BANK.index)
    assert drills.family_of("Skipped", forms) == "skip" and drills.family_of("sentence", forms) == "sentence"
    assert drills.family_of("uttered", forms) == "utter" and drills.family_of("serendipity", forms) is None
    assert drills.family_of("is", forms) is None and drills.family_of("a", forms) is None       # bank.WORD: never shown
    rows = [{"date": "2026-09-08", "task": "read-and-complete", "item": "skip.1.42"},
            {"date": "2026-09-14", "task": "read-and-complete", "item": "row.2.7"},
            {"date": "2026-09-14", "task": "listen-and-type", "item": "row.2"},
            {"date": "2026-09-14", "task": "read-and-complete", "item": "honey.3"}]
    today = date(2026, 9, 16)
    assert drills.recent_items(rows, "read-and-complete", today) == {"row.2", "honey"}           # seed stripped; 8 days old is out
    assert drills.recent_items(rows, "read-and-complete", today, days=10) == {"skip.1", "row.2", "honey"}
    assert drills.recent_items(rows, "listen-and-type", today) == {"row.2"}
    assert drills.pick([1, 2, 3], {"1", "2"}, str, random.Random(0)) == 3
    assert drills.pick([1, 2], {"1", "2"}, str, random.Random(0)) in (1, 2)                     # all shown → any
    assert drills.pick([], set(), str, random.Random(0)) is None
    counts = drills.today_counts([{"date": "2026-09-16", "task": "read-aloud"}, {"date": "2026-09-16", "task": "read-aloud"},
                                  {"date": "2026-09-15", "task": "read-aloud"}, {"date": "2026-09-16", "task": "nope"}], today)
    assert counts["read-aloud"] == 2 and sum(counts.values()) == 2 and set(counts) == set(drills.TASKS)


def test_task_timings():
    t = drills.TASKS
    assert (t["read-aloud"]["seconds"], t["speak-photo"]["prep"], t["speak-photo"]["seconds"], t["speak-photo"]["min"]) == (20, 20, 90, 30)
    assert t["read-then-speak"] == t["speak-photo"] and t["listen-then-speak"]["plays"] == 3
    assert (t["write-photo"]["seconds"], t["read-then-write"]["seconds"], t["read-then-write"]["min"]) == (60, 300, 50)
    assert (t["interactive-writing"]["seconds"], t["interactive-writing"]["seconds2"]) == (300, 180)
    assert (t["read-and-complete"]["seconds"], t["read-and-complete"]["passage_seconds"], t["listen-and-type"]["seconds"]) == (60, 180, 60)
    assert drills.SPEAKING == ["read-aloud", "speak-photo", "read-then-speak", "listen-then-speak"]


def test_attempt_row_and_tts_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ATTEMPTS", tmp_path / "attempts.csv")
    store.append_attempt({"date": "2026-09-16", "attempt": "2026-09-16_101010_abcd", "task": "read-aloud", "item": "skip.1",
                          "timed_out": True, "self": 4, "score": None})
    rows = store.load_attempts()
    assert list(rows[0]) == store.ATTEMPTS_HEADER
    assert rows[0] == {"date": "2026-09-16", "attempt": "2026-09-16_101010_abcd", "task": "read-aloud", "item": "skip.1",
                       "subband": "", "seconds": "", "timed_out": "1", "score": "", "self": "4", "words": "", "errors": "", "file": "",
                       "theta": "", "b": "", "events": ""}
    calls = []
    monkeypatch.setattr(tts, "synthesise", lambda text, voice, path: (calls.append(text), path.write_bytes(b"ID3")))
    a = tts.audio("Hello there.", "af_heart", tmp_path / "audio")
    b = tts.audio("Hello there.", "af_heart", tmp_path / "audio")
    c = tts.audio("Hello there.", "am_michael", tmp_path / "audio")
    assert a == b and a != c and a.suffix == ".wav" and calls == ["Hello there.", "Hello there."]   # the second play needs no model
    assert a == tts.audio_path("Hello there.", "af_heart", tmp_path / "audio")
    # Kokoro is the engine (#21): an edge-tts voice name left in sentences.csv maps to one fixed Kokoro voice
    assert tts.voice_of("af_bella") == "af_bella" and tts.voice_of("en-GB-SoniaNeural") in tts.VOICES["kokoro"]
    assert tts.audio_path("x", "en-GB-SoniaNeural") == tts.audio_path("x", tts.voice_of("en-GB-SoniaNeural"))
    assert drills.VOICES == tts.VOICES["kokoro"] and tts.MEDIA[a.suffix] == "audio/wav"


@pytest.fixture
def practice(tmp_path, monkeypatch):
    """Every path the drills write, and the test history, under tmp_path; edge-tts replaced by a stub."""
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS", "ATTEMPTS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "my-words.csv")
    monkeypatch.setattr(drills, "SENTENCES", tmp_path / "sentences.csv")
    monkeypatch.setattr(drills, "RECORDINGS", tmp_path / "recordings")
    monkeypatch.setattr(drills, "DRAFTS", tmp_path / "drafts")
    monkeypatch.setattr(tts, "AUDIO", tmp_path / "audio")
    monkeypatch.setattr(tts, "synthesise", lambda text, voice, path: path.write_bytes(b"ID3" + text.encode()))
    return tmp_path


def attempts(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def test_cloze_and_dictation_api(practice):
    c = TestClient(main.app)
    d = c.get("/api/drill/read-and-complete/next?mode=sentence").json()             # passage mode is the default since #17
    assert d["task"] == "read-and-complete" and d["mode"] == "sentence" and d["seconds"] == 60 and d["subband"] == "1k-a"
    assert 2 <= d["blanks"] <= 5 and sum(1 for p in d["pieces"] if isinstance(p, dict)) == d["blanks"]
    family, sense, seed = drills.parse_cloze_id(d["id"], main.BANK.index)
    assert family in main.BANK.index and main.BANK.index[family]["subband"] == "1k-a"
    # answer: the first blank wrong, the rest right (the answers come back from the item id)
    text = main.EXAMPLES[(family, str(sense))]
    gen = drills.cloze(text, seed, drills.forms_pattern(family, drills.members(main.BANK.index[family])))
    typed = ["zzz"] + gen["answers"][1:]
    r = c.post(f"/api/drill/read-and-complete/{d['id']}", json={"attempt": d["attempt"], "typed": typed, "ms": 12345}).json()
    assert r["score"] == round((d["blanks"] - 1) / d["blanks"], 4) and r["correct"] == d["blanks"] - 1 and r["text"] == text
    assert r["wrong"] == [{"expected": gen["answers"][0], "typed": "zzz"}]
    misses = [e for e in drills.cloze_events(gen["answers"], typed, main.LEX) if e["kind"] != "hit"]   # a content-word blank (#17)
    wrong_family = misses[0]["family"] if misses else None
    rows = attempts(practice / "attempts.csv")
    assert len(rows) == 1 and rows[0]["task"] == "read-and-complete" and rows[0]["item"] == d["id"] and rows[0]["attempt"] == d["attempt"]
    assert rows[0]["subband"] == "1k-a" and rows[0]["seconds"] == "12" and rows[0]["timed_out"] == "0" and rows[0]["words"] == str(d["blanks"])
    assert rows[0]["errors"] == f"{gen['answers'][0]}>zzz" and rows[0]["file"] == "" and rows[0]["self"] == ""
    mw = learn.load_my_words()
    if wrong_family:
        assert [m["family"] for m in mw] == [wrong_family] and mw[0]["source"] == "read-and-complete" and mw[0]["note"] == text
        assert r["added"] == [{"word": gen["answers"][0], "family": wrong_family, "in_index": True, "kind": "vocabulary", "added": True}]
    else:
        assert mw == [] and r["added"] == []
    # the same sentence is not shown again this week
    key = f"{family}.{sense}"
    for _ in range(5):
        assert not c.get("/api/drill/read-and-complete/next?mode=sentence").json()["id"].startswith(key + ".")
    assert c.post(f"/api/drill/read-and-complete/{d['id']}", json={"attempt": "bad", "typed": []}).status_code == 422
    assert c.post("/api/drill/read-and-complete/nofamily.9.9", json={"attempt": d["attempt"], "typed": []}).status_code == 404
    assert c.get("/api/drill/nope/next").status_code == 404

    # passage mode from the committed passages: first sentence intact, 3 minutes, no sub-band
    p = c.get("/api/drill/read-and-complete/next?mode=passage").json()
    assert p["mode"] == "passage" and p["seconds"] == 180 and p["subband"] == "" and p["source"].startswith("https://")
    assert p["blanks"] == 5 and isinstance(p["pieces"][0], str) and "." in p["pieces"][0]
    r = c.post(f"/api/drill/read-and-complete/{p['id']}", json={"attempt": p["attempt"], "typed": [], "ms": 180000, "timed_out": True}).json()
    assert r["score"] == 0 and len(r["wrong"]) == 5 and r["timed_out"]
    assert attempts(practice / "attempts.csv")[-1]["timed_out"] == "1"

    # dictation: the bank is built on first use, the MP3 is cached, a long wrong answer scores 0
    d = c.get("/api/drill/listen-and-type/next").json()
    assert d["plays_left"] == 3 and d["seconds"] == 60 and d["audio_url"] == f"/api/drill/listen-and-type/{d['id']}/audio"
    mine = {m["family"] for m in learn.open_my_words(learn.load_my_words())}          # the cloze misses above are priority words (#13)
    assert d["subband"] == "1k-a" or d["family"] in mine
    assert (practice / "sentences.csv").exists() and len(drills.load_sentences()) > 1000
    a = c.get(d["audio_url"])
    assert a.status_code == 200 and a.headers["content-type"] == "audio/wav" and a.content.startswith(b"ID3")
    assert len(list((practice / "audio").glob("*.wav"))) == 1
    row = next(r for r in drills.load_sentences() if r["id"] == d["id"])
    ok = c.post(f"/api/drill/listen-and-type/{d['id']}", json={"attempt": d["attempt"], "typed": row["sentence"], "ms": 20000, "plays": 2}).json()
    assert ok["score"] == 1.0 and ok["errors"] == [] and ok["added"] == [] and ok["reference"] == row["sentence"]
    d2 = c.get("/api/drill/listen-and-type/next").json()
    assert d2["id"] != d["id"]
    long = " ".join(["zzz"] * 40)
    bad = c.post(f"/api/drill/listen-and-type/{d2['id']}", json={"attempt": d2["attempt"], "typed": long, "ms": 60000, "plays": 3, "timed_out": True}).json()
    assert bad["score"] < irt.WRONG and bad["word_score"] == 0.0 and bad["words"] == len(drills.normalise(bad["reference"])) and bad["timed_out"]
    rows = attempts(practice / "attempts.csv")
    assert [r["task"] for r in rows] == ["read-and-complete"] * 2 + ["listen-and-type"] * 2
    assert rows[-1]["score"] == str(bad["score"]) and rows[-1]["timed_out"] == "1" and rows[-2]["score"] == "1.0" and rows[-2]["errors"] == ""
    # every content word of the reference is an event; missing ones go to my-words as vocabulary or hearing misses
    families = {e["family"] for e in bad["events"]}
    assert families and families == {f for f, _ in drills.parse_events(rows[-1]["events"])}
    assert all(e["kind"] in ("vocabulary", "hearing") for e in bad["events"])
    assert rows[-1]["theta"] == "" and float(rows[-1]["b"]) == bad["b"] and bad["theta"] is None   # no test yet: θ does not move
    open_ = {m["family"] for m in learn.open_my_words(learn.load_my_words())}
    assert families <= open_ and all(m["source"].split(":")[0] in ("read-and-complete", "listen-and-type") for m in learn.load_my_words())
    # the study list shows them as my-words (or as a repeat when a cloze blank above missed the same word, #17)
    words = c.get("/api/learn?n=100").json()["words"]
    assert {w["family"] for w in words if w["reason"] in ("my-words", "repeat")} >= families
    assert c.get("/api/config").json()["today"]["listen-and-type"] == 2


def test_speaking_and_writing_api(practice):
    c = TestClient(main.app)
    # Read Aloud: a bank sentence, 20 s, the recording uploaded under the attempt id, then the rating
    d = c.get("/api/drill/read-aloud/next").json()
    assert d["seconds"] == 20 and d["sentence"] and d["subband"] == "1k-a" and d["id"] in {r["id"] for r in drills.load_sentences()}
    up = c.post(f"/api/drill/read-aloud/{d['id']}/audio?attempt={d['attempt']}", content=b"\x1aE\xdf\xa3webm", headers={"content-type": "audio/webm"}).json()
    assert up == {"file": f"speaking/recordings/{d['attempt']}.webm", "bytes": 8}
    assert c.get(f"/api/drill/read-aloud/{d['attempt']}/recording").content == b"\x1aE\xdf\xa3webm"
    assert c.post(f"/api/drill/read-aloud/{d['id']}/audio?attempt=../../etc", content=b"x").status_code == 422
    r = c.post(f"/api/drill/read-aloud/{d['id']}", json={"attempt": d["attempt"], "ms": 19400, "rating": [4, 3, 3, 4], "lacked": "Serendipity, uttered, utter"}).json()
    assert r["self"] == 4 and r["file"] == up["file"] and r["recording_url"] == f"/api/drill/read-aloud/{d['attempt']}/recording"
    assert [(a["family"], a["in_index"], a["added"]) for a in r["added"]] == [("serendipity", False, True), ("utter", True, True)]
    mw = learn.load_my_words()
    assert [(m["family"], m["source"], m["note"]) for m in mw] == [("serendipity", "read-aloud", d["id"]), ("utter", "read-aloud", d["id"])]
    rows = attempts(practice / "attempts.csv")
    assert rows[-1]["self"] == "4" and rows[-1]["score"] == "" and rows[-1]["words"] == "" and rows[-1]["seconds"] == "19" and rows[-1]["subband"] == "1k-a"
    assert c.post(f"/api/drill/read-aloud/{d['id']}", json={"attempt": d["attempt"], "rating": [6, 1, 1, 1]}).status_code == 422
    # the extra word shows as `extra` on the study list
    words = c.get("/api/learn?n=5").json()["words"]
    assert [(w["family"], w["subband"], w["reason"]) for w in words[:2]] == [("utter", "4k-a", "my-words"), ("serendipity", "extra", "my-words")]

    # Read Then Speak and Listen Then Speak: prompts.csv, 20 s prep, 90 s answer, 30 s minimum; the prompt is read by TTS
    d = c.get("/api/drill/read-then-speak/next").json()
    assert (d["prep"], d["seconds"], d["min"]) == (20, 90, 30) and d["id"].startswith("rts-") and d["prompt"] and d["audio_url"] is None
    r = c.post(f"/api/drill/read-then-speak/{d['id']}", json={"attempt": d["attempt"], "ms": 45000, "rating": [2, 2, 3, 2]}).json()
    assert r["self"] == 2 and r["file"] == "" and r["recording_url"] is None and r["added"] == []
    assert attempts(practice / "attempts.csv")[-1]["file"] == ""
    d = c.get("/api/drill/listen-then-speak/next").json()
    assert d["id"].startswith("lts-") and d["plays"] == 3 and d["audio_url"] == f"/api/drill/listen-then-speak/{d['id']}/audio"
    a = c.get(d["audio_url"])
    assert a.status_code == 200 and a.content == b"ID3" + d["prompt"].encode()
    assert c.get("/api/drill/read-then-speak/rts-01/audio").status_code == 404
    assert c.get("/api/drill/speak-photo/next").status_code == 404                           # no photos on a fresh clone
    assert c.get("/api/drill/read-then-speak/rts-01/photo").status_code == 404

    # writing: autosave, then the final row; Interactive Writing keeps both parts in one draft
    d = c.get("/api/drill/interactive-writing/next").json()
    assert (d["seconds"], d["seconds2"], d["min"]) == (300, 180, 50) and d["follow_up"] and d["id"].startswith("iw-")
    s = c.post(f"/api/drill/interactive-writing/{d['id']}/draft", json={"attempt": d["attempt"], "text": "one two three", "seconds": 10}).json()
    assert s == {"file": f"writing/drafts/{d['attempt']}.md", "words": 3}
    draft = practice / "drafts" / f"{d['attempt']}.md"
    assert draft.read_text().startswith(f"---\ntask: interactive-writing\nprompt: {d['id']}\nseconds: 10\nwords: 3\n---\n")
    part1, part2 = " ".join(["alpha"] * 60), " ".join(["beta"] * 30)
    r = c.post(f"/api/drill/interactive-writing/{d['id']}", json={"attempt": d["attempt"], "ms": 480000, "text": part1, "text2": part2,
                                                                  "rating": [3, 3, 3, 4], "lacked": ""}).json()
    assert r["self"] == 3 and r["words"] == 90 and r["file"] == s["file"] and r["min"] == 50
    md = draft.read_text()
    assert "seconds: 480\nwords: 90\n" in md and f"## {d['prompt']}\n\n{part1}\n" in md and f"## {d['follow_up']}\n\n{part2}\n" in md
    row = attempts(practice / "attempts.csv")[-1]
    assert (row["task"], row["item"], row["seconds"], row["words"], row["self"], row["file"]) == ("interactive-writing", d["id"], "480", "90", "3", s["file"])
    d = c.get("/api/drill/read-then-write/next").json()
    assert (d["seconds"], d["seconds2"], d["min"]) == (300, 0, 50) and d["follow_up"] == ""
    assert c.get("/api/drill/write-photo/next").status_code == 404
    assert c.post("/api/drill/read-aloud/x/draft", json={"attempt": d["attempt"]}).status_code == 404
    today = c.get("/api/config").json()["today"]
    assert today["read-aloud"] == 1 and today["read-then-speak"] == 1 and today["interactive-writing"] == 1
    # exactly one row per finished attempt, every task through the same header
    rows = attempts(practice / "attempts.csv")
    assert len(rows) == 3 and len({r["attempt"] for r in rows}) == 3 and all(list(r) == store.ATTEMPTS_HEADER for r in rows)
    with (practice / "my-words.csv").open(newline="") as f:
        assert next(csv.reader(f)) == learn.MY_WORDS_HEADER
