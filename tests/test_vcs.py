"""Commit as the app writes (issue #20): commit_now on a throwaway repo, the background path, and which
route commits what."""
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import drills, learn, main, plan, store, tts, vcs


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_commit_now_commits_only_the_named_paths(repo):
    data = repo / "vocab" / "tests"
    data.mkdir(parents=True)
    (data / "levels.csv").write_text("a\n")
    (repo / "README.md").write_text("changed by hand\n")
    git(repo, "add", "README.md")                                   # staged by the user, must stay staged
    assert vcs.commit_now([data / "levels.csv"], "Level test: finished", root=repo, push=False)
    assert git(repo, "log", "-1", "--format=%s") == "Level test: finished"
    assert git(repo, "show", "--stat", "--format=", "HEAD").count("|") == 1 and "levels.csv" in git(repo, "show", "--stat", "--format=", "HEAD")
    assert "README.md" in git(repo, "diff", "--cached", "--name-only")
    # nothing changed → no commit; a directory pathspec picks up new files under it; deletions count
    assert not vcs.commit_now([data / "levels.csv"], "again", root=repo, push=False)
    (data / "results.csv").write_text("b\n")
    assert vcs.commit_now([data], "block", root=repo, push=False)
    (data / "results.csv").unlink()
    assert vcs.commit_now([data], "gone", root=repo, push=False)
    assert git(repo, "ls-files", "vocab") == "vocab/tests/levels.csv"
    # a path that does not exist yet is dropped, not fatal (the drafts folder before the first draft)
    (data / "levels.csv").write_text("d\n")
    assert vcs.commit_now([data / "levels.csv", repo / "practice" / "writing" / "drafts"], "no drafts yet", root=repo, push=False)
    assert git(repo, "log", "-1", "--format=%s") == "no drafts yet"
    # paths outside the repo, or no repo at all, are a quiet no-op
    assert not vcs.commit_now([repo.parent / "elsewhere.csv"], "x", root=repo, push=False)
    assert not vcs.commit_now([repo / "x"], "x", root=repo / "vocab", push=False)
    # a failing push surfaces as RuntimeError (no remote)
    (data / "levels.csv").write_text("c\n")
    with pytest.raises(RuntimeError):
        vcs.commit_now([data], "push", root=repo, push=True)


def test_background_commit_records_the_outcome(repo, monkeypatch):
    monkeypatch.setattr(vcs, "ENABLED", True)
    monkeypatch.setattr(vcs, "ROOT", repo)
    (repo / "plan-log.csv").write_text("date,words\n2026-09-17,1\n")
    vcs.commit([repo / "plan-log.csv"], "Plan: words done 2026-09-17")
    vcs.wait()
    assert vcs.last == {"message": "Plan: words done 2026-09-17", "ok": True, "error": None}
    assert git(repo, "log", "-1", "--format=%s") == "Plan: words done 2026-09-17"
    vcs.commit([repo / "plan-log.csv"], "nothing new")
    vcs.wait()
    assert vcs.last["ok"] is False and vcs.last["error"] is None
    monkeypatch.setattr(vcs, "PUSH", True)
    (repo / "plan-log.csv").write_text("date,words\n2026-09-17,1\n2026-09-18,1\n")
    vcs.commit([repo / "plan-log.csv"], "pushes")                    # the commit lands, the push fails, nothing raises
    vcs.wait()
    assert vcs.last["ok"] is False and vcs.last["error"]
    assert git(repo, "log", "-1", "--format=%s") == "pushes"


@pytest.fixture
def files(tmp_path, monkeypatch):
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS", "ATTEMPTS"):
        monkeypatch.setattr(store, name, tmp_path / getattr(store, name).name)
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "my-words.csv")
    monkeypatch.setattr(plan, "PLAN", tmp_path / "plan.csv")
    monkeypatch.setattr(plan, "LOG", tmp_path / "plan-log.csv")
    monkeypatch.setattr(drills, "SENTENCES", tmp_path / "sentences.csv")
    monkeypatch.setattr(drills, "DRAFTS", tmp_path / "drafts")
    monkeypatch.setattr(tts, "AUDIO", tmp_path / "audio")
    monkeypatch.setattr(tts, "synthesise", lambda text, voice, path: path.write_bytes(b"ID3"))
    calls = []
    monkeypatch.setattr(vcs, "commit", lambda paths, message: calls.append((list(paths), message)))
    return calls


def test_routes_commit_what_they_write(files):
    calls = files
    c = TestClient(main.app)
    d = c.post("/api/session").json()
    sid = d["session"]
    for _ in range(14):
        c.post(f"/api/session/{sid}/answer", json={"yes": False, "ms": 900})
    assert calls == []                                               # per-answer saves are not commit points
    r = c.post(f"/api/session/{sid}/answer", json={"yes": False, "ms": 900}).json()
    assert r["status"] != "next" and len(calls) == 1
    assert calls[-1][0] == main.TEST_FILES and calls[-1][1].startswith(f"Level test {sid}: ") and "block 1, " in calls[-1][1]
    assert c.post("/api/my-words", json={"family": "utter", "source": "learn"}).status_code == 200
    assert calls[-1] == ([learn.MY_WORDS], "my-words: +utter (learn)")
    plan.write_plan(plan.make(plan.date(2026, 9, 21), main.SUBBANDS))
    c.post("/api/plan/words")
    assert calls[-1][0] == [plan.LOG] and calls[-1][1].startswith("Plan: words done ")
    c.post("/api/plan/words")                                        # already ticked: nothing written, nothing committed
    assert sum(1 for p, m in calls if m.startswith("Plan:")) == 1
    d = c.get("/api/drill/read-aloud/next").json()
    c.post(f"/api/drill/read-aloud/{d['id']}", json={"attempt": d["attempt"], "ms": 19400, "rating": [4, 3, 3, 4], "lacked": "serendipity"})
    assert calls[-1] == (main.DRILL_FILES, f"Drill read-aloud {d['attempt']}: self 4, +1 my-words")
    assert c.get("/api/config").json()["git"]["enabled"] is False
