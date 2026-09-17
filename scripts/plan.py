#!/usr/bin/env python3
"""The learning schedule (issue #19): write vocab/plan.csv, or print where the plan stands.

    python3 scripts/plan.py init --start 2026-09-21        # five gates from 4k-a, 3,5,5,5,5 weeks → vocab/plan.csv
    python3 scripts/plan.py init --start ... --first 4k-b --spacing 5,5,5,5
    python3 scripts/plan.py                                # the gate table, the slide and the projected date

`init` refuses to overwrite an existing plan unless --force is given; edit the file by hand for a one-off change.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import learn, plan, store              # noqa: E402  (after the sys.path line)
from app.bank import Bank, load_subbands         # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    init = sub.add_parser("init", help="write vocab/plan.csv")
    init.add_argument("--start", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD", help="the Monday of week 1")
    init.add_argument("--first", default=plan.FIRST, help=f"gate 1's sub-band (default {plan.FIRST})")
    init.add_argument("--spacing", default=",".join(map(str, plan.SPACING)), help=f"weeks per gate (default {','.join(map(str, plan.SPACING))})")
    init.add_argument("--force", action="store_true", help="overwrite an existing plan")
    a = ap.parse_args(argv)

    subbands = load_subbands()
    rel = plan.PLAN.relative_to(ROOT) if plan.PLAN.is_relative_to(ROOT) else plan.PLAN
    if a.cmd == "init":
        if plan.PLAN.exists() and not a.force:
            print(f"{rel} exists — edit it, or pass --force", file=sys.stderr)
            return 1
        try:
            rows = plan.make(a.start, subbands, a.first, [int(x) for x in a.spacing.split(",")])
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        plan.write_plan(rows)
        print(f"{rel}: week 1 from {a.start}, " + ", ".join(f"{r['subband']} by {r['due']}" for r in rows))
        return 0

    rows = plan.load_plan()
    if not rows:
        print("no plan yet: python3 scripts/plan.py init --start <Monday>", file=sys.stderr)
        return 1
    sessions, attempts = store.load_sessions(), store.load_attempts()
    thetas = learn.theta_history(sessions, subbands, Bank().b)
    p = plan.today_plan(rows, thetas, subbands, attempts, store.load_results(), plan.load_log())
    print(f"week {p['week']} of {p['weeks']} · streak {p['streak']} · "
          + ("on track" if p["on_track"] else f"late by {p['slide']} wk") + f" · projected {p['projected']}")
    for g in p["gates"]:
        when = f"passed {g['passed_on']}" if g["passed_on"] else g["status"]
        slid = f" (was {g['due']})" if g["due_now"] != g["due"] else ""
        print(f"  {g['subband']:5} wk {g['week']:2}  by {g['due_now']}{slid}  {when}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
