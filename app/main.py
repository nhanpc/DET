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

from . import drills, irt, learn, progress, store, tts
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
    """The learner now, from the history on disk: `sessions`, the θ history (`thetas`), `theta`/`se` of the last
    reliable session (None before one), `level`, `frontier`, `det_estimate`, `det_range`. The start page, the
    Learn page and the drills need the same frontier, and the sessions are loaded once per request."""
    sessions = store.load_sessions()
    thetas = learn.theta_history(sessions, SUBBANDS, BANK.b)
    now = learn.current_theta(thetas)
    theta, se = now if now else (None, None)
    level, front = learn.frontier(theta, SUBBANDS)
    return {"sessions": sessions, "thetas": thetas, "theta": theta, "se": se, "level": level, "frontier": front,
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
            "resume": {"session": resume.id, "block": block_view(resume)} if resume else None,
            "tasks": drills.TASKS, "today": drills.today_counts(store.load_attempts())}


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
    sessions = store.load_sessions()
    levels = store.load_levels()
    r = progress.build(sessions, levels, SUBBANDS, b_of=BANK.b)
    stats = learn.word_stats(sessions, SUBBANDS)
    history, _ = learn.level_history(levels)
    my_words = learn.open_my_words(learn.load_my_words())
    counts = {**r["counts"], "my-words": len(my_words)}
    return {"sessions": r["tests"], "history": history, "trend": r["trend"], "level": r["level"],
            "frontier": r["frontier"], "theta": r["theta"], "se": r["se"], "det_estimate": r["det_estimate"],
            "det_range": r["det_range"], "thetas": r["thetas"], "theta_series": r["theta_series"],
            "subbands": r["subbands"], "counts": counts, "series": r["series"], "batch": n,
            "words": learn.study_list(stats, r["frontier"], BANK.index, SYNONYMS, my_words, n, BANK.examples)}


@app.get("/api/progress")
def progress_page():
    """The report dict scripts/report.py renders into vocab/progress.md (no Anki), mock tests included;
    a malformed mocks.csv row is a 422 naming the line (#11), the same message the script prints."""
    try:
        return progress.build(store.load_sessions(), store.load_levels(), SUBBANDS, mocks=store.load_mocks(), b_of=BANK.b)
    except ValueError as e:
        raise HTTPException(422, f"vocab/tests/mocks.csv: {e}")


@app.get("/api/learn")
def learn_page(n: int = learn.BATCH):
    return learn_view(max(1, min(n, 100)))


@app.post("/api/learn/export")
def learn_export(n: int = learn.BATCH):
    words = learn_view(max(1, min(n, 100)))["words"]
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
    depend on the seed), with the family's forms pattern; every sub-band, built once."""
    if not _CLOZE_POOL:
        for c in drills.sentence_candidates(SENSES, BANK.index):
            pat = drills.forms_pattern(c["family"], drills.members(BANK.index[c["family"]]))
            if drills.cloze(c["example"], 0, pat) is not None:
                _CLOZE_POOL.append({**c, "pattern": pat, "key": f"{c['family']}.{c['sense']}"})
    return _CLOZE_POOL


def sentence_bank() -> list[dict]:
    """practice/listen-and-type/sentences.csv, written from senses.csv on first use (gitignored)."""
    rows = drills.load_sentences()
    if not rows:
        rows = drills.build_sentences(SENSES, BANK.index)
        drills.write_sentences(rows)
    return rows


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


@app.get("/api/drill/{task}/next")
def drill_next(task: str, mode: str = "sentence"):
    """A fresh item for `task` and the attempt id to answer it with. Cloze and dictation draw from the frontier
    sub-band (cloze also from the study list) and skip items shown in the last NO_REPEAT_DAYS days; prompts
    rotate the same way. `mode=passage` = a hand-pasted passage (practice/read-and-complete/passages/)."""
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
    sessions, front = st["sessions"], st["frontier"]
    if task == "read-and-complete" and mode == "passage":
        p = drills.pick(drills.load_passages(), recent, lambda p: p["slug"], rng)
        if p is None:
            raise HTTPException(404, "no passage yet: paste one into practice/read-and-complete/passages/")
        seed = rng.randrange(10_000)
        item = drills.cloze(p["text"], seed, passage=True)
        if item is None:
            raise HTTPException(404, f"passage {p['slug']} yields fewer than {drills.MIN_BLANKS} blanks")
        return {"task": task, "attempt": attempt, "id": drills.passage_id(p["slug"], seed), "mode": "passage",
                "subband": "", "source": p.get("source", ""), "pieces": item["pieces"], "blanks": item["blanks"],
                "seconds": t["passage_seconds"]}
    if task == "read-and-complete":
        stats = learn.word_stats(sessions, SUBBANDS)
        my_words = learn.open_my_words(learn.load_my_words())
        studying = {w["family"] for w in learn.study_list(stats, front, BANK.index, SYNONYMS, my_words, learn.BATCH, BANK.examples)}
        pool = [c for c in cloze_pool() if c["subband"] == front or c["family"] in studying]
        c = drills.pick(pool, recent, lambda c: c["key"], rng)
        if c is None:
            raise HTTPException(404, f"no example sentence for {front}")
        seed = rng.randrange(10_000)
        item = drills.cloze(c["example"], seed, c["pattern"])
        return {"task": task, "attempt": attempt, "id": drills.cloze_id(c["family"], c["sense"], seed), "mode": "sentence",
                "subband": c["subband"], "pieces": item["pieces"], "blanks": item["blanks"], "seconds": t["seconds"]}
    # listen-and-type and read-aloud: the dictation bank, frontier sub-band
    pool = [r for r in sentence_bank() if r["subband"] == front] or sentence_bank()
    row = drills.pick(pool, recent, lambda r: r["id"], rng)
    if row is None:
        raise HTTPException(404, "the sentence bank is empty")
    d = {"task": task, "attempt": attempt, "id": row["id"], "subband": row["subband"], "seconds": t["seconds"], "prep": 0}
    if task == "read-aloud":
        return {**d, "sentence": row["sentence"], "min": 0}
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


@app.post("/api/drill/{task}/{item}")
def drill_answer(task: str, item: str, a: DrillAnswer):
    """Score the attempt (cloze, dictation) or take the self-rating (speaking, writing); one attempts.csv row;
    wrong and lacked words → my-words. Returns what the result screen shows."""
    t = task_of(task)
    attempt = attempt_of(a.attempt)
    row = {"date": attempt[:10], "attempt": attempt, "task": task, "item": item, "subband": "",
           "seconds": round(a.ms / 1000), "timed_out": a.timed_out, "score": "", "self": "", "words": "", "errors": "", "file": ""}
    if task == "read-and-complete":
        family, sense, seed = drills.parse_cloze_id(item, BANK.index)
        if sense is not None:
            ex = EXAMPLES.get((family, str(sense)))
            if not ex:
                raise HTTPException(404, f"unknown sense {family}.{sense}")
            gen = drills.cloze(ex, seed, drills.forms_pattern(family, drills.members(BANK.index[family])))
            row["subband"] = BANK.index[family]["subband"]
        else:
            p = next((p for p in drills.load_passages() if p["slug"] == family), None)
            if p is None:
                raise HTTPException(404, f"unknown passage {family!r}")
            ex = p["text"]
            gen = drills.cloze(ex, seed, passage=True)
        if gen is None:
            raise HTTPException(409, f"{item} yields no item")
        typed = a.typed if isinstance(a.typed, list) else [a.typed]
        score, wrong = drills.score_cloze(gen["answers"], typed)
        added = add_words([w for w, _ in wrong], task, ex, map_only=True)
        row.update(score=score, words=gen["blanks"], errors=drills.errors_column(wrong))
        store.append_attempt(row)
        return {"attempt": attempt, "score": score, "blanks": gen["blanks"], "correct": gen["blanks"] - len(wrong),
                "text": ex, "answers": gen["answers"], "wrong": [{"expected": w, "typed": g} for w, g in wrong],
                "added": added, "timed_out": a.timed_out}
    if task == "listen-and-type":
        s = sentence_row(item)
        r = drills.dictation_score(s["sentence"], a.typed if isinstance(a.typed, str) else " ".join(a.typed))
        added = add_words(r["wrong"], task, s["sentence"], map_only=True)
        row.update(subband=s["subband"], score=r["score"], words=r["words"], errors=drills.errors_column(r["errors"]))
        store.append_attempt(row)
        return {"attempt": attempt, "score": r["score"], "words": r["words"], "reference": s["sentence"],
                "diff": r["diff"], "errors": r["errors"], "added": added, "plays": a.plays, "timed_out": a.timed_out}
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
            "min": t["min"], "timed_out": a.timed_out,
            "recording_url": f"/api/drill/{task}/{attempt}/recording" if row["file"].endswith(".webm") else None}
