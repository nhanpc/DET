"""Progress tracking (issue #9): the report dict, the generated Markdown, the marker contract of
vocab/progress.md, the Anki table, the CSV cross-check, the CLI and GET /api/progress. Mock tests (issue #11):
the 5-row worked example of the issue, every branch of the focus and booking rules, validation, the section."""
import importlib.util
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import irt, learn, main, progress, store
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
    # level and frontier from θ: the last reliable session ("new": 20/20 at 4k-a, 6/10 at 4k-b), prior chained
    thetas = learn.theta_history(sessions, SUBBANDS)
    assert r["thetas"] == thetas and [h["id"] for h in thetas] == ["old", "same", "g", "new"]
    assert (r["theta"], r["se"]) == learn.current_theta(thetas) == (thetas[-1]["theta"], thetas[-1]["se"])
    assert (r["level"], r["frontier"]) == learn.frontier(r["theta"], SUBBANDS) == ("4k-b", "5k-a")
    assert (r["det_estimate"], r["det_range"]) == (98, [95, 101]) and 8.2 < r["theta"] < 8.3
    assert r["theta_series"] == [{"date": h["date"], "started": h["started"], "session": h["id"], "theta": h["theta"],
                                  "se": h["se"], "frontier": h["frontier"]} for h in thetas if h["id"] != "g"]
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
    assert progress.now_line(r) == "Level 4k-b (B2) · DET ≈ 98 (95–101) · θ 8.25 ± 0.37 · frontier 5k-a · trend flat · " \
                                   "4 tests (3 reliable) · last test 4k-a"              # the levels.csv row is the old rule's


def test_build_empty_history_and_last_test_differs():
    r = progress.build([], [], SUBBANDS, date(2026, 9, 16))
    assert r["level"] is None and r["frontier"] == "1k-a" and r["series"] == [] and r["tests"] == 0
    assert r["theta"] is None and r["se"] is None and r["det_estimate"] is None and r["thetas"] == r["theta_series"] == []
    assert r["counts"]["seen"] == 0 and all(s["status"] == "untested" for s in r["subbands"])
    assert progress.now_line(r) == "No level yet · frontier 1k-a · 0 tests (0 reliable)"
    md = progress.render_markdown(r, SUBBANDS)
    assert "```mermaid" not in md and "_No reliable test yet" in md and "### Anki" not in md and "_Not on the chart" not in md
    # a weak last session: θ below the mastery gap → no level; the θ line says so and stays on the chart
    sessions, levels = fixture()
    levels.append(level_row("2026-09-12", "x", None))
    sessions.append(blocks_session("x", "2026-09-12T10:00:00", [("1k-a", 3, 0)]))
    r = progress.build(sessions, levels, SUBBANDS, date(2026, 9, 16))
    assert r["last_level"] is None and r["no_level"] == 1 and len(r["series"]) == 2 and len(r["theta_series"]) == 4
    assert r["level"] is None and r["theta"] < irt.MASTERY_GAP and r["det_estimate"] == 10
    assert progress.now_line(r) == "No level yet · θ 0.64 ± 0.37 · frontier 1k-a · trend down · 5 tests (4 reliable)"
    md = progress.render_markdown(r, SUBBANDS)
    assert "- 12 Sep 2026 10:00 · θ = 0.64 ± 0.37 → 1k-a (no level), DET ≈ 10 (10–10)\n" in md
    assert "_Not on the chart: 1 unreliable._" in md
    # a stronger last session whose levels.csv row (old rule) says no level: the Now line names both
    sessions[-1] = blocks_session("x", "2026-09-12T10:00:00", [("1k-a", 7, 0)])
    r = progress.build(sessions, levels, SUBBANDS, date(2026, 9, 16))
    assert r["level"] == "1k-a" and progress.now_line(r).endswith("5 tests (4 reliable) · last test no level")


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
    assert "| 4k-b | B2 | 90–105 | 2 | 60 % | 0.60 | not yet |" in md
    assert "| 5k-a | B2+ | 105–115 | 0 | — | — | untested ← frontier |" in md
    assert "\n### Words\n\nrepeat " in md and "(%d seen)" % r["counts"]["seen"] in md
    # the ability section: one line per finished session (unreliable ones marked), then the θ chart
    lines = md.split("### Ability over time\n\n")[1].split("\n\n```mermaid")[0].split("\n")
    assert lines == progress.theta_lines(r["thetas"]) and len(lines) == 4
    assert lines[0] == "- 1 Sep 2026 10:00 · θ = 6.97 ± 0.28 → 4k-a (level 3k-b), DET ≈ 83 (79–91)"
    assert lines[2].endswith("DET ≈ 95 (91–98) · unreliable") and lines[3].startswith("- 10 Sep 2026 10:00 · θ = 8.25 ± 0.37 → 5k-a (level 4k-b)")
    chart = progress.chart(r["theta_series"], SUBBANDS)
    assert chart in md and chart.split("\n")[:2] == ["```mermaid", "xychart-beta"]
    assert '    x-axis ["1 Sep 10:00", "1 Sep 12:00", "10 Sep"]' in chart and "    line [6.97, 7.85, 8.25]" in chart
    assert '    y-axis "θ" 0 --> 12' in chart and "10 = C1 / DET 120" in chart and "    line [10, 10, 10]" in chart
    assert "| 0–1 | 1–2 | 2–3 | 3–4 | 4–5 | 5–6 | 6–7 | 7–8 | 8–9 | 9–10 | 10–11 | 11–12 |\n|---|" in md and "| 1k-a | 1k-b |" in md
    assert "_Not on the chart: 1 unreliable._" in md
    r["anki"] = [{"subband": "4k-a", "cards": 40, "mature": 3, "reviews": 12, "lapses": 2}]
    md = progress.render_markdown(r, SUBBANDS)
    assert "### Anki\n\n| Sub-band | Cards | Mature | Reviews 7d | Lapses |\n|---|---|---|---|---|\n| 4k-a | 40 | 3 | 12 | 2 |\n" in md
    r["anki"] = []
    assert "### Anki\n\n_No cards tagged" in progress.render_markdown(r, SUBBANDS)


def mock_row(day, source, overall, lit="", comp="", conv="", prod="", weakest="", notes=""):
    """A mocks.csv row as store.load_mocks() returns it (strings, blanks for missing subscores)."""
    return {"date": day, "source": source, "overall": str(overall), "literacy": str(lit), "comprehension": str(comp),
            "conversation": str(conv), "production": str(prod), "weakest": weakest, "notes": notes}


MOCKS = [mock_row("2026-09-20", "det-practice", 95, weakest="conversation", notes="range 95–110"),
         mock_row("2026-10-04", "mock-a", 105, 100, 110, 100, 110),
         mock_row("2026-10-18", "det-practice", 120),
         mock_row("2026-10-18", "det-practice", 125),                     # same-day retake replaces the row above
         mock_row("2026-11-01", "det-practice", 120, weakest="production")]
FOCUS = [("conversation", "weakest column"), ("literacy", "subscores of 2026-10-04"), ("literacy", "subscores of 2026-10-04"),
         ("literacy", "subscores of 2026-10-04"), ("literacy", "subscores of 2026-10-04")]
VERDICT = [("keep going", 25), ("keep going", 15), ("one more ≥ 120 to book", None), ("one more ≥ 120 to book", None),
           ("Book the real test", None)]


def test_mocks_worked_example():
    """The issue's table: focus and verdict after every row, the trend, de-duplication, the vocab column."""
    history = learn.level_history(fixture()[1])[0]
    for i in range(1, 6):
        m = progress.mocks_report(MOCKS[:i], history, date(2026, 11, 2))
        assert (m["focus"]["subscore"], m["focus"]["reason"]) == FOCUS[i - 1], i
        assert (m["verdict"], m["gap"]) == VERDICT[i - 1], i
    assert m["focus"]["folders"] == progress.FOLDERS["literacy"] == ["read-and-complete/", "writing/"]
    assert [(r["date"], r["source"], r["overall"]) for r in m["rows"]] == [
        ("2026-09-20", "det-practice", 95), ("2026-10-04", "mock-a", 105), ("2026-10-18", "det-practice", 125),
        ("2026-11-01", "det-practice", 120)]
    assert m["series"] == [{"date": d, "overall": o} for d, o in
                           (("2026-09-20", 95), ("2026-10-04", 105), ("2026-10-18", 125), ("2026-11-01", 120))]
    assert m["trend"] == "down" and not m["overdue"]                         # 125 → 120; last row 1 day old
    assert [r["vocab"] for r in m["rows"]] == ["90–105"] * 4                 # the 2026-09-10 reliable 4k-a test
    assert m["rows"][0]["notes"] == "range 95–110" and m["rows"][1]["literacy"] == 100 and m["rows"][0]["literacy"] is None
    # trend on the last two dates, not rows: after row 3 it is 105 → 120 = up; row 4 alone keeps it up
    assert progress.mocks_report(MOCKS[:3], history, date(2026, 10, 19))["trend"] == "up"
    # 28-day shelf life: dated 2026-11-02 the mock-a subscores are 29 days old and the weakest column takes over
    late = MOCKS[:4] + [mock_row("2026-11-02", "det-practice", 120, weakest="production")]
    f = progress.mocks_report(late, history, date(2026, 11, 2))["focus"]
    assert (f["subscore"], f["reason"], f["folders"]) == ("production", "weakest column", ["writing/", "speaking/"])
    # mock overdue: no row within 14 days before `generated`; no vocab column before the first reliable test
    m = progress.mocks_report(MOCKS[:1], history, date(2026, 10, 5))
    assert m["overdue"] and m["trend"] == "" and m["rows"][0]["vocab"] == "90–105"
    assert not progress.mocks_report(MOCKS[:1], history, date(2026, 10, 4))["overdue"]
    assert progress.mocks_report([mock_row("2026-08-01", "det-practice", 60)], history, date(2026, 8, 1))["rows"][0]["vocab"] == ""
    m = progress.mocks_report([], history, date(2026, 11, 2))
    assert m == {"rows": [], "series": [], "trend": "", "overdue": True, "verdict": "keep going", "gap": None,
                 "focus": {"subscore": None, "folders": [], "reason": "no mocks"}}


def test_mock_rules_every_branch():
    rows = lambda *rs: progress.mock_rows(progress.parse_mocks(list(rs)))                       # noqa: E731
    # Goal reached: any official row ≥ 120, whatever the practice rows say; an official row < 120 is just shown
    official = mock_row("2026-11-08", "official", 125, 120, 125, 120, 130)
    assert progress.mock_verdict(rows(*MOCKS[:2], official)) == ("Goal reached", None)
    low = mock_row("2026-11-08", "official", 115, 110, 115, 120, 115)
    assert progress.mock_verdict(rows(*MOCKS, low)) == ("Book the real test", None)
    assert progress.mock_verdict(rows(*MOCKS[:2], low)) == ("keep going", 15)
    # two rows ≥ 120 on one day are one date: not enough to book; two sources on a day count the lower overall
    same = rows(mock_row("2026-10-18", "det-practice", 120), mock_row("2026-10-18", "mock-a", 125))
    assert progress.mock_verdict(same) == ("one more ≥ 120 to book", None) and progress.mock_dates(same) == [("2026-10-18", 120)]
    mixed = rows(mock_row("2026-10-18", "det-practice", 125), mock_row("2026-10-18", "mock-a", 115),
                 mock_row("2026-11-01", "det-practice", 120))
    assert progress.mock_verdict(mixed) == ("one more ≥ 120 to book", None)
    # an official row on a practice day is kept beside it and left out of the practice dates
    both = rows(mock_row("2026-10-18", "det-practice", 125), mock_row("2026-10-18", "official", 100))
    assert len(both) == 2 and progress.mock_dates(both) == [("2026-10-18", 100)]
    assert progress.mock_dates(both, practice_only=True) == [("2026-10-18", 125)]
    # focus tie-break: the biggest drop since the previous row with subscores, then table order
    a, b = mock_row("2026-10-04", "mock-a", 105, 110, 110, 120, 110), mock_row("2026-10-11", "mock-a", 105, 100, 105, 100, 110)
    assert progress.mock_focus(rows(a, b))["subscore"] == "conversation"       # 120 → 100 beats 110 → 100
    c = mock_row("2026-10-11", "mock-a", 105, 100, 105, 100, 100)
    assert progress.mock_focus(rows(a, c))["subscore"] == "conversation"       # production dropped 10, conversation 20
    d = mock_row("2026-10-11", "mock-a", 105, 100, 105, 100, 110)
    assert progress.mock_focus(rows(mock_row("2026-10-04", "mock-a", 105, 110, 110, 110, 110), d))["subscore"] == "literacy"
    assert progress.mock_focus(rows(d))["subscore"] == "literacy"              # no previous row → table order
    # no weakest: rows exist, the latest has neither subscores nor a weakest, and no fresh subscores
    f = progress.mock_focus(rows(MOCKS[1], mock_row("2026-11-02", "det-practice", 110)))
    assert f == {"subscore": None, "folders": [], "reason": "no weakest"}
    # a weakest typed on a row that also has fresh subscores: the subscores win
    e = mock_row("2026-10-11", "mock-a", 105, 100, 105, 110, 110, weakest="production")
    assert progress.mock_focus(rows(e))["reason"] == "subscores of 2026-10-11"


def test_parse_mocks_rejects_a_bad_row_with_its_line():
    ok = progress.parse_mocks(MOCKS)
    assert len(ok) == 5 and ok[1]["production"] == 110 and ok[0]["weakest"] == "conversation" and ok[2]["weakest"] == ""
    bad = [(mock_row("2026-9-20", "det-practice", 95), "line 3: date = '2026-9-20', expected YYYY-MM-DD"),
           (mock_row("20261004", "det-practice", 95), "line 3: date"),
           (mock_row("2026-10-04", "", 95), "line 3: source is empty"),
           (mock_row("2026-10-04", "det-practice", ""), "line 3: overall = '', expected a multiple of 5 in 10–160"),
           (mock_row("2026-10-04", "det-practice", 102), "line 3: overall = '102'"),
           (mock_row("2026-10-04", "det-practice", 165), "line 3: overall = '165'"),
           (mock_row("2026-10-04", "det-practice", 5), "line 3: overall = '5'"),
           (mock_row("2026-10-04", "mock-a", 105, 100, 111, 100, 110), "line 3: comprehension = '111'"),
           (mock_row("2026-10-04", "det-practice", 105, weakest="reading"), "line 3: weakest = 'reading', expected one of")]
    for row, msg in bad:
        with pytest.raises(ValueError, match=msg):
            progress.parse_mocks([MOCKS[0], row])
    with pytest.raises(ValueError, match="line 2: "):
        progress.parse_mocks([mock_row("2026-10-04", "det-practice", "abc")])
    assert progress.parse_mocks([{"date": " 2026-10-04 ", "source": "x", "overall": " 105 "}])[0]["overall"] == 105


def test_mock_section_markdown():
    sessions, levels = fixture()
    r = progress.build(sessions, levels, SUBBANDS, date(2026, 11, 2), mocks=MOCKS)
    r["anki"] = []
    md = progress.render_markdown(r, SUBBANDS)
    section = md.split("\n### Mock tests\n\n")[1]
    assert md.index("### Anki") < md.index("### Mock tests") and md.endswith("- Booking: **Book the real test**\n")
    assert section.startswith(progress.mock_chart(r["mocks"]["series"]) + "\n\n| Date | Source | Overall | Literacy | "
                              "Comprehension | Conversation | Production | Weakest | Vocab |\n|---|")
    assert progress.mock_chart(r["mocks"]["series"]).split("\n") == [
        "```mermaid", "xychart-beta", '    title "Mock tests: overall"', '    x-axis ["09-20", "10-04", "10-18", "11-01"]',
        '    y-axis "DET" 60 --> 160', "    line [95, 105, 125, 120]", "    line [120, 120, 120, 120]", "```"]
    assert "| 2026-09-20 | det-practice | 95 |  |  |  |  | conversation | 90–105 |" in section
    assert "| 2026-10-04 | mock-a | 105 | 100 ← | 110 | 100 ← | 110 |  | 90–105 |" in section
    assert "| 2026-10-18 | det-practice | 125 |" in section and "| 120 |  |  |  |  |  | 90–105 |" not in section
    assert section.endswith("\n- Trend: down\n- Focus next week: Literacy → read-and-complete/, writing/ (subscores of 2026-10-04)\n"
                            "- Booking: **Book the real test**\n")
    # official rows in bold, Goal reached in bold, the y-axis follows a low score
    r = progress.build(sessions, levels, SUBBANDS, date(2026, 11, 9),
                       mocks=MOCKS[:1] + [mock_row("2026-11-08", "official", 120, 115, 125, 120, 130)])
    md = progress.render_markdown(r, SUBBANDS)
    assert "| **2026-11-08** | **official** | **120** | **115 ←** | **125** | **120** | **130** |  | **90–105** |" in md
    assert md.endswith("- Booking: **Goal reached**\n") and "(subscores of 2026-11-08)" in md
    r = progress.build([], [], SUBBANDS, date(2026, 11, 2), mocks=[mock_row("2026-10-25", "det-practice", 45)])
    md = progress.render_markdown(r, SUBBANDS)
    assert '    y-axis "DET" 40 --> 160' in md and "|  |  |  |  |  |  |\n" in md
    assert md.endswith("- Trend: —\n- Focus next week: vocabulary → 1k-a (no weakest)\n- Booking: keep going (gap 75)\n")
    # no rows: a hint instead of the chart, `mock overdue`, vocabulary focus, no gap
    md = progress.render_markdown(progress.build([], [], SUBBANDS, date(2026, 11, 2)), SUBBANDS)
    assert md.endswith("### Mock tests\n\n_No mock yet — take the free practice test and type it as row 1 of vocab/tests/mocks.csv._\n\n"
                       "- Trend: — · mock overdue\n- Focus next week: vocabulary → 1k-a (no mocks)\n- Booking: keep going\n")
    r = progress.build([], [], SUBBANDS, date(2026, 11, 2), mocks=MOCKS[:1])
    assert "- Trend: — · mock overdue\n- Focus next week: Conversation → listen-and-type/, speaking/ (weakest column)\n" in progress.render_markdown(r, SUBBANDS)


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


def test_cli_stops_on_a_bad_mock_row(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("report", SCRIPT)
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    out = tmp_path / "progress.md"
    monkeypatch.setattr(store, "MOCKS", tmp_path / "mocks.csv")
    assert report.main(["--out", str(out)]) == 0 and "_No mock yet" in out.read_text(encoding="utf-8")   # no file = no rows
    lines = [",".join(store.MOCKS_HEADER), "2026-09-20,det-practice,95,,,,,conversation,range 95–110",
             "2026-10-04,det-practice,102,,,,,,"]
    store.MOCKS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = out.read_text(encoding="utf-8")
    assert report.main(["--out", str(out)]) == 1 and out.read_text(encoding="utf-8") == before
    assert capsys.readouterr().err == f"{store.MOCKS}: line 3: overall = '102', expected a multiple of 5 in 10–160 — nothing written\n"
    store.MOCKS.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    assert report.main(["--out", str(out)]) == 0
    assert "| 2026-09-20 | det-practice | 95 |  |  |  |  | conversation |" in out.read_text(encoding="utf-8")


def test_api_progress_matches_learn(tmp_path, monkeypatch):
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS", "MOCKS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "my-words.csv")
    c = TestClient(main.app)
    p = c.get("/api/progress").json()
    assert p["tests"] == 0 and p["level"] is None and p["series"] == [] and p["anki"] is None
    assert p["mocks"]["rows"] == [] and p["mocks"]["verdict"] == "keep going" and p["mocks"]["focus"]["reason"] == "no mocks"
    # the mock rows ride on the same dict; a bad row is a 422 naming the line
    store.MOCKS.write_text(",".join(store.MOCKS_HEADER) + "\n2026-09-20,det-practice,95,,,,,conversation,\n", encoding="utf-8")
    m = c.get("/api/progress").json()["mocks"]
    assert m["rows"][0]["overall"] == 95 and m["rows"][0]["literacy"] is None and m["focus"]["subscore"] == "conversation"
    assert m["gap"] == 25 and m["overdue"] == (date.today() > date(2026, 10, 4))
    store.MOCKS.write_text(",".join(store.MOCKS_HEADER) + "\n2026-09-20,det-practice,95,,,,,reading,\n", encoding="utf-8")
    r = c.get("/api/progress")
    assert r.status_code == 422 and r.json()["detail"].startswith("vocab/tests/mocks.csv: line 2: weakest = 'reading'")
    store.MOCKS.unlink()
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
    assert set(p["counts"]) == {*learn.STATUSES, "seen"} and p["counts"]["seen"] == p["counts"]["known"]
    assert p["counts"]["seen"] % 10 == 0 and 20 <= p["counts"]["seen"] <= 60                  # 10 real words a block
    assert (p["theta"], p["level"], p["frontier"]) == (None, None, "1k-a") and not p["thetas"][0]["reliable"]
    assert p["theta_series"] == [] and (l["theta"], l["thetas"]) == (None, p["thetas"])
