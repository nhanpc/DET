"""End-to-end through the FastAPI app with a learner who says No to everything."""
import csv

from fastapi.testclient import TestClient

from app import main, store


def test_full_session_writes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(store, "RESULTS", tmp_path / "results.csv")
    monkeypatch.setattr(store, "LEVELS", tmp_path / "levels.csv")
    monkeypatch.setattr(store, "MY_WORDS", tmp_path / "my-words.csv")
    c = TestClient(main.app)

    assert c.get("/").status_code == 200
    d = c.post("/api/session").json()
    sid, block = d["session"], d["block"]
    assert block["no"] == 1 and len(block["words"]) == 15
    words = set(block["words"])

    status = None
    while status != "finished":
        for _ in range(block["size"] - block["pos"]):
            r = c.post(f"/api/session/{sid}/answer", json={"yes": False, "ms": 900}).json()
            status, block = r["status"], r["block"]
        if status == "block_done":
            block = c.post(f"/api/session/{sid}/next").json()["block"]
            assert not words & set(block["words"])
            words |= set(block["words"])

    r = c.get(f"/api/session/{sid}/result").json()
    assert r["level"] is None and r["reliable"] and r["blocks"] == 6
    assert len(r["misses"]) == 60
    assert (tmp_path / "sessions" / f"{sid}.json").exists()
    with (tmp_path / "results.csv").open() as f:
        assert len(list(csv.DictReader(f))) == 6
    with (tmp_path / "levels.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["reliable"] == "1" and rows[0]["items"] == "90"

    s = c.post(f"/api/session/{sid}/save-misses").json()
    assert s["added"] == 60
    assert c.post(f"/api/session/{sid}/save-misses").json()["added"] == 0   # no duplicates
    with (tmp_path / "my-words.csv").open() as f:
        my = list(csv.DictReader(f))
    assert len(my) == 60 and "definition" in my[0]
