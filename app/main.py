"""FastAPI backend. Run from the repo root:  uvicorn app.main:app --reload  →  http://localhost:8000"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import learn, store
from .adaptive import MAX_BLOCKS, PSEUDO_PER_BLOCK, REAL_PER_BLOCK, Session
from .bank import Bank, load_subbands

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="DET vocabulary level test")
SUBBANDS = load_subbands()
BANK = Bank()
SYNONYMS = learn.load_synonyms()
# Unfinished sessions come back from disk so a closed tab or a restart does not lose a test.
SESSIONS: dict[str, Session] = {d["id"]: Session.restore(d, SUBBANDS, BANK)
                                for d in store.load_sessions() if not d["finished"]}


class Answer(BaseModel):
    yes: bool
    ms: Optional[int] = None


class MyWord(BaseModel):
    family: str
    source: str = "learn"          # test (pinned on the result page), learn (the Add-a-word box) or a drill's task id
    note: str = ""


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
    d["misses"] = [{"word": i.word, "definition": i.definition} for i in r.misses]
    d["date"] = s.started.strftime("%-d %b %Y")
    d["seconds"] = int((datetime.now() - s.started).total_seconds())
    d["stop_reason"] = s.stop_reason
    return d


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def current_frontier() -> tuple[list[dict], list[dict], Optional[str], str]:
    """(sessions, pooled scores, level, frontier) from the history on disk — the start page and the Learn
    page need the same frontier, and the sessions are loaded once per request."""
    sessions = store.load_sessions()
    scores = learn.subband_scores(sessions, SUBBANDS)
    level, front = learn.frontier(scores)
    return sessions, scores, level, front


@app.get("/api/config")
def config():
    last = store.load_levels()
    open_ = [s for s in SESSIONS.values() if not s.finished]
    resume = max(open_, key=lambda s: s.started) if open_ else None
    _, _, _, front = current_frontier()
    return {"real_per_block": REAL_PER_BLOCK, "pseudo_per_block": PSEUDO_PER_BLOCK, "max_blocks": MAX_BLOCKS,
            "subbands": [asdict(b) for b in SUBBANDS], "last": last[-1] if last else None,
            "retest": learn.retest_due(last, front),
            "resume": {"session": resume.id, "block": block_view(resume)} if resume else None}


@app.post("/api/session")
def start(start: Optional[str] = None):
    """`start` = sub-band of block 1 (the Re-test button passes the frontier); default: the middle of the scale."""
    if start is not None and start not in {b.name for b in SUBBANDS}:
        raise HTTPException(400, f"unknown sub-band {start!r}")
    sid = datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    s = Session.create(sid, SUBBANDS, BANK, start_band=start)
    SESSIONS[sid] = s
    return {"session": sid, "block": block_view(s), "max_blocks": MAX_BLOCKS}


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
    sessions, scores, level, front = current_frontier()
    stats = learn.word_stats(sessions)
    history, trend = learn.level_history(store.load_levels())
    my_words = learn.open_my_words(learn.load_my_words())
    counts = {k: 0 for k in ("repeat", "missed", "learned", "shaky", "known")}
    for w in stats.values():
        counts[w.status] += 1
    counts["my-words"] = len(my_words)
    return {"sessions": len(history), "history": history, "trend": trend, "level": level, "frontier": front,
            "subbands": scores, "counts": counts, "batch": n,
            "words": learn.study_list(stats, front, BANK.index, SYNONYMS, my_words, n, BANK.examples)}


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
