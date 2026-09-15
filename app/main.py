"""FastAPI backend. Run from the repo root:  uvicorn app.main:app --reload  →  http://localhost:8000"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import store
from .adaptive import MAX_BLOCKS, PSEUDO_PER_BLOCK, REAL_PER_BLOCK, Session
from .bank import Bank, load_subbands

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="DET vocabulary level test")
SUBBANDS = load_subbands()
BANK = Bank()
# Unfinished sessions come back from disk so a closed tab or a restart does not lose a test.
SESSIONS: dict[str, Session] = {d["id"]: Session.restore(d, SUBBANDS, BANK)
                                for d in store.load_sessions() if not d["finished"]}


class Answer(BaseModel):
    yes: bool
    ms: Optional[int] = None


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


@app.get("/api/config")
def config():
    last = store.load_levels()
    open_ = [s for s in SESSIONS.values() if not s.finished]
    resume = max(open_, key=lambda s: s.started) if open_ else None
    return {"real_per_block": REAL_PER_BLOCK, "pseudo_per_block": PSEUDO_PER_BLOCK, "max_blocks": MAX_BLOCKS,
            "subbands": [asdict(b) for b in SUBBANDS], "last": last[-1] if last else None,
            "resume": {"session": resume.id, "block": block_view(resume)} if resume else None}


@app.post("/api/session")
def start():
    sid = datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    s = Session.create(sid, SUBBANDS, BANK)
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
