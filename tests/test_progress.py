"""Progress tracking (issue #9): the report dict, the generated Markdown, the marker contract of
vocab/progress.md, the Anki table, the CSV cross-check, the CLI and GET /api/progress."""
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import learn, main, progress, store
from app.bank import load_subbands
from tests.test_learn import blocks_session

SUBBANDS = load_subbands()
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "report.py"


def level_row(day, session, level, reliable=True, blocks=3):
    sb = next((b for b in SUBBANDS if b.name == level), None)
    return {"date": day, "session": session, "level": level or "", "det_low": str(sb.det_low) if sb else "",
            "det_high": str(sb.det_high) if sb else "", "blocks": str(blocks), "items": str(15 * blocks),
            "fa_rate": "0.0", "reliable": "1" if reliable else "0"}


def fixture():
    """Two reliable sessions on one day (3k-b, then 4k-a), a guess, and a reliable one on a later day."""
    old = blocks_session("old", "2026-09-01T10:00:00", [("4k-a", 7, 0), ("3k-b", 9, 0), ("4k-a", 7, 1)])
    same = blocks_session("same", "2026-09-01T12:00:00", [("4k-a", 9, 0), ("4k-b", 6, 0), ("4k-a", 9, 0)])
    guess = blocks_session("g", "2026-09-05T10:00:00", [("4k-a", 10, 5)], reliable=False)
    new = blocks_session("new", "2026-09-10T10:00:00", [("4k-a", 10, 0), ("4k-b", 6, 0), ("4k-a", 10, 0)])
    levels = [level_row("2026-09-01", "old", "3k-b"), level_row("2026-09-01", "same", "4k-a"),
              level_row("2026-09-05", "g", None, reliable=False, blocks=1), level_row("2026-09-10", "new", "4k-a")]
    return [old, same, guess, new], levels


def test_build_matches_learn_and_collapses_days():
    sessions, levels = fixture()
    r = progress.build(sessions, levels, SUBBANDS, date(2026, 9, 16))
    scores = learn.subband_scores(sessions, SUBBANDS)
    assert r["generated"] == "2026-09-16" and r["tests"] == 4 and r["reliable_tests"] == 3 and r["no_level"] == 0
    assert (r["level"], r["frontier"]) == learn.frontier(scores) == ("4k-a", "4k-b")
    assert r["last_level"] == "4k-a" and (r["cefr"], r["det_low"], r["det_high"]) == ("B2", 90, 105)
    assert r["trend"] == learn.level_history(levels)[1] == "flat"           # 4k-a → 4k-a on det_low
    # one row per sub-band in subbands.csv order, seven keys; score/status straight from subband_scores()
    assert [s["subband"] for s in r["subbands"]] == [b.name for b in SUBBANDS]
    assert all(set(s) == {"subband", "cefr", "det", "blocks", "known", "score", "status"} for s in r["subbands"])
    assert [(s["score"], s["status"]) for s in r["subbands"]] == [(s["score"], s["status"]) for s in scores]
    by = {s["subband"]: s for s in r["subbands"]}
    assert by["4k-a"]["known"] == round((7 * .25 + 7 * .25 + 9 * .5 + 9 * .5 + 10 + 10) / (20 * .25 + 20 * .5 + 20), 4)
    assert by["4k-a"]["score"] == round(by["4k-a"]["known"] - (1 * .25) / (10 * .25 + 10 * .5 + 10), 4)
    assert by["4k-b"] == {"subband": "4k-b", "cefr": "B2", "det": "90–105", "blocks": 2, "known": 0.6, "score": 0.6, "status": "not yet"}
    assert by["5k-a"] == {"subband": "5k-a", "cefr": "B2+", "det": "105–115", "blocks": 0, "known": None, "score": None, "status": "untested"}
    assert by["6k-b"]["det"] == "120+"
    # counts: every status plus seen; the same function the Learn page uses
    assert r["counts"] == learn.status_counts(learn.word_stats(sessions))
    assert set(r["counts"]) == {*learn.STATUSES, "seen"} and r["counts"]["seen"] == sum(r["counts"][k] for k in learn.STATUSES)
    # series: one point per day, the day's last reliable test with a level; the guess is left out
    assert r["series"] == [{"date": "2026-09-01", "session": "same", "order": 7, "level": "4k-a"},
                           {"date": "2026-09-10", "session": "new", "order": 7, "level": "4k-a"}]
    assert r["anki"] is None
    assert progress.now_line(r) == "Level 4k-a (B2) · DET ≈ 90–105 · frontier 4k-b · trend flat · 4 tests (3 reliable)"


def test_build_empty_history_and_last_test_differs():
    r = progress.build([], [], SUBBANDS, date(2026, 9, 16))
    assert r["level"] is None and r["frontier"] == "1k-a" and r["series"] == [] and r["tests"] == 0
    assert r["counts"]["seen"] == 0 and all(s["status"] == "untested" for s in r["subbands"])
    assert progress.now_line(r) == "No level yet · frontier 1k-a · 0 tests (0 reliable)"
    md = progress.render_markdown(r, SUBBANDS)
    assert "```mermaid" not in md and "_No reliable test with a level yet" in md and "### Anki" not in md
    # pooled level below the last test's level: the Now line names both; a reliable test with no level is footnoted
    sessions, levels = fixture()
    levels.append(level_row("2026-09-12", "x", None))
    sessions.append(blocks_session("x", "2026-09-12T10:00:00", [("1k-a", 5, 0)]))
    r = progress.build(sessions, levels, SUBBANDS, date(2026, 9, 16))
    assert r["last_level"] is None and r["no_level"] == 1 and len(r["series"]) == 2
    assert progress.now_line(r).endswith("5 tests (4 reliable) · last test no level")
    assert "_Not on the chart: 1 unreliable, 1 without a level, 1 earlier on a day with more than one test._" in progress.render_markdown(r, SUBBANDS)


def test_render_markdown_shape():
    sessions, levels = fixture()
    r = progress.build(sessions, levels, SUBBANDS, date(2026, 9, 16))
    md = progress.render_markdown(r, SUBBANDS)
    lines = md.split("\n")
    assert lines[:2] == [progress.HEADING, "_Generated by scripts/report.py on 2026-09-16 from vocab/tests/. Do not edit._"]
    assert md.endswith("\n") and "### Now\n\n" + progress.now_line(r) + "\n" in md
    rows = md.split("### Sub-bands\n\n")[1].split("\n\n### Words")[0].split("\n")
    assert rows[0] == "| Sub-band | CEFR | DET | Blocks | Known | Score | Status |"
    assert [r.split(" | ")[0][2:] for r in rows[2:]] == [b.name for b in SUBBANDS] and all(r.count("|") == 8 for r in rows)
    assert "| 4k-a | B2 | 90–105 | 6 | 93 % | 0.91 | mastered |" in md
    assert "| 4k-b | B2 | 90–105 | 2 | 60 % | 0.60 | not yet ← frontier |" in md
    assert "| 5k-a | B2+ | 105–115 | 0 | — | — | untested |" in md
    assert "\n### Words\n\nrepeat " in md and "(%d seen)" % r["counts"]["seen"] in md
    chart = progress.chart(r["series"], SUBBANDS)
    assert chart in md and chart.split("\n")[:2] == ["```mermaid", "xychart-beta"]
    assert '    x-axis ["1 Sep", "10 Sep"]' in chart and "    line [7, 7]" in chart
    assert '    y-axis "Sub-band" 1 --> 12' in chart and "11 = C1 / DET 120" in chart
    assert "| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |\n|---|" in md and "| 1k-a | 1k-b |" in md
    assert "_Not on the chart: 1 unreliable, 1 earlier on a day with more than one test._" in md
    r["anki"] = [{"subband": "4k-a", "cards": 40, "mature": 3, "reviews": 12, "lapses": 2}]
    md = progress.render_markdown(r, SUBBANDS)
    assert "### Anki\n\n| Sub-band | Cards | Mature | Reviews 7d | Lapses |\n|---|---|---|---|---|\n| 4k-a | 40 | 3 | 12 | 2 |\n" in md
    r["anki"] = []
    assert "### Anki\n\n_No cards tagged" in progress.render_markdown(r, SUBBANDS)


HAND = "# Progress\n\nTarget: 120.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
BLOCK = "## Progress (generated)\n_Generated by scripts/report.py on 2026-09-16 from vocab/tests/. Do not edit._\n\nbody\n"


def test_splice_four_cases():
    S, E = progress.START, progress.END
    # 1. both markers: only the text strictly between them changes
    text = HAND + S + "\n_Nothing yet._\n" + E + "\nbelow\n"
    out = progress.splice(text, BLOCK)
    assert out == HAND + S + "\n" + BLOCK + E + "\nbelow\n"
    assert progress.splice(out, BLOCK) == out                                       # idempotent
    assert progress.splice(out, BLOCK.replace("body", "other")).startswith(HAND + S + "\n## Progress")
    assert progress.splice(HAND + S + "\n" + E + "\n", BLOCK) == HAND + S + "\n" + BLOCK + E + "\n"   # empty block
    # 2. both missing: a blank line, the markers and the block go at the end; the text above is untouched
    assert progress.splice(HAND.rstrip("\n"), BLOCK) == HAND.rstrip("\n") + "\n\n" + S + "\n" + BLOCK + E + "\n"
    assert progress.splice(HAND, BLOCK) == HAND + "\n" + S + "\n" + BLOCK + E + "\n"
    assert progress.splice("", BLOCK) == S + "\n" + BLOCK + E + "\n"
    # 3. one marker missing, or a marker repeated → ValueError
    for bad in (HAND + S + "\n", HAND + E + "\n", HAND + S + "\n" + E + "\n" + E + "\n", HAND + "x " + S + "\n" + E + "\n"):
        with pytest.raises(ValueError):
            progress.splice(bad, BLOCK)
    # 4. end before start → ValueError
    with pytest.raises(ValueError, match="comes before"):
        progress.splice(E + "\n" + S + "\n", BLOCK)


def test_anki_stats_from_a_collection():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE notes (id INTEGER PRIMARY KEY, tags TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, ivl INTEGER, lapses INTEGER);
        CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER);
        INSERT INTO notes VALUES (1, ' 4k-a new '), (2, ' 4k-a missed '), (3, ' 3k-b my-words '), (4, ' extra my-words ');
        INSERT INTO cards VALUES (10, 1, 30, 0), (11, 1, 2, 1), (12, 2, 21, 3), (13, 3, 0, 0), (14, 4, 50, 0);
    """)
    now = datetime(2026, 9, 16, 12)
    ms = lambda d: int(datetime(2026, 9, d, 12).timestamp() * 1000)                 # noqa: E731
    conn.executemany("INSERT INTO revlog VALUES (?, ?)", [(ms(15), 10), (ms(15) + 1, 10), (ms(10), 11), (ms(1), 12), (ms(16), 13)])
    rows = progress.anki_stats(conn, SUBBANDS, now)
    assert rows == [{"subband": "3k-b", "cards": 1, "mature": 0, "reviews": 1, "lapses": 0},
                    {"subband": "4k-a", "cards": 3, "mature": 2, "reviews": 3, "lapses": 4}]   # `extra` is not a sub-band
    conn.execute("DELETE FROM cards")
    assert progress.anki_stats(conn, SUBBANDS, now) == []


def test_check_finds_a_hand_edited_csv():
    sessions, levels = fixture()
    for s in sessions:
        s["result"].update({"level": "4k-a" if s["result"]["reliable"] else None, "blocks": len(s["blocks"])})
    levels[0]["level"] = "4k-a"
    results = [{"session": s["id"], "subband": b["subband"], "hits": str(b["hits"]), "false_alarms": str(b["false_alarms"])}
               for s in sessions for b in s["blocks"]]
    assert progress.check(sessions, levels, results) == []
    levels[0]["level"], levels[1]["blocks"] = "3k-b", "9"
    results[0]["hits"] = "6"
    del results[-1]
    problems = progress.check(sessions, levels + [level_row("2026-09-20", "ghost", "4k-a")], results)
    assert problems == ["old: levels.csv level = '3k-b', session says '4k-a'",
                        "old block 1: results.csv says 4k-a 6/0, session says 4k-a 7/0",
                        "same: levels.csv blocks = '9', session says '3'",
                        "new: 2 results.csv rows, session has 3 finished blocks",
                        "ghost: levels.csv row without a session file"]


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True)


def test_cli_rewrites_only_the_generated_block(tmp_path):
    """--out on a copy of the real file, then the missing-file, no-marker and broken-marker cases; --print."""
    out = tmp_path / "progress.md"
    real = (ROOT / "vocab" / "progress.md").read_text(encoding="utf-8")
    out.write_text(real, encoding="utf-8")
    r = run("--out", str(out))
    assert r.returncode == 0 and r.stdout.startswith(("Level ", "No level yet")) and str(out) in r.stdout
    first = out.read_text(encoding="utf-8")
    head = lambda t: t.split(progress.START + "\n")[0]                                # noqa: E731
    assert head(first) == head(real) and first.endswith("\n" + progress.END + "\n") and first.count(progress.START) == 1
    inside = first.split(progress.START + "\n")[1].split(progress.END)[0].split("\n")
    assert inside[0] == progress.HEADING and inside[1] == f"_Generated by scripts/report.py on {date.today()} from vocab/tests/. Do not edit._"
    assert run("--out", str(out)).returncode == 0 and out.read_text(encoding="utf-8") == first   # no diff on a second run

    missing = tmp_path / "new" / "p.md"
    assert run("--out", str(missing)).returncode == 0
    assert missing.read_text(encoding="utf-8").startswith(progress.STUB + "\n" + progress.START + "\n" + progress.HEADING)

    plain = tmp_path / "plain.md"
    plain.write_text("# Mine\n\nkeep", encoding="utf-8")
    assert run("--out", str(plain)).returncode == 0
    assert plain.read_text(encoding="utf-8").startswith("# Mine\n\nkeep\n\n" + progress.START + "\n" + progress.HEADING)

    for bad in (progress.START + "\n", progress.END + "\n" + progress.START + "\n"):
        broken = tmp_path / "broken.md"
        broken.write_text(bad, encoding="utf-8")
        r = run("--out", str(broken))
        assert r.returncode == 1 and "nothing written" in r.stderr and broken.read_text(encoding="utf-8") == bad

    before = out.read_text(encoding="utf-8")
    r = run("--print", "--out", str(out))
    assert r.returncode == 0 and r.stdout.startswith(progress.HEADING + "\n_Generated") and out.read_text(encoding="utf-8") == before
    assert run("--check").returncode == 0


def test_api_progress_matches_learn(tmp_path, monkeypatch):
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "my-words.csv")
    c = TestClient(main.app)
    p = c.get("/api/progress").json()
    assert p["tests"] == 0 and p["level"] is None and p["series"] == [] and p["anki"] is None
    sid = c.post("/api/session").json()["session"]
    status = None
    while status != "finished":
        r = c.post(f"/api/session/{sid}/answer", json={"yes": True, "ms": 800}).json()
        status = r["status"]
        if status == "block_done":
            c.post(f"/api/session/{sid}/next")
    p, l = c.get("/api/progress").json(), c.get("/api/learn?n=5").json()
    assert p["tests"] == l["sessions"] == 1 and p["reliable_tests"] == 0 and p["no_level"] == 0   # yes to all = guessing
    assert (p["level"], p["frontier"], p["trend"]) == (l["level"], l["frontier"], l["trend"])
    assert p["subbands"] == l["subbands"] and p["series"] == l["series"] == []
    assert {k: l["counts"][k] for k in p["counts"]} == p["counts"] and "my-words" in l["counts"]
    assert set(p["counts"]) == {*learn.STATUSES, "seen"} and p["counts"]["seen"] == 60
