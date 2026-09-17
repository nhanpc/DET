"""The learning schedule (issue #19): 20 minutes a day, six days a week, five sub-band gates to the target date.

vocab/plan.csv      the gates, hand-editable: subband, week, due (the Sunday that ends the gate week); written by
                    scripts/plan.py init, read here. Week 1 starts on the Monday `week` weeks before gate 1's due.
vocab/plan-log.csv  one row per *Words done* tick on the home page: date, words — the only part of a session the
                    app cannot see in a file already (drills → practice/attempts.csv, tests → vocab/tests/).

A session = WORDS_MIN of words, then DRILL_MIN of one drill: dictation on odd session days, cloze on even ones
(session days are the Mon–Fri days counted from the start, so a missed day does not change the alternation and
the odd week starts with dictation, the even one with cloze); Saturday's
drill is the level test; Sunday is off and never breaks the streak. A gate is passed on the first reliable level
test whose θ-level (irt.level_of, the sub-band the home page shows) reaches the gate's sub-band. A gate week that
ends unpassed slides that gate and every later one — and the projected test date — by whole weeks; a gate passed
early moves nothing. Everything here is a pure function over rows already loaded, like app/learn.py.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from math import ceil
from pathlib import Path
from typing import Iterable, Optional

from .bank import VOCAB, Subband

PLAN = VOCAB / "plan.csv"
LOG = VOCAB / "plan-log.csv"
PLAN_HEADER = ["subband", "week", "due"]
LOG_HEADER = ["date", "words"]

TARGET = date(2027, 3, 3)           # README § Goal
WEEKS = 24                          # the plan's length; week 24 = the final practice test and the booking check (#11)
FIRST = "4k-a"                      # gate 1; the gates run up the sub-bands from here
SPACING = (3, 5, 5, 5, 5)           # weeks per gate: 4k-a, 4k-b, 5k-a, 5k-b, 6k-a
WORDS_MIN = 10
DRILL_MIN = 10
DRILLS = (("listen-and-type",), ("read-and-complete", "fill-in-the-blanks"))   # odd session days, even session days
OFF = 6                             # date.weekday(): Sunday
TEST_DAY = 5                        # Saturday: the weekly level test replaces the drill
GRACE = 1                           # streak: today may still be open; the chain must reach yesterday


# ---- files

def make(start: date, subbands: list[Subband], first: str = FIRST, spacing: Iterable[int] = SPACING) -> list[dict]:
    """The gate rows for a plan whose week 1 begins on `start` (a Monday): gate k covers `spacing[k]` weeks and
    is due on the Sunday that ends its last week."""
    if start.weekday() != 0:
        raise ValueError(f"the plan starts on a Monday, {start} is a {start.strftime('%A')}")
    names = [sb.name for sb in subbands]
    if first not in names:
        raise ValueError(f"unknown sub-band {first}")
    spacing = list(spacing)
    rows, week = [], 0
    for sb, n in zip(names[names.index(first):], spacing):
        week += n
        rows.append({"subband": sb, "week": week, "due": (start + timedelta(weeks=week) - timedelta(days=1)).isoformat()})
    if len(rows) < len(spacing):
        raise ValueError(f"only {len(rows)} sub-bands from {first}, {len(spacing)} gates asked")
    return rows


def write_plan(rows: list[dict], path: Optional[Path] = None) -> Path:
    path = path or PLAN
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, PLAN_HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows({k: r[k] for k in PLAN_HEADER} for r in rows)
    return path


def load_plan(path: Optional[Path] = None) -> list[dict]:
    """Gate rows in file order with `week` as int; [] when there is no plan yet."""
    path = path or PLAN
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [{"subband": r["subband"], "week": int(r["week"]), "due": r["due"]} for r in rows]


def load_log(path: Optional[Path] = None) -> list[dict]:
    path = path or LOG
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def append_log(day: date, words: int = 1, path: Optional[Path] = None) -> bool:
    """Tick *Words done* for `day`; False (nothing written) when the day is already ticked."""
    path = path or LOG
    if any(r["date"] == day.isoformat() for r in load_log(path)):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(LOG_HEADER)
        w.writerow([day.isoformat(), words])
    return True


# ---- calendar

def start_of(plan: list[dict]) -> date:
    """The Monday of week 1: gate 1's due Sunday minus its weeks, plus a day."""
    g = plan[0]
    return date.fromisoformat(g["due"]) - timedelta(weeks=g["week"]) + timedelta(days=1)


def week_of(start: date, today: date) -> int:
    """1 in the first week; 0 and below before the start."""
    return (today - start).days // 7 + 1


def weekdays(a: date, b: date) -> int:
    """Mon–Fri days in [a, b]; 0 when b is before a."""
    if b < a:
        return 0
    full, rem = divmod((b - a).days + 1, 7)
    return full * 5 + sum(1 for i in range(rem) if (a.weekday() + i) % 7 < TEST_DAY)


def session_day(start: date, today: date) -> int:
    """The drill days (Mon–Fri) from `start` to `today` inclusive: 1 on the first Monday, 5 on Friday, still 5 at
    the weekend; 0 and below before the start, counted the same way backwards so the alternation holds."""
    if today < start:
        return -weekdays(today + timedelta(days=1), start - timedelta(days=1))
    return weekdays(start, today)


def drill_for(day: int) -> tuple[str, ...]:
    """The drill group of a session day: DRILLS[0] on odd days, DRILLS[1] on even ones."""
    return DRILLS[(day - 1) % 2]


def weeks_late(due: date, when: date) -> int:
    """Whole weeks from a due Sunday to a later date (0 when `when` is not after `due`)."""
    return max(0, ceil((when - due).days / 7))


def gates(plan: list[dict], thetas: list[dict], subbands: list[Subband], today: date) -> list[dict]:
    """Every gate with `status` passed / open / late, `passed_on` (the first reliable session whose level reaches
    the sub-band), `due` as planned, `due_now` after the slide — the gates before it and, for a late gate, its
    own — and `late` = the weeks this gate itself added to the slide."""
    order = {sb.name: sb.order for sb in subbands}
    tests = sorted((t for t in thetas if t["reliable"]), key=lambda t: t["date"])
    out, shift = [], 0
    for g in plan:
        due = date.fromisoformat(g["due"])
        due_now = due + timedelta(weeks=shift)
        passed = next((t["date"] for t in tests if t["level"] and order.get(t["level"], 0) >= order[g["subband"]]), None)
        late = weeks_late(due_now, date.fromisoformat(passed)) if passed else weeks_late(due_now, today)
        shift += late
        due_now += timedelta(weeks=late)                                      # the late gate's own new deadline
        out.append({**g, "due_now": due_now.isoformat(), "passed_on": passed, "late": late,
                    "status": "passed" if passed else "late" if late else "open"})
    return out


# ---- streak

def done_days(attempts: Iterable[dict], results: Iterable[dict], log: Iterable[dict]) -> set[date]:
    """Days with a drill attempt, a finished level-test block or a *Words done* tick."""
    return {date.fromisoformat(r["date"]) for rows in (attempts, results, log) for r in rows if r.get("date")}


def streak(days: set[date], today: date) -> int:
    """Consecutive session days done, counted back from today (or from yesterday while today is still open);
    Sundays are skipped, neither counted nor breaking the chain."""
    cur = today
    if cur.weekday() == OFF or cur not in days:
        cur -= timedelta(days=GRACE)
    n = 0
    while True:
        if cur.weekday() == OFF:
            cur -= timedelta(days=1)
            continue
        if cur not in days:
            return n
        n += 1
        cur -= timedelta(days=1)


# ---- today

def today_plan(plan: list[dict], thetas: list[dict], subbands: list[Subband], attempts: list[dict],
               results: list[dict], log: list[dict], today: Optional[date] = None) -> Optional[dict]:
    """What the home page's *Today* card shows; None without a plan file."""
    if not plan:
        return None
    today = today or date.today()
    start = start_of(plan)
    day = session_day(start, today)
    off, test = today.weekday() == OFF, today.weekday() == TEST_DAY
    group = () if off or test else drill_for(day)
    iso = today.isoformat()
    gs = gates(plan, thetas, subbands, today)
    slide = sum(g["late"] for g in gs)
    nxt = next((g for g in gs if g["status"] != "passed"), None)
    return {"start": start.isoformat(), "week": week_of(start, today), "weeks": WEEKS, "session_day": day,
            "off": off, "test_day": test, "drill": group[0] if group else None,
            "words_min": WORDS_MIN, "drill_min": DRILL_MIN,
            "words_done": any(r["date"] == iso for r in log),
            "drill_done": sum(1 for r in attempts if r["date"] == iso and r["task"] in group),
            "test_done": any(r["date"] == iso for r in results),
            "streak": streak(done_days(attempts, results, log), today),
            "gates": gs, "next": nxt, "slide": slide, "target": TARGET.isoformat(),
            "projected": (TARGET + timedelta(weeks=slide)).isoformat(), "on_track": slide == 0}
