"""The run layout (docs/RUN_LAYOUT.md): directories,
.gitignore and the additive record blocks.  Pure filesystem + pydantic."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.workspace import Workspace


def test_create_makes_the_buckets_and_gitignore(tmp_path: Path):
    ws = Workspace(tmp_path / "run").create()
    assert ws.deliverable.is_dir() and ws.telemetry.is_dir() and ws.artifacts.is_dir()
    ignored = (ws.root / ".gitignore").read_text().splitlines()
    assert {"artifacts/", "deliverable/", "telemetry/", "trajectories/"} <= set(ignored)


def test_a_stale_index_lock_names_the_remedy(tmp_path, monkeypatch):
    """A stale git index lock is surfaced with its exact safe remedy."""
    import subprocess

    import pytest

    import codeverse3d.workspace as W
    from codeverse3d.workspace import Workspace, WorkspaceGitError

    monkeypatch.setattr(W.time, "sleep", lambda _s: None)  # the live-holder retry backoff: nothing to wait for

    ws = Workspace(tmp_path / "run").create()
    ws.src.mkdir(parents=True, exist_ok=True)
    (ws.src / "model.py").write_text("x = 1\n")
    ws.commit("round 0")

    (ws.root / ".git" / "index.lock").write_text("")  # what a SIGKILL leaves behind
    with pytest.raises(WorkspaceGitError) as ei:
        ws.commit("round 1")

    msg = str(ei.value)
    assert "index.lock" in msg, "git's own explanation must reach the operator"
    assert "File exists" in msg
    assert f"rm {ws.root / '.git' / 'index.lock'}" in msg, "the remedy must be named"
    # still a CalledProcessError, so existing handlers keep working, and stderr is kept
    assert isinstance(ei.value, subprocess.CalledProcessError)
    assert "index.lock" in (ei.value.stderr or "")


def test_a_planted_git_hook_or_filter_never_runs_on_a_commit(tmp_path):
    """Agent-writable hooks and clean filters never execute with harness privileges."""
    ws = Workspace(tmp_path / "run").create()
    fired = tmp_path / "fired"
    shell = f"#!/bin/sh\necho x >> {fired}\n"
    for d in (ws.root / ".git" / "hooks", ws.root / "evilhooks"):
        d.mkdir(parents=True, exist_ok=True)
        (d / "pre-commit").write_text(shell)
        (d / "pre-commit").chmod(0o755)
    (ws.root / ".gitconfig").write_text(
        f'[core]\n\thooksPath = {ws.root / "evilhooks"}\n'
        f'[filter "pwn"]\n\tclean = sh -c \'echo g >> {fired}; cat\'\n')
    (ws.root / ".gitattributes").write_text("*.py filter=pwn\n")
    ws._git("config", "--local", "filter.pwn.clean", f"sh -c 'echo l >> {fired}; cat'")

    (ws.src / "model.py").write_text("x = 1\n")
    ws.commit("round 0")

    assert not fired.exists(), f"a planted git hook/filter ran: {fired.read_text()!r}"
    assert ws._git("config", "--local", "--get", "filter.pwn.clean", check=False).returncode != 0, \
        "an exec-capable local key must be unset before the commit"

    # finish_session reaches git through changed_files() (git add / git diff) BEFORE it commits
    ws._git("config", "--local", "filter.pwn.clean", f"sh -c 'echo c >> {fired}; cat'")
    (ws.src / "model.py").write_text("x = 2\n")
    ws.changed_files()
    assert not fired.exists(), f"a planted filter ran during changed_files(): {fired.read_text()!r}"
