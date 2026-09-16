"""Text difficulty (issue #15): b_text from the words, length and coverage of a sentence or passage; names, numbers
and contractions; the ordering on the committed bank; the worked example in the docs; the Rasch item refit; the
sentences.csv and cloze.csv caches with the new columns; the passage front matter and scripts/passages.py."""
import csv
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import drills, learn, main, store, textdiff, tts
from app.bank import load_subbands

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "sentences.md"
LEX = main.LEX
BANK = main.BANK
ORDER = [sb.name for sb in load_subbands()]
EXAMPLE = "the landlord can evict a tenant who doesn't pay the rent"
EASY = "we can go home and see the man"                                  # every content word in 1k-a


def bands(text):
    return {t["family"]: BANK.index[t["family"]]["subband"] for t in textdiff.tokens(text, LEX) if "family" in t}


def test_easy_sentence_and_one_hard_word():
    assert set(bands(EASY).values()) == {"1k-a"}
    easy = textdiff.b_text(EASY, LEX)
    assert easy < 1.5
    hard = textdiff.b_text(EASY + " evict", LEX)
    assert BANK.index["evict"]["subband"] == "6k-b" and hard > 11 and hard > easy + 10
    f = textdiff.features(EASY + " evict", LEX)
    assert f["top"] == "evict" and f["load"] == 1 and f["b90"] == BANK.b["evict"]


def test_names_numbers_and_contractions_are_not_scored_nor_off_list():
    text = "Mary saw 3 dogs in Paris and didn't blorp"
    kinds = {t["word"]: t["kind"] for t in textdiff.tokens(text, LEX)}
    assert kinds["Mary"] == "name" and kinds["Paris"] == "name" and kinds["3"] == "number" and kinds["blorp"] == "off"
    assert kinds["didn't"] == "content" and next(t for t in textdiff.tokens(text, LEX) if t["word"] == "didn't")["family"] == "do"
    f = textdiff.features(text, LEX)
    assert f["off"] == 1 and f["n"] == 5 and f["length"] == 9                         # saw, dogs, in, and, didn't
    without = textdiff.b_text("Mary saw 3 dogs in Paris and didn't", LEX)
    assert textdiff.b_text(text, LEX) == pytest.approx(without + textdiff.W_OFF + textdiff.W_LENGTH)   # 9th token, +0.6 off-list
    # a name, a number or a contraction alone never counts as off-list; a sentence-initial unknown capital is a name
    assert textdiff.features("Mary 3 can't", LEX)["off"] == 0 and textdiff.features("Xyzzy is here", LEX)["off"] == 0
    assert textdiff.features("the landlord's dog", LEX)["n"] == 3                     # possessive: landlord scored, s dropped
    # function words are scored but are not content: they never set b90 or the load
    f = textdiff.features("the of and a to", LEX)
    assert f["n"] == 5 and f["content"] == 0 and f["b90"] == 0 and f["load"] == 0


def test_percentile_is_nearest_rank_max_up_to_nine():
    assert textdiff.percentile([]) == 0.0 and textdiff.percentile([3.0]) == 3.0
    assert textdiff.percentile(list(range(1, 10))) == 9 and textdiff.percentile(list(range(1, 11))) == 9
    assert textdiff.percentile([5.0, 1.0, 9.0]) == 9.0


def test_ordering_on_the_committed_bank():
    """The median b_text rises with the source band, 1k-a < 2k-a < … < 6k-b."""
    rows = textdiff.score_rows(drills.build_sentences(main.SENSES, BANK.index, random.Random(1)), LEX)
    hist = textdiff.histogram(rows, ORDER)
    medians = [h["median"] for h in hist]
    assert [h["band"] for h in hist] == ORDER and all(h["n"] > 100 for h in hist)
    assert all(y > x for x, y in zip(medians, medians[1:])), medians
    assert medians[0] < 3 and medians[-1] > 11
    table = textdiff.histogram_table(hist)
    assert table.splitlines()[0].startswith("| Band | Sentences |") and len(table.splitlines()) == 2 + len(ORDER)
    # every row carries the three new columns, parseable back
    assert all(set(r) >= {"b_text", "b_adjust", "features"} for r in rows)
    f = textdiff.parse_features(rows[0]["features"])
    assert set(f) == set(textdiff.FEATURE_KEYS) and textdiff.combine({**f, "zipf": float(f["zipf"])}) == rows[0]["b_text"]


def test_worked_example_matches_the_docs():
    f = textdiff.features(EXAMPLE, LEX)
    assert f == {"b90": 11.662, "load": 1, "length": 11, "off": 0, "zipf": 4.467, "top": "evict", "content": 6, "n": 11}
    b = textdiff.b_text(EXAMPLE, LEX)
    assert b == textdiff.combine(f) == pytest.approx(11.662 + 0.08 * 3 + 0.4 * (4.5 - 4.467), abs=1e-4) == 11.9152
    doc = DOCS.read_text(encoding="utf-8")
    assert "## How difficulty is computed" in doc and EXAMPLE in doc
    for needle in ("b90 = 11.662", "load = 1", "length = 11", "zipf = 4.467", "b_text = 11.92"):
        assert needle in doc, needle
    # the docs table is the report's table
    out = subprocess.run([sys.executable, "scripts/report.py", "--sentences"], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[2] in doc                                   # the 1k-a row


def test_rasch_item_refit():
    assert textdiff.refit(8.0, []) == 0.0 and textdiff.refit(8.0, [(5.0, 1.0)] * 4) == 0.0   # n < 5: b = b_text
    down = textdiff.refit(8.0, [(5.0, 1.0)] * 10)
    up = textdiff.refit(8.0, [(5.0, 0.0)] * 10)
    assert down < 0 and up > 0 and abs(down) <= textdiff.MAX_SHIFT * 10 / 15
    assert textdiff.refit(6.0, [(5.0, 1.0)] * 5 + [(5.0, 0.0)] * 5) == pytest.approx(-2 / 3, abs=1e-3)   # MLE at θ, shrunk 2/3
    assert textdiff.refit(6.0, [(6.0, 0.5)] * 10) == 0.0                                # credit 0.5 at b = θ: nothing to learn
    assert abs(textdiff.refit(6.0, [(6.0, 1.0)] * 100)) < abs(textdiff.refit(6.0, [(6.0, 1.0)] * 5)) * 30   # bounded
    assert textdiff.refit(6.0, [(6.0, 1.0)] * 100) < textdiff.refit(6.0, [(6.0, 1.0)] * 5)   # more evidence, less shrinkage


def test_refit_bank_reads_attempts_and_skips_rows_without_theta():
    attempts = [{"task": "listen-and-type", "item": "evict.1", "score": "1.0", "theta": "5.0"}] * 6 \
        + [{"task": "listen-and-type", "item": "evict.1", "score": "1.0", "theta": ""}] * 3 \
        + [{"task": "listen-and-type", "item": "evict.1", "score": "1.0"}] \
        + [{"task": "read-and-complete", "item": "skip.1.42", "score": "0.0", "theta": "7.0"}] * 5 \
        + [{"task": "read-and-complete", "item": "honey.7", "score": "0.2", "theta": "7.0"}] * 5
    per = textdiff.item_attempts(attempts, "listen-and-type")
    assert per == {"evict.1": [(5.0, 1.0)] * 6}
    assert textdiff.item_attempts(attempts, "read-and-complete", textdiff.strip_seed) == {"skip.1": [(7.0, 0.0)] * 5, "honey": [(7.0, 0.2)] * 5}
    rows = [{"id": "evict.1", "b_text": 11.9, "b_adjust": 0}, {"id": "rent.1", "b_text": 2.0, "b_adjust": 0.5}]
    assert textdiff.refit_bank(rows, attempts, "listen-and-type") == 2
    assert rows[0]["b_adjust"] < 0 and rows[1]["b_adjust"] == 0.0 and textdiff.b_of(rows[0]) == round(11.9 + rows[0]["b_adjust"], 4)
    assert textdiff.refit_bank(rows, attempts, "listen-and-type") == 0                  # idempotent
    cloze = [{"key": "skip.1", "b_text": 6.0, "b_adjust": 0}]
    assert textdiff.refit_bank(cloze, attempts, "read-and-complete", textdiff.strip_seed, "key") == 1 and cloze[0]["b_adjust"] > 0


def test_sentences_csv_rebuilt_with_the_new_columns(tmp_path, monkeypatch):
    path = tmp_path / "sentences.csv"
    monkeypatch.setattr(drills, "SENTENCES", path)
    # an old file (header from before #15) is not crashed on: it reads as empty and is rebuilt
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "family", "subband", "sentence", "voice"])
        w.writerow(["skip.1", "skip", "4k-b", "He skipped a row", "en-US-AriaNeural"])
    assert drills.load_sentences() == []
    rows = textdiff.sentence_bank(main.SENSES, BANK.index, LEX, rng=random.Random(1))
    assert len(rows) > 1000 and path.read_text().splitlines()[0] == ",".join(drills.SENTENCES_HEADER)
    again = drills.load_sentences()
    assert [r["id"] for r in again] == [r["id"] for r in rows] and again[0]["b_adjust"] == "0"
    skip = next(r for r in again if r["id"] == "skip.1")
    assert float(skip["b_text"]) > 7 and textdiff.parse_features(skip["features"])["top"] == "incomprehensible"
    assert main.sentence_bank() == again                                                 # the app reads the same file
    # a row without the scores still writes (blank cells), and write_sentences ignores extra keys
    drills.write_sentences([{"id": "x.1", "family": "x", "subband": "1k-a", "sentence": "s", "voice": "v", "pattern": "p"}], path)
    assert drills.load_sentences(path)[0]["b_text"] == ""


def test_cloze_cache(tmp_path, monkeypatch):
    path = tmp_path / "cloze.csv"
    monkeypatch.setattr(textdiff, "CLOZE", path)
    path.write_text("key,family\nskip.1,skip\n")
    assert textdiff.load_cloze() == []                                                   # another header → rebuilt
    rows = [{"key": "skip.1", "family": "skip", "sense": "1", "subband": "4k-b", "example": "He skipped a row in the text", "pattern": "x"}]
    textdiff.write_cloze(textdiff.score_rows(rows, LEX, "example"))
    back = textdiff.load_cloze()
    assert path.read_text().splitlines()[0] == ",".join(textdiff.CLOZE_HEADER)
    assert back[0]["key"] == "skip.1" and float(back[0]["b_text"]) == rows[0]["b_text"] and "pattern" not in back[0]


def test_passages_carry_b_text_and_the_script_scores_and_checks(tmp_path):
    for p in drills.load_passages():
        assert p["b_text"] is not None and p["b_adjust"] == 0.0 and p["b_text"] == textdiff.b_text(p["text"], LEX)
        assert 3 <= p["b_text"] <= 12 and p["licence"].startswith("CC BY-SA")
    # an unscored copy: check fails, score writes b_text / b_adjust / features, check passes, score is idempotent
    folder = tmp_path / "passages"
    folder.mkdir()
    (folder / "honey.md").write_text((drills.PASSAGES / "honey.md").read_text(encoding="utf-8").split("\n---\n")[1], encoding="utf-8")
    src = drills.PASSAGES / "bicycle.md"
    (folder / "bicycle.md").write_text(re.sub(r"^(b_text|b_adjust|features):.*\n", "", src.read_text(encoding="utf-8"), flags=re.M), encoding="utf-8")
    assert drills.load_passages(folder)[0]["b_text"] is None
    run = lambda *args: subprocess.run([sys.executable, "scripts/passages.py", *args, "--folder", str(folder)],   # noqa: E731
                                       cwd=ROOT, capture_output=True, text=True)
    r = run("check")
    assert r.returncode == 1 and "bicycle.md: no b_text" in r.stderr and "honey.md: no source" in r.stderr and "no licence" in r.stderr
    assert run("score").returncode == 0
    meta, _ = textdiff.passage_front_matter(folder / "bicycle.md")
    assert list(meta) == ["source", "licence", "b_text", "b_adjust", "features"] and meta["b_adjust"] == "0"
    assert (folder / "bicycle.md").read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert not textdiff.score_passage(folder / "bicycle.md", LEX)[1]
    (folder / "honey.md").unlink()
    assert run("check").returncode == 0
    # a stale b_text or too few blanks fails
    (folder / "bicycle.md").write_text((folder / "bicycle.md").read_text(encoding="utf-8").replace("b_text: ", "b_text: 1"), encoding="utf-8")
    assert "stale" in run("check").stderr
    (folder / "short.md").write_text("---\nsource: x\nlicence: y\nb_text: 0\nb_adjust: 0\n---\nOne sentence. Two more words.\n", encoding="utf-8")
    assert "short.md: 1 blanks" in run("score").stdout + run("check").stderr


@pytest.fixture
def practice(tmp_path, monkeypatch):
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS", "ATTEMPTS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "my-words.csv")
    monkeypatch.setattr(drills, "SENTENCES", tmp_path / "sentences.csv")
    monkeypatch.setattr(textdiff, "CLOZE", tmp_path / "cloze.csv")
    monkeypatch.setattr(main, "_CLOZE_POOL", [])
    monkeypatch.setattr(tts, "AUDIO", tmp_path / "audio")
    monkeypatch.setattr(tts, "synthesise", lambda text, voice, path: path.write_bytes(b"ID3" + text.encode()))
    return tmp_path


def test_drill_items_carry_b(practice):
    c = TestClient(main.app)
    d = c.get("/api/drill/listen-and-type/next").json()
    row = next(r for r in drills.load_sentences() if r["id"] == d["id"])
    assert d["b"] == textdiff.b_of(row) == float(row["b_text"])
    d = c.get("/api/drill/read-and-complete/next?mode=sentence").json()
    assert d["b"] > 0 and (practice / "cloze.csv").exists() and len(textdiff.load_cloze()) > 1000
    key = d["id"].rpartition(".")[0]
    assert d["b"] == textdiff.b_of(next(r for r in main.cloze_pool() if r["key"] == key))
    p = c.get("/api/drill/read-and-complete/next?mode=passage").json()
    assert 3 <= p["b"] <= 12
