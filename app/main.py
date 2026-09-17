"""FastAPI backend. Run from the repo root:  uvicorn app.main:app --reload  →  http://localhost:8000"""
from __future__ import annotations

import random
import re
import uuid
from dataclasses import asdict
from datetime import date, datetime
from hashlib import sha1
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import drills, irt, learn, plan, progress, store, textdiff, tts
from .adaptive import MAX_BLOCKS, PSEUDO_PER_BLOCK, REAL_PER_BLOCK, WINDOW, Session
from .bank import VOCAB, Bank, load_subbands, read_csv

STATIC = Path(__file__).parent / "static"
ATTEMPT_ID = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{4}$")   # drills.attempt_id(): also a file name, so checked
PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
PHOTO_PROMPT = "Describe the photo in as much detail as you can."

app = FastAPI(title="DET vocabulary level test")
SUBBANDS = load_subbands()
BANK = Bank()
SYNONYMS = learn.load_synonyms()
FORMS = drills.form_index(BANK.index)                     # any word form → family, for drill errors and lacked words
SENSES = read_csv(VOCAB / "senses.csv")
EXAMPLES = {(r["family"], r["sense"]): r["example"] for r in SENSES}   # cloze id → the sentence it was cut from
LEX = textdiff.Lexicon(BANK.index, BANK.b)                # text difficulty b_text (issue #15) for sentences and passages
_CLOZE_POOL: list[dict] = []                              # sentence_candidates() that yield a cloze item; built on first use
# Unfinished sessions come back from disk so a closed tab or a restart does not lose a test.
_SAVED = store.load_sessions()
SESSIONS: dict[str, Session] = {d["id"]: Session.restore(d, SUBBANDS, BANK, recent=store.recent_words(_SAVED))
                                for d in _SAVED if not d["finished"]}
del _SAVED


class Answer(BaseModel):
    yes: bool
    ms: Optional[int] = None


class MyWord(BaseModel):
    family: str
    source: str = "learn"          # test (pinned on the result page), learn (the Add-a-word box) or a drill's task id
    note: str = ""


class DrillAnswer(BaseModel):
    """POST /api/drill/<task>/<id>: what the drill screen sends when the clock stops or the answer is in."""
    attempt: str
    ms: int = 0
    timed_out: bool = False
    typed: list[str] | str = ""    # cloze: one string per blank; dictation: the sentence
    plays: int = 0                 # dictation, Listen Then Speak: how often the audio was played
    text: str = ""                 # writing: part 1
    text2: str = ""                # Interactive Writing: part 2
    rating: list[int] = []         # speaking, writing: the four 1–5 lines (drills.RATING_LINES)
    lacked: str = ""               # speaking, writing: "words I lacked", comma-separated


class Draft(BaseModel):
    attempt: str
    text: str = ""
    text2: str = ""
    seconds: int = 0


def get(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if s is None:
        raise HTTPException(404, "unknown session")
    return s


def block_view(s: Session) -> dict:
    b = s.block
    return {"no": b.no, "size": len(b.items), "pos": b.pos, "words": [i.word for i in b.items]}


def result_view(s: Session) -> dict:
    r = s.result()
    d = asdict(r)
    d["misses"] = [{"word": i.word, "definition": i.definition, "b": i.b} for i in r.misses]
    d["date"] = s.started.strftime("%-d %b %Y")
    d["seconds"] = int((datetime.now() - s.started).total_seconds())
    d["stop_reason"] = s.stop_reason
    return d


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def current_state() -> dict:
    """The learner now, from the history on disk: `sessions`, `attempts`, the θ history (`thetas`), `theta_test`
    of the last reliable session, `theta`/`se` = that posterior updated by the scored drill attempts since it
    (learn.drill_theta, #16; None before a reliable test), `theta_listen`/`se_listen` from the dictation attempts
    only, `level`, `frontier`, `det_estimate`, `det_range`. The start page, the Learn page and the drills need
    the same numbers, and the files are loaded once per request."""
    sessions, attempts = store.load_sessions(), store.load_attempts()
    thetas = learn.theta_history(sessions, SUBBANDS, BANK.b)
    test = learn.current_theta(thetas)
    now = learn.drill_theta(thetas, attempts)
    listen = learn.drill_theta(thetas, attempts, ("listen-and-type",))
    theta, se = now if now else (None, None)
    level, front = learn.frontier(theta, SUBBANDS)
    return {"sessions": sessions, "attempts": attempts, "thetas": thetas, "theta": theta, "se": se,
            "theta_test": test[0] if test else None, "theta_listen": listen[0] if listen else None,
            "se_listen": listen[1] if listen else None, "level": level, "frontier": front,
            "det_estimate": irt.det_estimate(theta, SUBBANDS) if now else None,
            "det_range": list(irt.det_range(theta, se, SUBBANDS)) if now else None}


@app.get("/api/config")
def config():
    last = store.load_levels()
    open_ = [s for s in SESSIONS.values() if not s.finished]
    resume = max(open_, key=lambda s: s.started) if open_ else None
    st = current_state()
    return {"real_per_block": REAL_PER_BLOCK, "pseudo_per_block": PSEUDO_PER_BLOCK, "max_blocks": MAX_BLOCKS,
            "window": WINDOW, "se_stop": irt.SE_STOP,
            "subbands": [asdict(b) for b in SUBBANDS], "last": last[-1] if last else None,
            "retest": learn.retest_due(last, st["frontier"]), "frontier": st["frontier"], "level": st["level"],
            "theta": st["theta"], "se": st["se"], "det_estimate": st["det_estimate"], "det_range": st["det_range"],
            "theta_test": st["theta_test"], "theta_listen": st["theta_listen"], "se_listen": st["se_listen"],
            "resume": {"session": resume.id, "block": block_view(resume)} if resume else None,
            "tasks": drills.TASKS, "today": drills.today_counts(st["attempts"]), "pool": learn.pool_counts(priority(st)[2]),
            "plan": plan_view(st)}


def plan_view(st: dict) -> Optional[dict]:
    """The *Today* card (issue #19): plan.today_plan over vocab/plan.csv, the θ history, the drill attempts, the
    finished test blocks and the *Words done* ticks; None without a plan file."""
    return plan.today_plan(plan.load_plan(), st["thetas"], SUBBANDS, st["attempts"], store.load_results(), plan.load_log())


@app.post("/api/plan/words")
def plan_words():
    """Tick *Words done* for today (idempotent) and return the plan as /api/config would."""
    plan.append_log(date.today())
    return plan_view(current_state())


@app.post("/api/session")
def start(start: Optional[str] = None):
    """A new test. The prior θ₀ is the θ of the last reliable session (the Re-test button and the Start button
    alike — the CAT's "start at the frontier"), 6.0 before the first one; `start` = a sub-band name puts θ₀ in
    the middle of that sub-band instead. Words shown in the last RECENT_DAYS days are not drawn."""
    if start is not None and start not in {b.name for b in SUBBANDS}:
        raise HTTPException(400, f"unknown sub-band {start!r}")
    st = current_state()
    sid = datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    s = Session.create(sid, SUBBANDS, BANK, start_band=start, theta0=st["theta"],
                       recent=store.recent_words(st["sessions"]))
    SESSIONS[sid] = s
    return {"session": sid, "block": block_view(s), "max_blocks": MAX_BLOCKS, "theta0": s.theta0}


@app.post("/api/session/{sid}/answer")
def answer(sid: str, a: Answer):
    s = get(sid)
    try:
        status = s.answer(a.yes, a.ms)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if status != "next":
        store.save_block(s, s.block)
    if status == "finished":
        store.save_result(s, s.result())
    store.write_session(s)
    return {"status": status, "block": block_view(s)}


@app.post("/api/session/{sid}/next")
def next_block(sid: str):
    s = get(sid)
    try:
        s.next_block()
    except ValueError as e:
        raise HTTPException(409, str(e))
    store.write_session(s)
    return {"block": block_view(s)}


@app.get("/api/session/{sid}/result")
def result(sid: str):
    s = get(sid)
    if not s.finished:
        raise HTTPException(409, "session not finished")
    return result_view(s)


def learn_view(n: int) -> dict:
    """The Learn page: the progress report (level, frontier, pooled sub-bands, counts, chart series — the same
    dict GET /api/progress and scripts/report.py use) plus the history rows and the study list."""
    sessions, attempts = store.load_sessions(), store.load_attempts()
    levels = store.load_levels()
    rows = learn.load_my_words()
    r = progress.build(sessions, levels, SUBBANDS, b_of=BANK.b, attempts=attempts, index=BANK.index, my_words=rows)
    stats = learn.word_stats(sessions, SUBBANDS, attempts, BANK.index)
    history, _ = learn.level_history(levels)
    my_words = learn.open_my_words(rows)
    done = learn.done_counts(rows)
    counts = {**r["counts"], "my-words": len(my_words), "done-cards": done["cards"], "done-practice": done["practice"]}
    return {"sessions": r["tests"], "history": history, "trend": r["trend"], "level": r["level"],
            "frontier": r["frontier"], "theta": r["theta"], "se": r["se"], "det_estimate": r["det_estimate"],
            "det_range": r["det_range"], "theta_test": r["theta_test"], "theta_listen": r["theta_listen"],
            "se_listen": r["se_listen"], "thetas": r["thetas"], "theta_series": r["theta_series"],
            "subbands": r["subbands"], "counts": counts, "series": r["series"], "batch": n, "pool": r["pool"],
            "words": learn.study_list(stats, r["frontier"], BANK.index, SYNONYMS, my_words, n, BANK.examples)}


@app.get("/api/progress")
def progress_page():
    """The report dict scripts/report.py renders into vocab/progress.md (no Anki), mock tests included;
    a malformed mocks.csv row is a 422 naming the line (#11), the same message the script prints."""
    try:
        st = current_state()
        return progress.build(st["sessions"], store.load_levels(), SUBBANDS, mocks=store.load_mocks(), b_of=BANK.b,
                              attempts=st["attempts"], index=BANK.index, my_words=learn.load_my_words(), plan=plan_view(st))
    except ValueError as e:
        raise HTTPException(422, f"vocab/tests/mocks.csv: {e}")


@app.get("/api/learn")
def learn_page(n: int = learn.BATCH):
    return learn_view(max(1, min(n, 100)))


@app.post("/api/learn/export")
def learn_export(n: int = learn.BATCH):
    words = learn.card_entries(learn_view(max(1, min(n, 100)))["words"])
    today = date.today()
    path = learn.export_anki(today.isoformat(), words)
    done = learn.mark_done([w["family"] for w in words if w["reason"] == "my-words"], today)
    root = store.VOCAB.parent
    return {"file": str(path.relative_to(root)) if path.is_relative_to(root) else str(path), "cards": len(words),
            "my_words_done": done}


@app.get("/api/my-words")
def my_words():
    rows = learn.load_my_words()
    return {"open": sum(1 for r in rows if not r["done"]), "words": rows[::-1]}


@app.post("/api/my-words")
def add_my_word(w: MyWord):
    """Pin a word met in practice. 409 when the family already has an open entry."""
    family = w.family.strip().lower()
    if not family:
        raise HTTPException(422, "family is empty")
    if not learn.add_my_word(family, w.source, w.note):
        raise HTTPException(409, f"{family} is already on the list")
    return {"family": family, "source": w.source, "note": w.note, "in_index": family in BANK.index}


# ---- Phase 5: the task drills (issue #10) -----------------------------------------------------------------
# One flow for every task: GET /api/drill/<task>/next hands out an item and a fresh attempt id; the screen runs
# the clock; POST /api/drill/<task>/<id> scores it (cloze, dictation) or takes the self-rating (speaking,
# writing), appends one practice/attempts.csv row and sends wrong or lacked words to vocab/my-words.csv.

def task_of(task: str) -> dict:
    t = drills.TASKS.get(task)
    if t is None:
        raise HTTPException(404, f"unknown task {task!r}")
    return t


def attempt_of(attempt: str) -> str:
    if not ATTEMPT_ID.match(attempt):
        raise HTTPException(422, f"bad attempt id {attempt!r}")
    return attempt


def cloze_pool() -> list[dict]:
    """Sentence-mode candidates from senses.csv whose target is eligible and yield 2–5 blanks (None-ness does not
    depend on the seed), with the family's forms pattern and b_text; every sub-band, built once and cached in
    practice/read-and-complete/cloze.csv (gitignored, rebuilt like sentences.csv when missing or outdated)."""
    if not _CLOZE_POOL:
        rows = textdiff.load_cloze()
        if not rows:
            for c in drills.sentence_candidates(SENSES, BANK.index):
                pat = drills.forms_pattern(c["family"], drills.members(BANK.index[c["family"]]))
                if drills.cloze(c["example"], 0, pat) is not None:
                    rows.append({**c, "key": f"{c['family']}.{c['sense']}"})
            textdiff.write_cloze(textdiff.score_rows(rows, LEX, "example"))
        for r in rows:
            r["pattern"] = drills.forms_pattern(r["family"], drills.members(BANK.index[r["family"]]))
        _CLOZE_POOL.extend(rows)
    return _CLOZE_POOL


def sentence_bank() -> list[dict]:
    """practice/listen-and-type/sentences.csv, written from senses.csv on first use (gitignored); a file with the
    header from before #15 (no b_text) is rebuilt the same way."""
    return textdiff.sentence_bank(SENSES, BANK.index, LEX)


def sentence_row(item: str) -> dict:
    row = next((r for r in sentence_bank() if r["id"] == item), None)
    if row is None:
        raise HTTPException(404, f"unknown sentence {item!r}")
    return row


def prompt_rows(task: str) -> list[dict]:
    """prompts.csv rows of one task. Photo tasks also take every image in practice/speaking/photos/ that no row
    names (id = file stem, the default instruction), so dropping photos into the folder is enough; a row whose
    photo file is missing is left out."""
    path = drills.SPEAKING_PROMPTS if task in drills.SPEAKING else drills.WRITING_PROMPTS
    rows = [r for r in drills.load_prompts(path) if r["task"] == task]
    if task in ("speak-photo", "write-photo"):
        named = {r["photo"] for r in rows}
        rows = [r for r in rows if (drills.PHOTOS / r["photo"]).is_file()]
        rows += [{"id": p.stem, "task": task, "prompt": PHOTO_PROMPT, "photo": p.name, "follow_up": "", "source": "own photo"}
                 for p in sorted(drills.PHOTOS.glob("*")) if p.suffix.lower() in PHOTO_TYPES and p.name not in named] \
            if drills.PHOTOS.exists() else []
    return rows


def prompt_row(task: str, item: str) -> dict:
    row = next((r for r in prompt_rows(task) if r["id"] == item), None)
    if row is None:
        raise HTTPException(404, f"unknown prompt {item!r}")
    return row


def prompt_voice(item: str) -> str:
    """Listen Then Speak: one of the four accents, fixed per prompt id so the cached MP3 is reused."""
    return drills.VOICES[int(sha1(item.encode()).hexdigest(), 16) % len(drills.VOICES)]


def speech(text: str, voice: str) -> Path:
    try:
        return tts.audio(text, voice)
    except Exception as e:                                                  # edge-tts needs the internet once per sentence
        raise HTTPException(503, f"could not synthesise the audio ({e.__class__.__name__}: {e}); the MP3 is cached once it works")


def prompt_view(task: str, row: dict, attempt: str) -> dict:
    t = drills.TASKS[task]
    photo = row.get("photo") and (drills.PHOTOS / row["photo"]).is_file()
    d = {"task": task, "attempt": attempt, "id": row["id"], "subband": "", "prompt": row["prompt"],
         "follow_up": row.get("follow_up", ""), "source": row.get("source", ""),
         "photo_url": f"/api/drill/{task}/{row['id']}/photo" if photo else None,
         "prep": t["prep"], "seconds": t["seconds"], "seconds2": t.get("seconds2", 0), "min": t["min"],
         "plays": t.get("plays", 0), "audio_url": None}
    if task == "listen-then-speak":
        speech(row["prompt"], prompt_voice(row["id"]))
        d["audio_url"] = f"/api/drill/{task}/{row['id']}/audio"
    return d


def add_words(words: list[str], task: str, note: str, map_only: bool) -> list[dict]:
    """Wrong or lacked words → vocab/my-words.csv through learn.add_my_word(), once per family. `map_only`: a
    word outside index.csv is logged in attempts.csv only (cloze, dictation); otherwise it is added under its
    own spelling and becomes an *extra* entry (the "words I lacked" box)."""
    out, seen = [], set()
    for w in words:
        family = drills.family_of(w, FORMS)
        if family is None and map_only:
            continue
        family = family or w
        if family in seen:
            continue
        seen.add(family)
        out.append({"word": w, "family": family, "in_index": family in BANK.index,
                    "added": learn.add_my_word(family, task, note)})
    return out


def add_event_words(events: list[dict], task: str, note: str) -> list[dict]:
    """The wrong words of a drill's events (#16) → my-words, once per family: a `vocabulary` miss under
    `source = <task>`, a form / hearing / spelling slip under `<task>:<kind>` (learn.skill_source: shown as *heard
    wrong*, never exported). Hits add nothing. An event's own `note` (the sentence of a passage blank, #17)
    beats `note`."""
    out, seen = [], set()
    for e in events:
        if e["kind"] == "hit" or e["family"] in seen:
            continue
        seen.add(e["family"])
        source = task if e["kind"] == "vocabulary" else f"{task}:{e['kind']}"
        out.append({"word": e["word"], "family": e["family"], "in_index": True, "kind": e["kind"],
                    "added": learn.add_my_word(e["family"], source, e.get("note") or note)})
    return out


def theta_for_selection(st: dict) -> float:
    """The θ the passage window is built around: the learner's θ, or the middle of the frontier sub-band before
    the first reliable test (the sub-band's order − 0.5 on the b scale)."""
    if st["theta"] is not None:
        return st["theta"]
    sb = next(b for b in SUBBANDS if b.name == st["frontier"])
    return sb.order - 0.5


def passage_pick(passages: list[dict], st: dict, pool_map: dict[str, tuple[str, int]], recent: set[str], rng: random.Random) -> tuple[Optional[dict], Optional[str], Optional[float]]:
    """Passage mode selection (#17): the scored passages with |b − θ| ≤ DRILL_WINDOW, widened until ten
    (drills.in_window), drawn by drills.pick_weighted with the weight of the heaviest priority family the
    passage can damage (drills.passage_targets; a passage without one is a frontier item) and the 30-day
    no-repeat set. Returns (passage, the family to damage or None, the window width)."""
    scored = [p for p in passages if p["b_text"] is not None]
    if not scored:
        return None, None, None
    window, width = drills.in_window(scored, theta_for_selection(st), textdiff.b_of)
    targets = {p["slug"]: drills.passage_targets(p["text"], pool_map, LEX) for p in window}

    def weight(p):
        return max([pool_map[f][1] for f, _ in targets[p["slug"]]] + [drills.FRONTIER_WEIGHT])

    p = drills.pick_weighted(window, recent, lambda p: p["slug"], weight, rng)
    if p is None:
        return None, None, width
    best = [f for f, _ in targets[p["slug"]] if pool_map[f][1] == weight(p) and pool_map[f][1] > drills.FRONTIER_WEIGHT]
    return p, (rng.choice(best) if best else None), width


def priority(st: dict) -> tuple[dict, list[dict], dict[str, tuple[str, int]]]:
    """(word stats, open my-words rows, learn.priority_pool()) for the state of current_state() — what the
    vocabulary drills draw by (issue #13)."""
    stats = learn.word_stats(st["sessions"], SUBBANDS, st["attempts"], BANK.index)
    my_words = learn.open_my_words(learn.load_my_words())
    return stats, my_words, learn.priority_pool(stats, my_words, BANK.index, st["frontier"])


def target_view(family: str, stats: dict, my_words: list[dict], pool: dict[str, tuple[str, int]]) -> dict:
    """What the item screen and the result screen say about the target family: `family`, `forms` (to highlight
    it in the sentence), `reason` (the pool tier, "" for a frontier or off-pool word) and its `reason_label`."""
    reason = pool.get(family, ("", 0))[0]
    return {"family": family, "forms": [family, *drills.members(BANK.index[family])] if family in BANK.index else [family],
            "reason": reason if reason in learn.PRIORITY else "", "reason_label": learn.reason_label(family, reason, stats, my_words)}


def forms_of(family: str) -> re.Pattern:
    """drills.forms_pattern() of a family of the bank: its headword and members."""
    return drills.forms_pattern(family, drills.members(BANK.index[family]))


def weight_of(pool: dict[str, tuple[str, int]], fallback: int = 0):
    """The pick_weighted() weight of a bank row or cloze candidate: its family's pool weight, `fallback` outside it."""
    return lambda r: pool.get(r["family"], ("", fallback))[1]


@app.get("/api/drill/{task}/next")
def drill_next(task: str, mode: str = "passage"):
    """A fresh item for `task` and the attempt id to answer it with. The vocabulary drills draw by
    learn.priority_pool() (#13: the words missed in the test or in practice first, the frontier last, through
    drills.pick_weighted) — sentence-mode cloze from the sentence candidates of those families, dictation, Read
    Aloud and Fill in the Blanks from the sentences near θ (#16, #17), passage mode from the passages near θ
    (#17, passage_pick) — and skip items shown in the last NO_REPEAT_DAYS days (PASSAGE_REPEAT_DAYS for a
    passage); prompts rotate the same way. Read and Complete: `mode=passage` (the default, the DET's C-test) or
    `mode=sentence` (the 1-minute form)."""
    t = task_of(task)
    attempt = drills.attempt_id()
    recent = drills.recent_items(store.load_attempts(), task)
    rng = random.Random()
    if task in drills.SPEAKING[1:] or task in drills.WRITING:
        row = drills.pick(prompt_rows(task), recent, lambda r: r["id"], rng)
        if row is None:
            where = "practice/speaking/photos/" if task in ("speak-photo", "write-photo") else \
                ("practice/speaking/prompts.csv" if task in drills.SPEAKING else "practice/writing/prompts.csv")
            raise HTTPException(404, f"no prompt for {task}: add one to {where}")
        return prompt_view(task, row, attempt)
    st = current_state()
    front = st["frontier"]
    stats, my_words, pool_map = priority(st)
    if task == "read-and-complete" and mode != "sentence":
        recent = drills.recent_items(store.load_attempts(), task, days=drills.PASSAGE_REPEAT_DAYS)
        p, family, width = passage_pick(drills.load_passages(), st, pool_map, recent, rng)
        if p is None:
            raise HTTPException(404, "no scored passage: run `scripts/passages.py fetch` and `score`")
        seed = drills.passage_seed(p["text"], forms_of(family), rng) if family else None
        if seed is None:
            family, seed = None, rng.randrange(10_000)
        item = drills.cloze(p["text"], seed, passage=True)
        if item is None:
            raise HTTPException(404, f"passage {p['slug']} yields fewer than {drills.MIN_BLANKS} blanks")
        target = target_view(family, stats, my_words, pool_map) if family else {"family": "", "forms": [], "reason": "", "reason_label": ""}
        return {"task": task, "attempt": attempt, "id": drills.passage_id(p["slug"], seed), "mode": "passage",
                "subband": "", "source": p.get("source", ""), "pieces": item["pieces"], "blanks": item["blanks"],
                "seconds": t["passage_seconds"], "b": textdiff.b_of(p), "theta": st["theta"], "window": width, **target}
    if task == "read-and-complete":
        # sentence mode: the cloze candidates of the pool's families, weighted; the whole bank when none
        candidates = [c for c in cloze_pool() if c["family"] in pool_map] or cloze_pool()
        c = drills.pick_weighted(candidates, recent, lambda c: c["key"], weight_of(pool_map, 1), rng)
        if c is None:
            raise HTTPException(404, f"no example sentence for {front}")
        seed = rng.randrange(10_000)
        item = drills.cloze(c["example"], seed, c["pattern"])
        return {"task": task, "attempt": attempt, "id": drills.cloze_id(c["family"], c["sense"], seed), "mode": "sentence",
                "subband": c["subband"], "pieces": item["pieces"], "blanks": item["blanks"], "seconds": t["seconds"],
                "b": textdiff.b_of(c), **target_view(c["family"], stats, my_words, pool_map)}
    # listen-and-type, read-aloud and fill-in-the-blanks: the dictation bank near θ (|b − θ| ≤ 0.6, widened until
    # 10 candidates, issue #16), the frontier sub-band before the first reliable test; inside the window the pool
    # weights (#13) — a sentence outside the pool counts as a frontier one, so the window stays wide
    bank = sentence_bank()
    if task == "fill-in-the-blanks":
        bank = [r for r in bank if drills.fill_blank(r["sentence"], forms_of(r["family"])) is not None]
    if st["theta"] is not None:
        pool, width = drills.in_window(bank, st["theta"], textdiff.b_of)
    else:
        pool, width = [r for r in bank if r["family"] in pool_map or r["subband"] == front] or bank, None
    row = drills.pick_weighted(pool, recent, lambda r: r["id"], weight_of(pool_map, drills.FRONTIER_WEIGHT), rng)
    if row is None:
        raise HTTPException(404, "the sentence bank is empty")
    d = {"task": task, "attempt": attempt, "id": row["id"], "subband": row["subband"], "seconds": t["seconds"], "prep": 0,
         "b": textdiff.b_of(row), "theta": st["theta"], "window": width, **target_view(row["family"], stats, my_words, pool_map)}
    if task == "read-aloud":
        return {**d, "sentence": row["sentence"], "min": 0}
    if task == "fill-in-the-blanks":
        item = drills.fill_blank(row["sentence"], forms_of(row["family"]))
        return {**d, "id": drills.fill_id(row["family"], row["id"].split(".")[1]), "pieces": item["pieces"], "keep": item["keep"],
                "words": drills.word_count(row["sentence"])}
    speech(row["sentence"], row["voice"])
    return {**d, "audio_url": f"/api/drill/{task}/{row['id']}/audio", "plays_left": t["plays"], "words": drills.word_count(row["sentence"])}


@app.get("/api/drill/{task}/{item}/audio")
def drill_audio(task: str, item: str):
    """The cached edge-tts MP3 of a dictation sentence or a Listen Then Speak prompt."""
    task_of(task)
    if task == "listen-then-speak":
        row = prompt_row(task, item)
        path = speech(row["prompt"], prompt_voice(item))
    elif task in ("listen-and-type", "read-aloud"):
        row = sentence_row(item)
        path = speech(row["sentence"], row["voice"])
    else:
        raise HTTPException(404, f"{task} has no audio")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/drill/{task}/{item}/photo")
def drill_photo(task: str, item: str):
    row = prompt_row(task, item)
    path = drills.PHOTOS / row.get("photo", "")
    if not row.get("photo") or not path.is_file():
        raise HTTPException(404, "no photo for this prompt")
    return FileResponse(path, media_type=PHOTO_TYPES.get(path.suffix.lower(), "application/octet-stream"))


@app.post("/api/drill/{task}/{item}/audio")
def drill_upload(task: str, item: str, attempt: str, audio: bytes = Body(..., media_type="audio/webm")):
    """The MediaRecorder blob → practice/speaking/recordings/<attempt>.webm, before the self-rating is sent."""
    if task not in drills.SPEAKING:
        raise HTTPException(404, f"{task} is not a speaking task")
    name = f"{attempt_of(attempt)}.webm"
    drills.RECORDINGS.mkdir(parents=True, exist_ok=True)
    (drills.RECORDINGS / name).write_bytes(audio)
    return {"file": f"{drills.REL_RECORDINGS}/{name}", "bytes": len(audio)}


@app.get("/api/drill/{task}/{attempt}/recording")
def drill_recording(task: str, attempt: str):
    path = drills.RECORDINGS / f"{attempt_of(attempt)}.webm"
    if not path.is_file():
        raise HTTPException(404, "no recording for this attempt")
    return FileResponse(path, media_type="audio/webm")


def write_draft(task: str, item: str, attempt: str, text: str, text2: str, seconds: int) -> tuple[str, int]:
    """practice/writing/drafts/<attempt>.md rewritten in full; returns (the `file` column, words written)."""
    row = prompt_row(task, item)
    parts = [(row["prompt"], text)]
    if task == "interactive-writing":
        parts.append((row.get("follow_up") or "Part 2", text2))
    n = drills.word_count(text) + (drills.word_count(text2) if task == "interactive-writing" else 0)
    drills.DRAFTS.mkdir(parents=True, exist_ok=True)
    name = f"{attempt}.md"
    (drills.DRAFTS / name).write_text(drills.draft_text(task, item, seconds, n, parts), encoding="utf-8")
    return f"{drills.REL_DRAFTS}/{name}", n


@app.post("/api/drill/{task}/{item}/draft")
def drill_draft(task: str, item: str, d: Draft):
    """Autosave (every 10 s and at time-out): the whole draft rewritten under the attempt's own file."""
    if task not in drills.WRITING:
        raise HTTPException(404, f"{task} is not a writing task")
    file, n = write_draft(task, item, attempt_of(d.attempt), d.text, d.text2, d.seconds)
    return {"file": file, "words": n}


def dictation_answer(item: str, a: DrillAnswer, row: dict) -> dict:
    """Listen and Type (issue #16): the credit (character-level edit distance) is the attempt's score and, through
    irt.snap(), the response that moves θ — `theta` in the row is θ before the attempt, `b` the sentence's
    difficulty; `events` says what each content word told us (drills.dictation_events) and feeds word_stats and
    my-words (add_event_words). After the row is written the bank's b_adjust is refit from every attempt
    (textdiff.refit_bank, #15) and the bank rewritten when something changed."""
    st = current_state()
    bank = sentence_bank()
    s = next((r for r in bank if r["id"] == item), None)
    if s is None:
        raise HTTPException(404, f"unknown sentence {item!r}")
    theta, b = st["theta"], textdiff.b_of(s)
    r = drills.dictation_score(s["sentence"], a.typed if isinstance(a.typed, str) else " ".join(a.typed))
    events = drills.dictation_events(r["diff"], theta if theta is not None else irt.THETA0, LEX)
    added = add_event_words(events, "listen-and-type", s["sentence"])
    row.update(subband=s["subband"], score=r["score"], words=r["words"], errors=drills.errors_column(r["errors"]),
               theta=theta, b=b, events=drills.events_column(events))
    store.append_attempt(row)
    attempts = store.load_attempts()
    after = learn.drill_theta(st["thetas"], attempts)                  # θ now, the row just written included
    if textdiff.refit_bank(bank, attempts, "listen-and-type"):
        drills.write_sentences(bank)
    return {"attempt": row["attempt"], "score": r["score"], "credit": r["score"], "word_score": r["word_score"],
            "words": r["words"], "reference": s["sentence"], "diff": r["diff"], "errors": r["errors"], "events": events,
            "added": added, "plays": a.plays, "timed_out": a.timed_out, "b": b, **target_view(s["family"], {}, [], {}),
            "theta": theta, "se": st["se"], "theta_after": after[0] if after else None, "se_after": after[1] if after else None,
            "done": close_rows()}


def scale_after(st: dict, row: dict, b: float, score: float, events: list[dict]) -> dict:
    """What every drill on the scale does once its row is scored (#16, #17): `theta` (before), `b` and `events`
    into the row, the row appended, θ after it — the fields the result screen shows."""
    row.update(score=score, theta=st["theta"], b=b, events=drills.events_column(events))
    store.append_attempt(row)
    after = learn.drill_theta(st["thetas"], store.load_attempts())
    return {"b": b, "theta": st["theta"], "se": st["se"], "theta_after": after[0] if after else None,
            "se_after": after[1] if after else None}


def cloze_answer(item: str, a: DrillAnswer, row: dict) -> dict:
    """Read and Complete (#17 on the scale): the item is regenerated from its id; `credit = correct / blanks` is
    the score and, through irt.snap(), the response that moves θ against the sentence's or passage's `b`; every
    content-word blank is an event (drills.cloze_events: hit / spelling / vocabulary) that feeds word_stats and
    my-words (a passage blank's note is its sentence). After the row the item's `b_adjust` is refit from every
    attempt (textdiff.refit_bank) — the cloze cache or the passage's front matter rewritten when it moved."""
    st = current_state()
    family, sense, seed = drills.parse_cloze_id(item, BANK.index)
    passages: list[dict] = []
    if sense is not None:
        ex = EXAMPLES.get((family, str(sense)))
        if not ex:
            raise HTTPException(404, f"unknown sense {family}.{sense}")
        gen = drills.cloze(ex, seed, forms_of(family))
        row["subband"] = BANK.index[family]["subband"]
        source = next((c for c in cloze_pool() if c["key"] == f"{family}.{sense}"), None)
        b = textdiff.b_of(source) if source else textdiff.b_text(ex, LEX)
    else:
        passages = drills.load_passages()
        p = next((p for p in passages if p["slug"] == family), None)
        if p is None:
            raise HTTPException(404, f"unknown passage {family!r}")
        ex = p["text"]
        gen = drills.cloze(ex, seed, passage=True)
        b = textdiff.b_of(p) if p["b_text"] is not None else textdiff.b_text(ex, LEX)
    if gen is None:
        raise HTTPException(409, f"{item} yields no item")
    typed = a.typed if isinstance(a.typed, list) else [a.typed]
    score, wrong = drills.score_cloze(gen["answers"], typed)
    events = drills.cloze_events(gen["answers"], typed, LEX)
    if sense is None:
        for e in events:
            e["note"] = drills.sentence_of(ex, e["word"])
    added = add_event_words(events, "read-and-complete", ex)
    row.update(words=gen["blanks"], errors=drills.errors_column(wrong))
    scale = scale_after(st, row, b, score, events)
    attempts = store.load_attempts()
    if sense is not None:
        if textdiff.refit_bank(cloze_pool(), attempts, "read-and-complete", textdiff.strip_seed, "key"):
            textdiff.write_cloze(cloze_pool())
    elif textdiff.refit_bank(passages, attempts, "read-and-complete", textdiff.strip_seed, "slug"):
        for q in passages:
            textdiff.set_passage_adjust(drills.PASSAGES / f"{q['slug']}.md", q["b_adjust"])
    target = target_view(family, {}, [], {}) if sense is not None else {"family": "", "forms": [], "reason": "", "reason_label": ""}
    return {"attempt": row["attempt"], "score": score, "credit": score, "blanks": gen["blanks"], "correct": gen["blanks"] - len(wrong),
            "text": ex, "answers": gen["answers"], "wrong": [{"expected": w, "typed": g} for w, g in wrong], "events": events,
            "added": added, "timed_out": a.timed_out, "done": close_rows(), **scale, **target}


def fill_answer(item: str, a: DrillAnswer, row: dict) -> dict:
    """Fill in the Blanks (#17): `<family>.<sense>.fb` → the bank sentence, the item regenerated, the typed word
    against the answer — exact letters, case-insensitive — credit 1 / 0 against the sentence's `b`, one event
    for the target family (hit / spelling / vocabulary), the θ before and after."""
    st = current_state()
    try:
        sid = drills.parse_fill_id(item)
    except ValueError as e:
        raise HTTPException(422, str(e))
    s = sentence_row(sid)
    gen = drills.fill_blank(s["sentence"], forms_of(s["family"]))
    if gen is None:
        raise HTTPException(409, f"{item} yields no item")
    typed = a.typed if isinstance(a.typed, str) else " ".join(a.typed)
    kind = drills.blank_kind(gen["answer"], typed)
    events = [{"family": s["family"], "kind": kind, "word": gen["answer"], "typed": typed.strip()}]
    added = add_event_words(events, "fill-in-the-blanks", s["sentence"])
    wrong = [] if kind == "hit" else [(gen["answer"], typed.strip())]
    row.update(subband=s["subband"], words=1, errors=drills.errors_column(wrong))
    scale = scale_after(st, row, textdiff.b_of(s), 1.0 if kind == "hit" else 0.0, events)
    return {"attempt": row["attempt"], "score": row["score"], "credit": row["score"], "answer": gen["answer"],
            "typed": typed.strip(), "kind": kind, "sentence": s["sentence"], "pieces": gen["pieces"], "events": events,
            "added": added, "timed_out": a.timed_out, "done": close_rows(), **scale, **target_view(s["family"], {}, [], {})}


def close_rows() -> list[str]:
    """After a drill answer: the open my-words rows whose family was hit on HIT_DAYS days are marked done
    (learn.practice_done, #13) — the families closed, for the result screen."""
    return learn.practice_done(learn.load_my_words(), store.load_attempts(), date.today())


@app.post("/api/drill/{task}/{item}")
def drill_answer(task: str, item: str, a: DrillAnswer):
    """Score the attempt (cloze, dictation) or take the self-rating (speaking, writing); one attempts.csv row;
    wrong and lacked words → my-words. Returns what the result screen shows."""
    t = task_of(task)
    attempt = attempt_of(a.attempt)
    row = {"date": attempt[:10], "attempt": attempt, "task": task, "item": item, "subband": "",
           "seconds": round(a.ms / 1000), "timed_out": a.timed_out, "score": "", "self": "", "words": "", "errors": "", "file": ""}
    if task == "read-and-complete":
        return cloze_answer(item, a, row)
    if task == "fill-in-the-blanks":
        return fill_answer(item, a, row)
    if task == "listen-and-type":
        return dictation_answer(item, a, row)
    # speaking and writing: the self-rating (mean of four lines, half up) and the "words I lacked" box
    try:
        rating = drills.self_rating(a.rating) if a.rating else None
    except ValueError as e:
        raise HTTPException(422, str(e))
    if task in drills.SPEAKING:
        if task == "read-aloud":
            row["subband"] = sentence_row(item)["subband"]
        else:
            prompt_row(task, item)
        row["file"] = f"{drills.REL_RECORDINGS}/{attempt}.webm" if (drills.RECORDINGS / f"{attempt}.webm").is_file() else ""
    else:
        file, n = write_draft(task, item, attempt, a.text, a.text2, round(a.ms / 1000))
        row.update(words=n, file=file)
    added = add_words(drills.lacked_words(a.lacked), task, item, map_only=False)
    row["self"] = rating if rating is not None else ""
    store.append_attempt(row)
    return {"attempt": attempt, "self": rating, "words": row["words"], "file": row["file"], "added": added,
            "min": t["min"], "timed_out": a.timed_out, "done": close_rows(),
            "recording_url": f"/api/drill/{task}/{attempt}/recording" if row["file"].endswith(".webm") else None}
