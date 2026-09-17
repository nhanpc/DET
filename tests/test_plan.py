"""The learning schedule (issue #19): the gate file, the calendar, the slide, the streak, the Today dict, the
words tick through the API, the Plan section of the report and the CLI."""
import importlib.util
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, plan, progress, store
from app.bank import load_subbands

SUBBANDS = load_subbands()
START = date(2026, 9, 21)                      # a Monday
PLAN = plan.make(START, SUBBANDS)


def t(day, level, reliable=True):
    return {"date": day, "level": level, "reliable": reliable}


def test_make_gates_and_start():
    assert [(g["subband"], g["week"], g["due"]) for g in PLAN] == [
        ("4k-a", 3, "2026-10-11"), ("4k-b", 8, "2026-11-15"), ("5k-a", 13, "2026-12-20"),
        ("5k-b", 18, "2027-01-24"), ("6k-a", 23, "2027-02-28")]
    assert all(date.fromisoformat(g["due"]).weekday() == 6 for g in PLAN)      # every gate ends on a Sunday
    assert plan.start_of(PLAN) == START
    with pytest.raises(ValueError):
        plan.make(START + timedelta(days=1), SUBBANDS)                           # not a Monday
    with pytest.raises(ValueError):
        plan.make(START, SUBBANDS, first="6k-a")                                 # only two sub-bands left, five gates asked
    assert [g["subband"] for g in plan.make(START, SUBBANDS, first="5k-b", spacing=(4, 4))] == ["5k-b", "6k-a"]


def test_plan_file_round_trip(tmp_path):
    path = plan.write_plan(PLAN, tmp_path / "plan.csv")
    assert plan.load_plan(path) == PLAN
    assert plan.load_plan(tmp_path / "missing.csv") == []


def test_session_days_alternate_over_drill_days_and_survive_a_missed_day():
    days = {START + timedelta(days=i): plan.session_day(START, START + timedelta(days=i)) for i in range(14)}
    assert [days[START + timedelta(days=i)] for i in range(7)] == [1, 2, 3, 4, 5, 5, 5]   # Sat and Sun add nothing
    assert plan.drill_for(1) == ("listen-and-type",) and plan.drill_for(2) == ("read-and-complete", "fill-in-the-blanks")
    assert plan.drill_for(days[START + timedelta(days=4)]) == ("listen-and-type",)         # Friday of week 1
    assert plan.drill_for(days[START + timedelta(days=7)]) == plan.drill_for(2)           # Monday of week 2 flips
    # the numbering is the calendar's, so skipping Wednesday leaves Thursday's drill where it was
    assert plan.drill_for(days[START + timedelta(days=3)]) == plan.drill_for(4)
    # before the start the count runs backwards and keeps the alternation into week 1
    assert plan.session_day(START, START - timedelta(days=3)) == 0                          # the Friday before
    assert plan.session_day(START, START - timedelta(days=4)) == -1                         # the Thursday before
    assert plan.week_of(START, START) == 1 and plan.week_of(START, START + timedelta(days=13)) == 2
    assert plan.week_of(START, START - timedelta(days=1)) == 0


def test_gates_pass_early_without_moving_anything():
    gs = plan.gates(PLAN, [t("2026-09-15", "4k-a")], SUBBANDS, date(2026, 9, 17))
    assert gs[0]["status"] == "passed" and gs[0]["passed_on"] == "2026-09-15" and gs[0]["late"] == 0
    assert [g["status"] for g in gs[1:]] == ["open"] * 4
    assert all(g["due_now"] == g["due"] for g in gs)
    # a level beyond the gate passes it and every gate below it, on the same date; an unreliable session never does
    gs = plan.gates(PLAN, [t("2026-10-01", "5k-a", reliable=False), t("2026-10-03", "5k-a")], SUBBANDS, date(2026, 10, 4))
    assert [g["passed_on"] for g in gs] == ["2026-10-03"] * 3 + [None, None]


def test_missed_gate_slides_every_later_gate_by_whole_weeks():
    today = date(2026, 10, 12)                                     # the Monday after gate 1's Sunday, nothing passed
    gs = plan.gates(PLAN, [], SUBBANDS, today)
    assert gs[0]["status"] == "late" and gs[0]["late"] == 1 and gs[0]["due_now"] == "2026-10-18"
    assert [g["due_now"] for g in gs[1:]] == ["2026-11-22", "2026-12-27", "2027-01-31", "2027-03-07"]
    assert [g["status"] for g in gs[1:]] == ["open"] * 4
    # a second week late
    assert plan.gates(PLAN, [], SUBBANDS, date(2026, 10, 19))[0]["late"] == 2
    # passed a week late: the slide is recorded from the pass date, and stays
    gs = plan.gates(PLAN, [t("2026-10-14", "4k-a")], SUBBANDS, date(2026, 12, 1))
    assert gs[0]["status"] == "passed" and gs[0]["late"] == 1 and gs[0]["due_now"] == "2026-10-18"
    assert gs[1]["status"] == "late" and gs[1]["late"] == 2                      # Dec 1 is 9 days past Nov 22
    assert gs[1]["due_now"] == "2026-12-06" and gs[2]["due_now"] == "2027-01-10"  # 13 weeks + 3 weeks of slide


def test_streak_skips_sundays_and_gives_today_grace():
    mon = date(2026, 9, 21)
    done = {mon - timedelta(days=2), mon - timedelta(days=3), mon - timedelta(days=4)}    # Thu, Fri, Sat
    assert plan.streak(done, mon) == 3                       # Monday not done yet: the chain ends on Saturday, Sunday skipped
    assert plan.streak(done | {mon}, mon) == 4
    assert plan.streak(done, mon - timedelta(days=1)) == 3   # asked on the Sunday itself
    assert plan.streak(done, mon + timedelta(days=1)) == 0   # Monday missed: the chain broke
    assert plan.streak({mon - timedelta(days=2), mon - timedelta(days=4)}, mon) == 1   # Friday missing
    assert plan.streak(set(), mon) == 0


def test_today_plan_rows():
    kw = dict(thetas=[t("2026-09-15", "4k-a")], subbands=SUBBANDS, results=[], log=[])
    thu = date(2026, 9, 24)
    p = plan.today_plan(PLAN, attempts=[{"date": "2026-09-24", "task": "fill-in-the-blanks"}], today=thu, **kw)
    assert p["week"] == 1 and p["session_day"] == 4 and p["drill"] == "read-and-complete"
    assert p["drill_done"] == 1 and not p["words_done"] and not p["off"] and not p["test_day"]
    assert p["next"]["subband"] == "4k-b" and p["on_track"] and p["projected"] == "2027-03-03" and p["streak"] == 1
    p = plan.today_plan(PLAN, attempts=[], today=thu, **{**kw, "log": [{"date": "2026-09-24", "words": "1"}]})
    assert p["words_done"] and p["streak"] == 1
    sat = plan.today_plan(PLAN, attempts=[], today=date(2026, 9, 26), **{**kw, "results": [{"date": "2026-09-26"}]})
    assert sat["test_day"] and sat["drill"] is None and sat["test_done"]
    sun = plan.today_plan(PLAN, attempts=[], today=date(2026, 9, 27), **kw)
    assert sun["off"] and sun["drill"] is None
    late = plan.today_plan(PLAN, attempts=[], today=date(2026, 11, 16), **kw)
    assert not late["on_track"] and late["slide"] == 1 and late["projected"] == "2027-03-10"
    assert plan.today_plan([], attempts=[], today=thu, **kw) is None


@pytest.fixture
def files(tmp_path, monkeypatch):
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS", "ATTEMPTS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    monkeypatch.setattr(plan, "PLAN", tmp_path / "plan.csv")
    monkeypatch.setattr(plan, "LOG", tmp_path / "plan-log.csv")
    return tmp_path


def test_words_tick_through_the_api(files):
    c = TestClient(main.app)
    assert c.get("/api/config").json()["plan"] is None                    # no plan file
    plan.write_plan(PLAN)
    p = c.get("/api/config").json()["plan"]
    assert p["gates"][0]["subband"] == "4k-a" and not p["words_done"]
    p = c.post("/api/plan/words").json()
    assert p["words_done"] and p["streak"] >= 1 if date.today().weekday() != 6 else p["words_done"]
    assert c.post("/api/plan/words").json()["words_done"]                 # idempotent: one row
    assert len(plan.load_log()) == 1 and plan.load_log()[0]["date"] == date.today().isoformat()
    assert c.get("/api/progress").json()["plan"]["words_done"]


def test_report_plan_section():
    p = plan.today_plan(PLAN, [t("2026-09-15", "4k-a")], SUBBANDS, [], [], [], today=date(2026, 11, 16))
    lines = "\n".join(progress.plan_lines(p))
    assert "Week **9** of 24" in lines and "late by 1 wk" in lines and "**2027-03-10** (target 2027-03-03)" in lines
    assert "| 4k-a | 3 | 2026-10-11 | passed 2026-09-15 |" in lines
    assert "| 4k-b | 8 | 2026-11-22 (was 2026-11-15) | late |" in lines
    assert "scripts/plan.py init" in progress.plan_lines(None)[0]
    r = progress.build([], [], SUBBANDS, today=date(2026, 11, 16), plan=p)
    md = progress.render_markdown(r, SUBBANDS)
    assert md.index("### Ability over time") < md.index("### Plan") < md.index("### Mock tests") and "late by 1 wk" in md


def test_cli_init_and_status(files, capsys):
    spec = importlib.util.spec_from_file_location("plan_cli", plan.VOCAB.parent / "scripts" / "plan.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main([]) == 1                                                # no plan yet
    assert cli.main(["init", "--start", "2026-09-22"]) == 1                # a Tuesday
    assert cli.main(["init", "--start", "2026-09-21"]) == 0 and plan.load_plan() == PLAN
    assert cli.main(["init", "--start", "2026-09-28"]) == 1                # exists, no --force
    assert cli.main(["init", "--start", "2026-09-28", "--force"]) == 0 and plan.start_of(plan.load_plan()) == date(2026, 9, 28)
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "projected" in out and "6k-a" in out
