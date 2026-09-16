"""Read and Complete and Fill in the Blanks on the DET scale (issue #17): the blank kinds and events, the passage
targets and the seeded window, the fill item, and the API — passage mode as the default, selection in the θ
window with the priority family damaged, credit = correct / blanks, θ before → after, the events and the refit."""
import json
import random
import shutil
from datetime import date

from fastapi.testclient import TestClient

from app import drills, irt, learn, main, store, textdiff
from app.bank import WORD
from tests.test_adaptive import learner_at
from tests.test_drills import attempts, practice  # noqa: F401  (the fixture)
from tests.test_learn import session

LEX = main.LEX
COMMITTED = store.VOCAB.parent / "practice" / "read-and-complete" / "passages"       # the bank in git: never written by a test
PASSAGE = ("Honey is a food made by honeybees from nectar. They put the honey into a honeycomb, which for them is a "
           "storage unit. Honey is sweet and can be used instead of sugar. It is a supersaturated liquid. As the "
           "temperature drops, glucose comes out of solution. Then it is a semi-solid rather than a liquid.")


# ---- pure functions --------------------------------------------------------------------------------------------

def test_blank_kind_and_cloze_events():
    assert drills.blank_kind("sentence", "sentence") == drills.blank_kind("sentence", "SENTENCE!") == "hit"
    assert drills.blank_kind("sentence", "sentance") == "spelling"                       # one edit
    assert drills.blank_kind("sentence", "sentanse") == "vocabulary"                      # two edits
    assert drills.blank_kind("the", "") == "vocabulary" and drills.blank_kind("skip", "skipp") == "spelling"
    ev = drills.cloze_events(["storage", "the", "sweet", "sugar", "and"], ["storage", "teh", "sweat", "", "and"], LEX)
    assert [(e["family"], e["kind"], e["word"], e["typed"]) for e in ev] == \
        [("store", "hit", "storage", "storage"), ("sweet", "spelling", "sweet", "sweat"), ("sugar", "vocabulary", "sugar", "")]
    assert drills.cloze_events(["sugar"], [], LEX) == [{"family": "sugar", "kind": "vocabulary", "word": "sugar", "typed": ""}]
    assert drills.events_column(ev) == "store:hit|sweet:spelling|sugar:vocabulary"


def test_passage_targets_and_seeded_window():
    # the damaged parity after the first sentence: put, honey, honeycomb, for, storage, Honey, and, used, sugar, …
    targets = drills.passage_targets(PASSAGE, {"honey", "sugar", "sweet", "storage", "store"}, LEX)
    assert targets == [("honey", "honey"), ("store", "storage"), ("sugar", "sugar")]          # sweet is on the other parity
    pat = drills.forms_pattern("sugar", ["sugars"])
    rng = random.Random(3)
    seed = drills.passage_seed(PASSAGE, pat, rng)
    item = drills.cloze(PASSAGE, seed, passage=True)
    assert seed is not None and "sugar" in item["answers"] and item["blanks"] == 5
    assert drills.cloze(PASSAGE, seed, passage=True) == item                                   # the id replays the item
    assert drills.passage_seed(PASSAGE, drills.forms_pattern("sweet", []), random.Random(1)) is None
    assert drills.passage_seed("One sentence only.", pat, random.Random(1)) is None
    assert drills.sentence_of(PASSAGE, "sugar") == "Honey is sweet and can be used instead of sugar."
    assert drills.sentence_of(PASSAGE, "nectar") == "Honey is a food made by honeybees from nectar."
    assert drills.sentence_of(PASSAGE, "zzz") == PASSAGE


def test_fill_blank_item_and_ids():
    pat = drills.forms_pattern("tenant", ["tenants"])
    it = drills.fill_blank("The landlord can evict a tenant who does not pay.", pat)
    assert it == {"pieces": ["The landlord can evict a ", {"keep": "te", "missing": 4}, " who does not pay."], "answer": "tenant",
                  "keep": 2, "damaged": "The landlord can evict a te____ who does not pay."}
    assert drills.fill_blank("Tenants pay rent.", pat)["pieces"][0] == {"keep": "Ten", "missing": 4}      # ceil(7 / 3) = 3
    assert drills.fill_blank("Nobody here.", pat) is None
    assert drills.fill_id("tenant", 1) == "tenant.1.fb" and drills.parse_fill_id("tenant.1.fb") == "tenant.1"
    for bad in ("tenant.1", "tenant.1.42", ".fb"):
        try:
            drills.parse_fill_id(bad)
            assert False, bad
        except ValueError:
            pass
    rows = [{"date": "2026-09-14", "task": "fill-in-the-blanks", "item": "tenant.1.fb"},
            {"date": "2026-09-14", "task": "read-and-complete", "item": "honey.42"}]
    assert drills.recent_items(rows, "fill-in-the-blanks", date(2026, 9, 16)) == {"tenant.1"}
    assert drills.recent_items(rows, "read-and-complete", date(2026, 10, 10), days=drills.PASSAGE_REPEAT_DAYS) == {"honey"}
    assert drills.recent_items(rows, "read-and-complete", date(2026, 10, 20), days=drills.PASSAGE_REPEAT_DAYS) == set()
    assert drills.TASKS["fill-in-the-blanks"] == {"skill": "reading", "prep": 0, "seconds": 20, "min": 0}
    assert "fill-in-the-blanks" in learn.DRILL_TASKS


def test_old_cloze_rows_load_and_do_not_move_theta(tmp_path, monkeypatch):
    path = tmp_path / "attempts.csv"
    path.write_text("date,attempt,task,item,subband,seconds,timed_out,score,self,words,errors,file\n"
                    "2026-09-10,2026-09-10_101010_abcd,read-and-complete,skip.1.42,4k-b,30,0,0.8,,5,skipped>skiped,\n", encoding="utf-8")
    monkeypatch.setattr(store, "ATTEMPTS", path)
    rows = store.load_attempts()
    assert rows[0]["theta"] == rows[0]["b"] == rows[0]["events"] == "" and rows[0]["score"] == "0.8"
    assert learn.drill_responses(rows) == [] and drills.recent_items(rows, "read-and-complete", date(2026, 9, 12)) == {"skip.1"}
    store.append_attempt({"date": "2026-09-12", "attempt": "2026-09-12_101010_abcd", "task": "read-and-complete", "item": "honey.7",
                          "score": 0.6, "theta": 6.0, "b": 6.5, "events": "sugar:hit|sweet:vocabulary"})
    rows = store.load_attempts()
    assert learn.drill_responses(rows) == [(6.5, 0.6)] and path.read_text().splitlines()[0] == ",".join(store.ATTEMPTS_HEADER)


# ---- the API -----------------------------------------------------------------------------------------------------

def with_test(tmp, theta=6):
    """A reliable session on disk (θ ≈ `theta`) and a private copy of the passage bank, so the refit never
    touches the committed files."""
    s = session("s", learner_at(theta), 4, "2026-09-10T10:00:00")
    store.SESSIONS.mkdir(parents=True, exist_ok=True)
    (store.SESSIONS / "s.json").write_text(json.dumps(s), encoding="utf-8")
    bank = tmp / "passages"
    shutil.copytree(COMMITTED, bank)
    return bank


def test_passage_mode_on_the_scale(practice, monkeypatch):
    bank = with_test(practice)
    monkeypatch.setattr(drills, "PASSAGES", bank)
    c = TestClient(main.app)
    cfg = c.get("/api/config").json()
    theta0 = cfg["theta"]
    # passage mode is the default: a scored passage inside the window, 3 minutes, θ on the item
    d = c.get("/api/drill/read-and-complete/next").json()
    assert d["mode"] == "passage" and d["seconds"] == 180 and d["theta"] == theta0 and d["window"] >= drills.DRILL_WINDOW
    assert abs(d["b"] - theta0) <= d["window"] and d["blanks"] == 5 and d["source"].startswith("https://simple.wikipedia.org/wiki/")
    slug, sense, seed = drills.parse_cloze_id(d["id"], main.BANK.index)
    p = next(p for p in drills.load_passages() if p["slug"] == slug)
    assert sense is None and d["b"] == textdiff.b_of(p)
    gen = drills.cloze(p["text"], seed, passage=True)
    # three right, one spelling slip, one blank → credit 3 / 5, per-blank events, θ down from a fractional credit
    typed = gen["answers"][:3] + [gen["answers"][3][:-1] + ("x" if gen["answers"][3][-1] != "x" else "y"), ""]
    r = c.post(f"/api/drill/read-and-complete/{d['id']}", json={"attempt": d["attempt"], "typed": typed, "ms": 90000}).json()
    assert r["score"] == r["credit"] == 0.6 and r["correct"] == 3 and r["blanks"] == 5 and r["text"] == p["text"]
    assert r["theta"] == theta0 and r["b"] == d["b"] and r["theta_after"] is not None and r["theta_after"] != theta0
    kinds = {e["word"]: e["kind"] for e in r["events"]}
    content = [w for w in gen["answers"] if LEX.family(w) and LEX.family(w) not in LEX.function and WORD.match(LEX.family(w))]
    assert set(kinds) <= set(gen["answers"]) and all(kinds[w] == "hit" for w in gen["answers"][:3] if w in kinds)
    if gen["answers"][3] in kinds:
        assert kinds[gen["answers"][3]] == "spelling"
    if gen["answers"][4] in kinds:
        assert kinds[gen["answers"][4]] == "vocabulary"
    assert len(r["events"]) == len(content)
    rows = attempts(practice / "attempts.csv")
    assert rows[-1]["item"] == d["id"] and rows[-1]["score"] == "0.6" and float(rows[-1]["theta"]) == theta0 and float(rows[-1]["b"]) == d["b"]
    assert rows[-1]["events"] == drills.events_column(r["events"]) and rows[-1]["words"] == "5"
    # my-words: the spelling slip under read-and-complete:spelling, the blank under read-and-complete, the note is the blank's sentence
    for a in r["added"]:
        row = next(m for m in learn.load_my_words() if m["family"] == a["family"])
        assert row["source"] == ("read-and-complete" if a["kind"] == "vocabulary" else f"read-and-complete:{a['kind']}")
        assert row["note"] == drills.sentence_of(p["text"], a["word"]) and a["word"] in row["note"]
    # θ now includes the cloze row; a perfect passage moves it up
    cfg2 = c.get("/api/config").json()
    assert cfg2["theta"] == r["theta_after"]
    d2 = c.get("/api/drill/read-and-complete/next").json()
    assert d2["id"].rpartition(".")[0] != slug                                                # not the same passage within 30 days
    slug2, _, seed2 = drills.parse_cloze_id(d2["id"], main.BANK.index)
    p2 = next(p for p in drills.load_passages() if p["slug"] == slug2)
    r2 = c.post(f"/api/drill/read-and-complete/{d2['id']}", json={"attempt": d2["attempt"], "typed": drills.cloze(p2["text"], seed2, passage=True)["answers"], "ms": 1000}).json()
    assert r2["credit"] == 1.0 and r2["theta"] == cfg2["theta"] and r2["theta_after"] > r2["theta"] and all(e["kind"] == "hit" for e in r2["events"])
    # the refit: six attempts at one passage from a lower θ → a negative b_adjust written into its front matter
    store.ATTEMPTS.unlink()
    for i in range(6):
        store.append_attempt({"date": f"2026-09-1{i}", "attempt": f"2026-09-1{i}_101010_abcd", "task": "read-and-complete",
                              "item": drills.passage_id(slug, i), "score": 1.0, "theta": theta0 - 2, "b": d["b"]})
    d3 = c.get("/api/drill/read-and-complete/next").json()
    c.post(f"/api/drill/read-and-complete/{d3['id']}", json={"attempt": d3["attempt"], "typed": [], "ms": 1000})
    meta, body = textdiff.passage_front_matter(bank / f"{slug}.md")
    assert float(meta["b_adjust"]) < 0 and float(meta["b_text"]) == p["b_text"] and body.strip() == p["text"]
    assert next(q for q in drills.load_passages() if q["slug"] == slug)["b_adjust"] < 0
    assert (drills.PASSAGES / f"{slug}.md").read_text().count("---\n") == 2
    # the committed bank was not touched
    assert not any("b_adjust: -" in f.read_text() for f in COMMITTED.glob("*.md"))


def test_priority_family_is_damaged_in_the_passage(practice, monkeypatch):
    bank = with_test(practice)
    monkeypatch.setattr(drills, "PASSAGES", bank)
    monkeypatch.setattr(drills, "PRIORITY_SHARE", 1.0)
    c = TestClient(main.app)
    theta = c.get("/api/config").json()["theta"]
    window, _ = drills.in_window([p for p in drills.load_passages()], theta, textdiff.b_of)
    # a family of the bank that stands at the damaged parity of a passage in the window → my-words → the target
    candidates = [(p, f, w) for p in window for f, w in drills.passage_targets(p["text"], main.BANK.index, LEX) if main.BANK.index[f]["subband"] != "1k-a"]
    p, family, word = candidates[len(candidates) // 2]
    learn.add_my_word(family, "test", "", date(2026, 9, 14))
    for _ in range(3):
        d = c.get("/api/drill/read-and-complete/next").json()
        assert d["family"] == family and d["reason"] == "my-words" and d["reason_label"] == "my-words · test 2026-09-14"
        slug, _, seed = drills.parse_cloze_id(d["id"], main.BANK.index)
        text = next(q for q in drills.load_passages() if q["slug"] == slug)["text"]
        gen = drills.cloze(text, seed, passage=True)
        pat = drills.forms_pattern(family, drills.members(main.BANK.index[family]))
        assert any(pat.match(a) for a in gen["answers"]) and abs(d["b"] - theta) <= d["window"]
    # the answer marks the family and its blank is that family's event
    r = c.post(f"/api/drill/read-and-complete/{d['id']}", json={"attempt": d["attempt"], "typed": gen["answers"], "ms": 1000}).json()
    assert r["family"] == "" or r["family"] == family                                          # passage results carry no target chip
    assert (family, "hit") in drills.parse_events(attempts(practice / "attempts.csv")[-1]["events"])


def test_fill_in_the_blanks_api(practice, monkeypatch):
    with_test(practice)
    c = TestClient(main.app)
    theta0 = c.get("/api/config").json()["theta"]
    d = c.get("/api/drill/fill-in-the-blanks/next").json()
    assert d["task"] == "fill-in-the-blanks" and d["seconds"] == 20 and d["id"].endswith(".fb") and d["theta"] == theta0
    assert abs(d["b"] - theta0) <= d["window"] and d["forms"][0] == d["family"]
    blank = next(p for p in d["pieces"] if isinstance(p, dict))
    row = next(r for r in drills.load_sentences() if r["id"] == drills.parse_fill_id(d["id"]))
    gen = drills.fill_blank(row["sentence"], drills.forms_pattern(row["family"], drills.members(main.BANK.index[row["family"]])))
    assert blank == {"keep": gen["answer"][:d["keep"]], "missing": len(gen["answer"]) - d["keep"]} and d["keep"] == -(-len(gen["answer"]) // 3)
    assert d["words"] == drills.word_count(row["sentence"]) and d["b"] == textdiff.b_of(row)
    # right, in another case: credit 1, a hit, θ up, one row with theta / b / events
    r = c.post(f"/api/drill/fill-in-the-blanks/{d['id']}", json={"attempt": d["attempt"], "typed": gen["answer"].upper(), "ms": 5000}).json()
    assert r["credit"] == r["score"] == 1.0 and r["kind"] == "hit" and r["answer"] == gen["answer"] and r["sentence"] == row["sentence"]
    assert r["theta"] == theta0 and r["theta_after"] > theta0 and r["events"] == [{"family": row["family"], "kind": "hit", "word": gen["answer"], "typed": gen["answer"].upper()}]
    rows = attempts(practice / "attempts.csv")
    assert rows[-1]["task"] == "fill-in-the-blanks" and rows[-1]["item"] == d["id"] and rows[-1]["score"] == "1.0" and rows[-1]["words"] == "1"
    assert float(rows[-1]["theta"]) == theta0 and float(rows[-1]["b"]) == d["b"] and rows[-1]["events"] == f"{row['family']}:hit" and rows[-1]["subband"] == row["subband"]
    assert r["added"] == [] and c.get("/api/config").json()["theta"] == r["theta_after"]
    # one letter off: a spelling slip, credit 0, θ down, my-words under fill-in-the-blanks:spelling
    d2 = c.get("/api/drill/fill-in-the-blanks/next").json()
    assert d2["id"] != d["id"]
    row2 = next(x for x in drills.load_sentences() if x["id"] == drills.parse_fill_id(d2["id"]))
    ans = drills.fill_blank(row2["sentence"], drills.forms_pattern(row2["family"], drills.members(main.BANK.index[row2["family"]])))["answer"]
    slip = ans[:-1] + ("x" if ans[-1] != "x" else "y")
    r2 = c.post(f"/api/drill/fill-in-the-blanks/{d2['id']}", json={"attempt": d2["attempt"], "typed": slip, "ms": 20000, "timed_out": True}).json()
    assert r2["credit"] == 0.0 and r2["kind"] == "spelling" and r2["theta_after"] < r2["theta"] == r["theta_after"] and r2["timed_out"]
    assert r2["added"] == [{"word": ans, "family": row2["family"], "in_index": True, "kind": "spelling", "added": True}]
    assert next(m for m in learn.load_my_words() if m["family"] == row2["family"])["source"] == "fill-in-the-blanks:spelling"
    assert attempts(practice / "attempts.csv")[-1]["errors"] == f"{ans}>{slip}"
    # nothing typed: a vocabulary miss; a bad id is refused; the frontier before a test keeps θ blank
    d3 = c.get("/api/drill/fill-in-the-blanks/next").json()
    r3 = c.post(f"/api/drill/fill-in-the-blanks/{d3['id']}", json={"attempt": d3["attempt"], "typed": "", "ms": 20000}).json()
    assert r3["kind"] == "vocabulary" and r3["credit"] == 0.0
    assert c.post("/api/drill/fill-in-the-blanks/tenant.1", json={"attempt": d3["attempt"], "typed": ""}).status_code == 422
    assert c.post("/api/drill/fill-in-the-blanks/nosuch.9.fb", json={"attempt": d3["attempt"], "typed": ""}).status_code == 404
    p = c.get("/api/progress").json()
    assert p["theta"] == r3["theta_after"]
    assert c.get("/api/config").json()["today"]["fill-in-the-blanks"] == 3


def test_drills_before_a_test_draw_from_the_frontier(practice, monkeypatch):
    shutil.copytree(COMMITTED, practice / "passages")
    monkeypatch.setattr(drills, "PASSAGES", practice / "passages")
    c = TestClient(main.app)
    d = c.get("/api/drill/read-and-complete/next").json()
    assert d["mode"] == "passage" and d["theta"] is None and d["b"] <= 5                       # the easiest ten passages: the frontier is 1k-a
    r = c.post(f"/api/drill/read-and-complete/{d['id']}", json={"attempt": d["attempt"], "typed": [], "ms": 1000}).json()
    assert r["theta"] is None and r["theta_after"] is None and r["credit"] == 0.0
    rows = attempts(practice / "attempts.csv")
    assert rows[-1]["theta"] == "" and float(rows[-1]["b"]) == d["b"] and rows[-1]["events"]
    f = c.get("/api/drill/fill-in-the-blanks/next").json()
    assert f["theta"] is None and (f["subband"] == "1k-a" or f["reason"] == "my-words") and f["window"] is None   # the blanks above joined the pool
    assert irt.snap(0.6) == 0.6 and irt.snap(1.0) == 1.0
