"""End-to-end through the FastAPI app with a learner who says No to everything."""
import csv
import json

import pytest
from fastapi.testclient import TestClient

from app import main, store
from app.adaptive import Session


@pytest.fixture
def files(tmp_path, monkeypatch):
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    return tmp_path


def rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def test_full_session_writes_files_as_it_goes(files):
    c = TestClient(main.app)
    assert c.get("/").status_code == 200
    d = c.post("/api/session").json()
    sid, block = d["session"], d["block"]
    assert block["no"] == 1 and len(block["words"]) == 15
    words = set(block["words"])
    session_file = files / "sessions" / f"{sid}.json"
    assert not session_file.exists()                      # nothing on disk until the first answer

    status = None
    while status != "finished":
        for _ in range(block["size"] - block["pos"]):
            r = c.post(f"/api/session/{sid}/answer", json={"yes": False, "ms": 900}).json()
            status, block = r["status"], r["block"]
        saved = json.loads(session_file.read_text())
        assert saved["finished"] == (status == "finished")
        assert len(rows(files / "results.csv")) == block["no"]                 # written at every block end
        assert len(rows(files / "misses.csv")) == 10 * block["no"]
        assert not (files / "levels.csv").exists() or status == "finished"
        if status == "block_done":
            block = c.post(f"/api/session/{sid}/next").json()["block"]
            assert not words & set(block["words"])
            words |= set(block["words"])

    r = c.get(f"/api/session/{sid}/result").json()
    assert r["level"] is None and r["reliable"] and r["blocks"] == 6
    assert len(r["misses"]) == 60
    assert json.loads(session_file.read_text())["result"]["misses"] == [m["word"] for m in r["misses"]]

    lv = rows(files / "levels.csv")
    assert len(lv) == 1 and lv[0]["session"] == sid and lv[0]["reliable"] == "1" and lv[0]["items"] == "90"
    ms = rows(files / "misses.csv")
    assert len(ms) == 60 and {m["kind"] for m in ms} == {"miss"} and ms[0]["ms"] == "900"
    assert [m["word"] for m in ms] == [m["word"] for m in r["misses"]]
    assert c.get("/api/config").json()["resume"] is None


def test_unfinished_session_is_offered_for_resume_and_restored_from_disk(files):
    c = TestClient(main.app)
    d = c.post("/api/session").json()
    sid, block = d["session"], d["block"]
    for _ in range(15):
        c.post(f"/api/session/{sid}/answer", json={"yes": True, "ms": 500})   # block 1: yes to all
    block = c.post(f"/api/session/{sid}/next").json()["block"]
    for _ in range(3):
        c.post(f"/api/session/{sid}/answer", json={"yes": False, "ms": 500})  # 3 answers into block 2

    resume = c.get("/api/config").json()["resume"]
    assert resume["session"] == sid and resume["block"]["no"] == 2 and resume["block"]["pos"] == 3
    assert [m["kind"] for m in rows(files / "misses.csv")] == ["false_alarm"] * 5
    assert not (files / "levels.csv").exists()

    # A restart rebuilds the session from sessions/<id>.json and continues from the 4th word of block 2.
    live = main.SESSIONS[sid]
    saved = store.load_sessions()
    assert [s["id"] for s in saved] == [sid] and not saved[0]["finished"]
    back = Session.restore(saved[0], main.SUBBANDS, main.BANK)
    assert back.band_idx == live.band_idx and back.used == live.used and back.last_dir == live.last_dir
    assert back.block.pos == 3 and [i.word for i in back.block.items] == block["words"]
    assert [i.answer for i in back.block.items[:3]] == [False] * 3
    assert store.session_dict(back) == store.session_dict(live)
    assert back.answer(False, 1) == "next"
