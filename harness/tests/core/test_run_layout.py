"""The three-bucket run layout (docs/RUN_LAYOUT.md): directories, aliases,
.gitignore and the additive record blocks.  Pure filesystem + pydantic."""

from __future__ import annotations

import json
import os
from pathlib import Path

from codeverse3d.contracts.run import RunDeliverable, RunRecord, RunTelemetry
from codeverse3d.contracts.spec import Spec
from codeverse3d.workspace import EVIDENCE_DIR, LAYOUT_ALIASES, Workspace


def test_create_makes_the_buckets_aliases_and_gitignore(tmp_path: Path):
    ws = Workspace(tmp_path / "run").create()
    assert ws.deliverable.is_dir() and ws.telemetry.is_dir() and ws.artifacts.is_dir()
    assert os.readlink(ws.root / EVIDENCE_DIR) == "artifacts"  # evidence/ is the alias
    ignored = (ws.root / ".gitignore").read_text().splitlines()
    assert {"artifacts/", "deliverable/", "telemetry/", "evidence", "trajectories/"} <= set(ignored)
    for name, target in LAYOUT_ALIASES:
        link = ws.root / name
        assert link.is_symlink(), name
        assert os.readlink(link) == target
    ws.events_path.write_text('{"event": "run.start"}\n')
    assert (ws.telemetry / "events.jsonl").read_text().strip().endswith("}")
    # an atomic rewrite of the physical file must not disturb the alias
    ws.write_json(ws.state_path, {"status": "passed"})
    assert json.loads((ws.telemetry / "run_state.json").read_text())["status"] == "passed"
    assert (ws.telemetry / "run_state.json").is_symlink()


def test_ensure_layout_is_idempotent_and_dry_runnable(tmp_path: Path):
    ws = Workspace(tmp_path / "run")
    planned = ws.ensure_layout(dry_run=True)
    assert planned and not ws.deliverable.exists()
    first = ws.ensure_layout()
    assert set(first) == set(planned)
    assert ws.ensure_layout() == {}  # nothing left to do
    assert ws.ensure_layout(dry_run=True) == {}


def test_ensure_layout_never_clobbers_real_data(tmp_path: Path):
    ws = Workspace(tmp_path / "run")
    (ws.root / "evidence").mkdir(parents=True)
    (ws.root / "evidence" / "keep.txt").write_text("evidence")
    actions = ws.ensure_layout()
    assert actions["evidence"] == "kept"
    assert (ws.root / "evidence" / "keep.txt").read_text() == "evidence"
    assert not (ws.root / "evidence").is_symlink()


def test_record_blocks_are_additive(tmp_path: Path):
    """An old record.json (no telemetry, a deliverable block) validates; an old manifest loads."""
    spec = Spec(id="x", track="static_object", language="blender", prompt="a chair")
    old = {"spec": spec.model_dump(mode="json"), "workspace": str(tmp_path), "status": "passed",
           "deliverable": {"best_round": 1, "commit": "abc"}}
    rec = RunRecord.model_validate(old)
    assert rec.telemetry is None and rec.status.value == "stopped"
    rec.telemetry = RunTelemetry()
    again = RunRecord.model_validate_json(rec.model_dump_json())
    assert again.telemetry is not None and again.telemetry.schema_version == 1
    assert RunDeliverable.model_validate({"best_round": 1, "commit": "abc"}).round == 1  # a pre-2026-09-22 manifest


# --------------------------------------------------------------------------- relocation
def _moved_run(tmp_path: Path):
    """Create a record whose absolute sheet path predates a workspace move."""
    import shutil

    from codeverse3d.contracts.artifacts import Judgment, RenderSet
    from codeverse3d.contracts.common import Backends, Language, Track
    from codeverse3d.contracts.run import RoundRecord, RunStatus

    a = tmp_path / "A" / "run1"
    (a / "artifacts" / "renders" / "r01").mkdir(parents=True)
    sheet_a = a / "artifacts" / "renders" / "r01" / "sheet.png"
    sheet_a.write_bytes(b"PNG")
    spec = Spec(id="run1", track=Track.STATIC_OBJECT, language=Language.BLENDER,
                prompt="a wooden chair", backends=Backends(generator="gemini-cli:gemini-3.6-flash"))
    rec = RunRecord(spec=spec, workspace=str(a), status=RunStatus.MAX_ROUNDS,
                    rounds=[RoundRecord(index=0, kind="generate",
                                        judgment=Judgment(rubric="r", scores={}, overall=0.6, passed=False),
                                        renders=RenderSet(views=[], contact_sheet=str(sheet_a)))])
    b = tmp_path / "B" / "run1"
    b.parent.mkdir(parents=True)
    shutil.move(str(a), str(b))
    return a, b, sheet_a, rec


def _captured(render) -> str:
    """rich's Console holds its own stream, so capsys never sees it; widen it too so the
    panel cannot wrap the path under assertion."""
    from codeverse3d.cli._common import console

    old_width, console.width = console.width, 400
    try:
        with console.capture() as cap:
            render()
    finally:
        console.width = old_width
    return cap.get()


def test_a_copied_run_reads_its_own_files_while_the_original_still_exists(tmp_path):
    """V10f: a copied run's stored absolute paths rebase onto THIS root, even while the original lives."""
    import shutil

    from codeverse3d.workspace import Workspace

    a = tmp_path / "A" / "run1"
    (a / "artifacts" / "renders" / "r01").mkdir(parents=True)
    sheet_a = a / "artifacts" / "renders" / "r01" / "sheet.png"
    sheet_a.write_bytes(b"ORIGINAL")
    b = tmp_path / "B" / "run1"
    b.parent.mkdir(parents=True)
    shutil.copytree(a, b)  # copy: the original stays alive
    (b / "artifacts" / "renders" / "r01" / "sheet.png").write_bytes(b"COPY")
    got = Workspace(b).rebase(str(sheet_a))
    assert got == b / "artifacts" / "renders" / "r01" / "sheet.png"
    assert got.read_bytes() == b"COPY", "the copy's own file must win over the live original"
    # ...and a path already under THIS root is untouched, existing or not
    assert Workspace(a).rebase(str(sheet_a)) == sheet_a


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


def test_show_itself_prints_the_relocated_sheet_not_the_stored_one(tmp_path):
    """The actual ``show`` command also rebases its displayed contact sheet."""
    from codeverse3d.cli.layout_cmd import print_evidence
    from codeverse3d.workspace import Workspace

    a, b, _sheet, rec = _moved_run(tmp_path)
    out = _captured(lambda: print_evidence(Workspace(b), rec))
    assert str(b / "artifacts" / "renders" / "r01" / "sheet.png") in out
    assert str(a) not in out, "`3dcode show` must not print a path under the old root"
