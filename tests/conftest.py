import pytest

from app import vcs


@pytest.fixture(autouse=True)
def no_git(monkeypatch):
    """The suite never commits (issue #20): tests that want git call vcs.commit_now on a throwaway repo."""
    monkeypatch.setattr(vcs, "ENABLED", False)
