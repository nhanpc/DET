import pytest

from app import learn, plan, store, vcs


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """No test reads or writes the real data files (they grow as the app is used, #20) and none commits:
    every data path points into tmp_path unless the test patches it itself; git is off."""
    for name in ("SESSIONS", "RESULTS", "MISSES", "LEVELS", "MOCKS", "ATTEMPTS"):
        monkeypatch.setattr(store, name, tmp_path / "data" / getattr(store, name).name)
    monkeypatch.setattr(learn, "MY_WORDS", tmp_path / "data" / "my-words.csv")
    monkeypatch.setattr(learn, "DECKS", tmp_path / "data" / "decks")
    monkeypatch.setattr(plan, "PLAN", tmp_path / "data" / "plan.csv")
    monkeypatch.setattr(plan, "LOG", tmp_path / "data" / "plan-log.csv")
    monkeypatch.setattr(vcs, "ENABLED", False)
