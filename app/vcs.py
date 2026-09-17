"""Commit the data files as the app writes them (issue #20).

commit(paths, message) stages the given paths (`git add -A -- paths`, so deletions count and ignored files are
skipped) and commits only them (`git commit -- paths`: whatever the user staged by hand stays staged), in a
daemon thread behind a lock so no response waits for git. Nothing staged → no commit. Any failure — not a
repo, git missing, a hook — is logged and kept in `last`, never raised. DET_GIT=0 disables it (the tests run
with it off); DET_GIT_PUSH=1 pushes after each commit.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Iterable, Optional

from .bank import ROOT

ENABLED = os.environ.get("DET_GIT", "1") != "0"
PUSH = os.environ.get("DET_GIT_PUSH", "0") == "1"
TIMEOUT = 60                                    # seconds per git command (a push over a slow line)
log = logging.getLogger("det.vcs")
_lock = threading.Lock()
last: dict = {"message": None, "ok": None, "error": None}    # the latest commit attempt, for /api/config


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=TIMEOUT)


def relative(paths: Iterable[Path | str], root: Path) -> list[str]:
    """The paths under `root`, relative to it; the others (a test's tmp_path) are dropped."""
    out = []
    for p in paths:
        p = Path(p)
        p = p if p.is_absolute() else root / p
        if p.is_relative_to(root):
            out.append(str(p.relative_to(root)))
    return out


def commit_now(paths: Iterable[Path | str], message: str, root: Optional[Path] = None, push: Optional[bool] = None) -> bool:
    """Stage and commit `paths` under `root` now; True when a commit was made, False when there was nothing to
    commit or a path was outside the repo. Raises RuntimeError with git's stderr on any git failure."""
    root, push = root or ROOT, PUSH if push is None else push
    rel = relative(paths, root)
    if not rel or not (root / ".git").exists():
        return False
    r = _git(root, "add", "-A", "--", *rel)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "git add failed")
    if _git(root, "diff", "--cached", "--quiet", "--", *rel).returncode == 0:
        return False
    r = _git(root, "commit", "-q", "-m", message, "--", *rel)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or "git commit failed")
    if push:
        r = _git(root, "push", "-q")
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or "git push failed")
    return True


def _run(paths: list[str], message: str) -> None:
    with _lock:
        try:
            done = commit_now(paths, message)
            last.update(message=message, ok=done, error=None)
            if done:
                log.info("committed: %s", message)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            last.update(message=message, ok=False, error=str(e))
            log.warning("git: %s — %s", message, e)


_pending: list[threading.Thread] = []


def commit(paths: Iterable[Path | str], message: str) -> None:
    """Commit in the background; a no-op when disabled."""
    if not ENABLED:
        return
    t = threading.Thread(target=_run, args=([str(p) for p in paths], message), daemon=True, name="det-git")
    _pending.append(t)
    t.start()


def wait() -> None:
    """Block until every pending commit is done — for tests and scripts."""
    while _pending:
        _pending.pop(0).join()
