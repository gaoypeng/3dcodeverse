"""The three-bucket run layout (docs/RUN_LAYOUT.md): directories, aliases,
.gitignore and the additive record blocks.  Pure filesystem + pydantic."""

from __future__ import annotations

import json
import os
from pathlib import Path

from codeverse.contracts.run import RunDeliverable, RunRecord, RunTelemetry
from codeverse.contracts.spec import Spec
from codeverse.workspace import EVIDENCE_DIR, LAYOUT_ALIASES, Workspace


def test_create_makes_the_three_buckets(tmp_path: Path):
    ws = Workspace(tmp_path / "run").create()
    assert ws.deliverable.is_dir() and ws.telemetry.is_dir() and ws.artifacts.is_dir()
    # evidence/ is the alias, artifacts/ the physical home (see docs/RUN_LAYOUT.md)
    evidence = ws.root / EVIDENCE_DIR
    assert evidence.is_symlink() and os.readlink(evidence) == "artifacts"
    assert evidence.resolve() == ws.artifacts.resolve()
    assert ws.cost_path.parent == ws.telemetry and ws.usage_path.name == "usage.jsonl"


def test_telemetry_aliases_point_at_the_root_files(tmp_path: Path):
    ws = Workspace(tmp_path / "run").create()
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


def test_gitignore_covers_the_derived_buckets(tmp_path: Path):
    ws = Workspace(tmp_path / "run").create()
    text = (ws.root / ".gitignore").read_text()
    for line in ("artifacts/", "deliverable/", "telemetry/", "evidence", "trajectories/"):
        assert line in text.splitlines()
    assert ws.ensure_gitignore() is False  # idempotent
    (ws.root / ".gitignore").write_text("src/nothing\n")
    assert ws.ensure_gitignore(dry_run=True) is True
    assert (ws.root / ".gitignore").read_text() == "src/nothing\n"
    assert ws.ensure_gitignore() is True
    assert "deliverable/" in (ws.root / ".gitignore").read_text()


def test_record_blocks_are_additive(tmp_path: Path):
    """An old record.json (no telemetry / deliverable) must still validate."""
    spec = Spec(id="x", track="static_object", language="blender", prompt="a chair")
    old = {"spec": spec.model_dump(mode="json"), "workspace": str(tmp_path), "status": "passed"}
    rec = RunRecord.model_validate(old)
    assert rec.telemetry is None and rec.deliverable is None
    rec.telemetry = RunTelemetry()
    rec.deliverable = RunDeliverable(best_round=1, commit="abc")
    again = RunRecord.model_validate_json(rec.model_dump_json())
    assert again.deliverable is not None and again.deliverable.best_round == 1
    assert again.telemetry is not None and again.telemetry.schema_version == 1


# --------------------------------------------------------------------------- relocation
def _moved_run(tmp_path: Path):
    """Create a record whose absolute sheet path predates a workspace move."""
    import shutil

    from codeverse.contracts.artifacts import RenderSet
    from codeverse.contracts.common import Backends, Language, Track
    from codeverse.contracts.run import RoundRecord, RunStatus

    a = tmp_path / "A" / "run1"
    (a / "artifacts" / "renders" / "r01").mkdir(parents=True)
    sheet_a = a / "artifacts" / "renders" / "r01" / "sheet.png"
    sheet_a.write_bytes(b"PNG")
    spec = Spec(id="run1", track=Track.STATIC_OBJECT, language=Language.BLENDER,
                prompt="a wooden chair", backends=Backends(generator="gemini-cli:gemini-3.6-flash"))
    rec = RunRecord(spec=spec, workspace=str(a), status=RunStatus.PASSED, best_round=0,
                    rounds=[RoundRecord(index=0, kind="generate",
                                        renders=RenderSet(views=[], contact_sheet=str(sheet_a)))])
    b = tmp_path / "B" / "run1"
    b.parent.mkdir(parents=True)
    shutil.move(str(a), str(b))
    return a, b, sheet_a, rec


def _captured(render) -> str:
    """rich's Console holds its own stream, so capsys never sees it; widen it too so the
    panel cannot wrap the path under assertion."""
    from codeverse.cli._common import console

    old_width, console.width = console.width, 400
    try:
        with console.capture() as cap:
            render()
    finally:
        console.width = old_width
    return cap.get()


def test_a_relocated_run_still_resolves_its_stored_paths(tmp_path):
    """Stored absolute paths rebase after moving or archiving a run."""
    from codeverse.workspace import Workspace

    _a, b, sheet_a, _rec = _moved_run(tmp_path)
    stored = str(sheet_a)  # what the writer puts in record.json
    ws = Workspace(b)
    got = ws.rebase(stored)
    assert got.exists(), f"a moved run must still find its own sheet, got {got}"
    assert got == b / "artifacts" / "renders" / "r01" / "sheet.png"
    assert Path(stored) != got

    # an absolute path that IS still valid is left alone
    assert ws.rebase(str(got)) == got
    # a relative path joins the root (the only case the old code handled)
    assert ws.rebase("artifacts/renders/r01/sheet.png") == got
    # nothing matches -> the original comes back, so the caller reports a real missing
    # file instead of a silently wrong one
    missing = tmp_path / "elsewhere" / "nope.png"
    assert ws.rebase(str(missing)) == missing


def test_a_copied_run_reads_its_own_files_while_the_original_still_exists(tmp_path):
    """rebase returned any stored absolute path that still EXISTED as-is, so a
    copied/rsynced run silently read — and a retexture wrote past — the ORIGINAL's
    files for as long as that directory lived (V10f).  This root's copy must win."""
    import shutil

    from codeverse.workspace import Workspace

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


def test_show_prints_the_sheet_that_exists_after_a_move(tmp_path):
    """The user-visible half: `3dcv show` must not print a path that is not there."""
    from codeverse.cli._common import print_record_summary
    from codeverse.workspace import Workspace

    _a, b, _sheet, rec = _moved_run(tmp_path)
    printed = _captured(lambda: print_record_summary(rec, Workspace(b).root))
    line = next(ln for ln in printed.splitlines() if "sheet:" in ln)
    shown = line.split("sheet:", 1)[1].strip().rstrip("│ ").strip()
    assert shown.endswith("sheet.png"), f"the panel wrapped the path: {line!r}"
    assert Path(shown).exists(), f"show printed a sheet that is not there: {shown!r}"


def test_a_stale_index_lock_names_the_remedy(tmp_path):
    """A stale git index lock is surfaced with its exact safe remedy."""
    import subprocess

    import pytest

    from codeverse.workspace import Workspace, WorkspaceGitError

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
    from codeverse.cli.layout_cmd import print_evidence
    from codeverse.workspace import Workspace

    a, b, _sheet, rec = _moved_run(tmp_path)
    out = _captured(lambda: print_evidence(Workspace(b), rec))
    assert str(b / "artifacts" / "renders" / "r01" / "sheet.png") in out
    assert str(a) not in out, "`3dcv show` must not print a path under the old root"
