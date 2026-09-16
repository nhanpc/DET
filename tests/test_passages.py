"""The passage bank (issue #17): scripts/passages.py fetch — the topics file, the excerpt rule, the filters and the
files it writes (the MediaWiki call replaced by fixtures) — `check` on a bad passage, and the committed bank."""
import importlib.util
import io
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from app import drills, main, textdiff

ROOT = Path(__file__).resolve().parent.parent
LEX = main.LEX


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = load_script("passages")

EASY = ("Bread is a type of baked food. It is mainly made from dough, which is made mainly from flour and water. "
        "Usually, salt and yeast are added. Bread is often baked in an oven. It can be bought all over the world. "
        "Bread can be toasted or used to make sandwiches. Bread can be made into many different foods, like pizza. "
        "There are many different kinds of bread. Some are sweet and some are not.")
SHORT = "Twist bread is a type of bread rolled into a long shape. It is then baked over an open fire. The dough is made like bread dough."
LISTY = ("Bread is a type of baked food. It is mainly made from dough. Usually, salt and yeast are added. "
         "Bread is often baked in an oven. The two main types of bread are:\n\nLeavened bread is made by adding yeast "
         "to the dough. The yeast produces gas that makes the dough lighter. It can be made into larger loaves.")


def test_topics_file_and_reader(tmp_path):
    topics = P.read_topics()
    assert len(topics) >= 100 and "honey" in topics and len(topics) == len({t.lower() for t in topics})
    f = tmp_path / "t.txt"
    f.write_text("# comment\nbread\n\nBread  # again\nmilk\n", encoding="utf-8")
    assert P.read_topics(f) == ["bread", "milk"]


def test_excerpt_takes_the_longest_run_of_opening_sentences_within_the_length():
    text, why = P.excerpt(EASY, 50, 80)
    assert why == "" and 50 <= drills.word_count(text) <= 80 and EASY.startswith(text) and len(P.sentences(text)) >= 4
    assert text.endswith("Some are sweet and some are not.")                              # the longest run that fits
    assert P.excerpt(EASY, 50, 60)[0].endswith("to make sandwiches.")                      # a tighter cap stops earlier
    assert P.excerpt(SHORT, 50, 80) == (None, "too short: 27 words in the intro")
    text, why = P.excerpt(LISTY, 50, 80)                                                    # a colon introduces a list
    assert text is None and "no run" in why or "too short" in why
    assert P.excerpt(" ".join(["word"] * 90) + ".", 50, 80)[1].startswith("fewer than 4 sentences")
    assert P.excerpt("One. Two. Three four five. Six seven eight nine.", 6, 8)[1].startswith("no run")
    assert P.excerpt("", 50, 80) == (None, "no whole sentence")
    # sentences: whole ones, a tail without a stop is dropped
    assert P.sentences("One here. Two here! Three? four") == ["One here.", "Two here!", "Three?"]


def test_clean_drops_pronunciation_guides():
    assert P.clean("Honey (/\u02c8h\u028cni/) is\u00a0sweet , yes.") == "Honey is sweet, yes."
    assert P.clean("Paris (pronounced pa-REE) is big () and old (very old).") == "Paris is big and old (very old)."
    assert P.slugify("Great Wall of China") == "great-wall-of-china" and P.slugify("Allgäu Alps (mountains)") == "allgau-alps-mountains"


def test_reject_reasons():
    ok, _ = P.excerpt(EASY, 50, 80)
    assert P.reject(ok, LEX) == ""
    assert P.reject(ok + " See [1].", LEX).startswith("markup artifact")
    assert "unusual character" in P.reject(ok.replace("Bread is", "Bread (ˈbrɛd) is"), LEX)
    assert P.reject(ok.replace("Bread is", "Bread (a food is"), LEX) == "unbalanced parenthesis"
    assert P.reject(ok.replace("Bread is a type", "Mr. Bread is a type"), LEX).startswith("abbreviation or fragment")
    off = ok.replace("salt and yeast", "zorblax and quenting and frimbles")                # three off-list words
    assert P.reject(off, LEX) == "3 off-list content words (at most 2)"
    names = "Anna met Bob. " + " ".join(f"Anna met Bob in Paris on May {i}, 2001." for i in range(9))
    assert "names or numbers" in P.reject(names, LEX)
    assert P.reject("One sentence here. Two more words.", LEX).endswith("fewer than 6")


def fake_api(pages):
    """A MediaWiki `get` returning the same pages for every search term."""
    calls = []

    def get(params):
        calls.append(params["gsrsearch"])
        return {"query": {"pages": [{"title": t, "extract": e, "index": i} for i, (t, e) in enumerate(pages)]}}
    get.calls = calls
    return get


def test_fetch_writes_valid_files_and_logs_every_rejection(tmp_path):
    folder, out = tmp_path / "passages", tmp_path / "passages" / "incoming"
    folder.mkdir()
    (folder / "bread.md").write_text("---\nsource: x\nlicence: y\n---\n" + EASY + "\n", encoding="utf-8")   # already in the bank
    pages = [("Bread", EASY), ("Bread roll", EASY.replace("Bread", "Roll")), ("Twist bread", SHORT),
             ("List of breads", EASY), ("Bread (band)", EASY), ("Bread mold", EASY.replace("salt and yeast", "zorblax and quenting and frimbles"))]
    get = fake_api(pages)
    log = io.StringIO()
    n = P.fetch(folder, out, LEX, 5, 50, 80, ["bread", "milk"], 6, get, log, fetched=date(2026, 9, 17))
    assert n == 1 and sorted(p.name for p in out.glob("*.md")) == ["bread-roll.md"] and get.calls == ["bread", "milk"]
    meta, body = textdiff.passage_front_matter(out / "bread-roll.md")
    assert meta["source"] == "https://simple.wikipedia.org/wiki/Bread_roll" and meta["licence"] == "CC BY-SA 4.0"
    assert meta["fetched"] == "2026-09-17" and list(meta)[:3] == ["source", "licence", "fetched"]
    assert float(meta["b_text"]) == textdiff.b_text(body.strip(), LEX) and meta["b_adjust"] == "0" and "b90=" in meta["features"]
    assert body.strip().startswith("Roll is a type of baked food.")
    lines = log.getvalue().splitlines()
    assert "Twist bread: too short: 27 words in the intro" in lines
    assert "List of breads: skipped by title" in lines and "Bread (band): skipped by title" in lines
    assert "Bread mold: 3 off-list content words (at most 2)" in lines
    assert not any(line.startswith("Bread:") for line in lines)                            # in the bank: silently skipped
    # a scored, checked bank: the new file passes `check`; the cap on --n stops the run
    assert P.check(out, LEX) == 0
    out2 = tmp_path / "more"
    assert P.fetch(folder, out2, LEX, 0, 50, 80, ["bread"], 6, get, io.StringIO()) == 0 and not out2.exists()
    # a failed call is one log line, not the end of the run
    def boom(params):
        raise OSError("no network")
    log = io.StringIO()
    assert P.fetch(folder, tmp_path / "x", LEX, 5, 50, 80, ["bread"], 6, boom, log) == 0
    assert log.getvalue().startswith("bread: search failed (OSError: no network)")


def test_check_fails_without_licence_or_with_few_blanks(tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    run = lambda *args: subprocess.run([sys.executable, "scripts/passages.py", *args, "--folder", str(folder)],   # noqa: E731
                                       cwd=ROOT, capture_output=True, text=True)
    (folder / "good.md").write_text("---\nsource: https://x\nlicence: CC BY-SA 4.0\n---\n" + EASY + "\n", encoding="utf-8")
    assert run("score").returncode == 0 and run("check").returncode == 0
    (folder / "nolicence.md").write_text("---\nsource: https://x\n---\n" + EASY + "\n", encoding="utf-8")
    run("score")
    r = run("check")
    assert r.returncode == 1 and "nolicence.md: no licence: in the front matter" in r.stderr
    (folder / "nolicence.md").unlink()
    (folder / "short.md").write_text("---\nsource: https://x\nlicence: CC BY-SA 4.0\n---\nOne sentence here. Two more words here.\n", encoding="utf-8")
    run("score")
    r = run("check")
    assert r.returncode == 1 and "short.md: 2 blanks by the C-test rule, fewer than 6" in r.stderr
    assert run("fetch", "--topics", str(folder / "none.txt")).returncode != 0              # no topics file: nothing written


def test_the_committed_bank():
    """≥ 150 reviewed passages with source, licence and a current b_text, 50–80 words, spread over 3–12 so a θ
    between 4 and 11.5 has ten within ±0.6; the fetched ones carry the fetch date; `check` passes."""
    passages = drills.load_passages()
    assert len(passages) >= 150
    for p in passages:
        assert p["source"].startswith("https://simple.wikipedia.org/wiki/") and p["licence"] == "CC BY-SA 4.0"
        assert 3 <= p["b_text"] <= 12 and p["b_text"] == textdiff.b_text(p["text"], LEX)
        assert 50 <= drills.word_count(p["text"]) <= 82 and P.passage_blanks(p["text"]) >= P.MIN_PASSAGE_BLANKS
        assert P.reject(p["text"], LEX) == "" or p["slug"] in ("bicycle", "honey", "volcano")   # the three #15 originals
    assert sum(1 for p in passages if p.get("fetched")) >= 150
    bs = [p["b_text"] for p in passages]
    for theta in [x / 2 for x in range(8, 24)]:
        assert sum(1 for b in bs if abs(b - theta) <= 0.6) >= 10, theta
    assert P.check(drills.PASSAGES, LEX) == 0
    assert not (drills.PASSAGES / "incoming").exists()


@pytest.mark.parametrize("word,keep", [("tenant", 2), ("tenants", 3), ("the", 1), ("incomprehensible", 6)])
def test_fill_keep_is_ceil_of_a_third(word, keep):
    assert drills.fill_keep(word) == keep
