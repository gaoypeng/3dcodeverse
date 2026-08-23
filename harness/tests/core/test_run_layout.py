"""The three-bucket run layout (docs/RUN_LAYOUT.md): directories, aliases,
.gitignore and the additive record blocks.  Pure filesystem + pydantic."""

from __future__ import annotations

import json
import os
from pathlib import Path

from codeverse.contracts.run import RunDeliverable, RunRecord, RunTelemetry
from codeverse.contracts.spec import Spec
from codeverse.workspace import LAYOUT_ALIASES, Workspace


def test_create_makes_the_three_buckets(tmp_path: Path):
    ws = Workspace(tmp_path / "run").create()
    assert ws.deliverable.is_dir() and ws.telemetry.is_dir() and ws.artifacts.is_dir()
    # evidence/ is the alias, artifacts/ the physical home (see docs/RUN_LAYOUT.md)
    assert ws.evidence.is_symlink() and os.readlink(ws.evidence) == "artifacts"
    assert ws.evidence.resolve() == ws.artifacts.resolve()
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
