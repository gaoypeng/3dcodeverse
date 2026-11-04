"""deliverable/ (addons/select.package) + telemetry/ (record.package_run), `3dcode show`, and
export / gallery on BOTH layouts (old runs must keep working)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from codeverse3d.addons import select
from codeverse3d.cli.main import app
from codeverse3d.cost.types import STAGE_ORDER
from codeverse3d.record.deliverable import build_deliverable, deliverable_path, load_deliverable
from codeverse3d.record.record import load_record, package_run
from codeverse3d.record.telemetry import (
    build_telemetry,
    ledger_rows,
    load_telemetry,
)

runner = CliRunner()


# --------------------------------------------------------------------------- packaging
def test_package_run_builds_a_stable_independent_and_loadable_handover(fake_run):
    ws, rec = fake_run
    assert load_telemetry(ws, rec) is None and load_deliverable(ws) is None
    assert deliverable_path(ws, "object.glb") == ws.artifacts / "object.glb"
    # Cross the old hard-link fast-path threshold before packaging.
    (ws.artifacts / "object.glb").write_bytes(b"glTF" + b"\0" * (300 * 1024))
    package_run(ws, rec)
    assert rec.telemetry is not None and load_deliverable(ws) is None  # the run packages no round
    select.package(ws.root, 1)
    d = load_deliverable(ws)
    assert d is not None
    roles = {f.role for f in d.files}
    assert {"code", "model", "sheet"} <= roles and "manifest" not in roles   # it cannot carry its own hash
    assert load_deliverable(ws) == d and d.total_bytes == sum(f.bytes for f in d.files)
    assert d.round == 1 and d.entry == "deliverable/src/model.py"
    assert (ws.deliverable / "src" / "model.py").read_text().startswith("# round 1")
    cost = json.loads(ws.cost_path.read_text())
    assert cost["total_usd"] == rec.total_usage.cost_usd
    assert cost["by_round"]
    settings = json.loads(ws.settings_path.read_text())
    assert {r["role"] for r in settings["roles"]} == {"planner", "generator", "judge", "captioner"}
    assert deliverable_path(ws, "object.glb") == ws.deliverable / "object.glb"
    assert deliverable_path(ws, "nope.glb") is None

    # Rebuilding unchanged content is byte-for-byte stable.
    second = build_deliverable(ws, rec, 1)
    assert d.generated_at == second.generated_at  # unchanged content → no diff
    assert [(f.path, f.sha256) for f in d.files] == [(f.path, f.sha256) for f in second.files]

    # Package metadata can be recovered from files even before the caller saves rec.
    bare = load_record(ws).model_copy(update={"telemetry": None})
    assert load_telemetry(ws, bare) is not None and load_deliverable(ws) is not None

    # Delivered assets are copies: editing a hand-over cannot mutate evidence.
    delivered = ws.deliverable / "object.glb"
    assert delivered.stat().st_nlink == 1 and (ws.artifacts / "object.glb").stat().st_nlink == 1
    delivered.write_bytes(b"user edited the hand-over copy")
    assert (ws.artifacts / "object.glb").read_bytes().startswith(b"glTF")


# --------------------------------------------------------------------------- show
def _show(slug: str, runs_dir: Path, *args: str):
    return runner.invoke(app, ["show", slug, "--runs-dir", str(runs_dir), *args])


def test_show_sections_and_status_share_the_packaged_run(fake_run):
    ws, rec = fake_run
    package_run(ws, rec)
    select.package(ws.root, 1)
    r = _show(ws.root.name, ws.root.parent)
    assert r.exit_code == 0, r.output
    out = r.output
    assert "DELIVERABLE" in out and "QUALITY EVIDENCE" in out and "COST & SETTINGS" in out
    assert out.index("DELIVERABLE") < out.index("QUALITY EVIDENCE") < out.index("COST & SETTINGS")
    assert "deliverable/object.glb" in out and "deliverable/src/model.py" in out
    assert "static_object" in out                       # evidence: rubric
    assert "the ledger, list price" in out and "models per role" in out and "price table" in out
    assert "gemini-cli" in out and "generator" in out      # settings: model id per role

    # A selected section stays isolated, and invalid selectors fail cleanly.
    r = _show(ws.root.name, ws.root.parent, "--section", "cost")
    assert r.exit_code == 0 and "COST & SETTINGS" in r.output and "DELIVERABLE" not in r.output
    assert _show(ws.root.name, ws.root.parent, "--section", "bogus").exit_code == 1

    # `status` is `show --section status`: the run and its rounds, nothing from the other sections;
    # `show` opens with that same section.
    ws.write_json(ws.record_path, rec)
    r = runner.invoke(app, ["status", ws.root.name, "--runs-dir", str(ws.root.parent)])
    assert r.exit_code == 0, r.output
    assert "STATUS" in r.output and "rounds" in r.output and "passed" in r.output and "DELIVERABLE" not in r.output
    assert out.index("STATUS") < out.index("DELIVERABLE")


def test_telemetry_summarises_the_runs_own_ledger(fake_run):
    ws, rec = fake_run
    ledger = ws.root / "telemetry" / "cost.jsonl"
    ledger.unlink()
    assert ledger_rows(ws) == []  # a run with no ledger has no rows (nothing is reconstructed)
    tele = build_telemetry(ws, rec, write=False)
    assert tele.cost is not None and tele.cost.n_calls == 0 and tele.settings is not None
    ledger.write_text(json.dumps({"stage": "plan", "role": "planner", "label": "plan", "cost_usd": 0.5,
                                  "model": "gemini-3.8-flash", "n_calls": 1}) + "\n")
    tele = build_telemetry(ws, rec)
    assert tele.cost is not None and tele.cost.by_stage[0].stage == "plan"
    assert {s.stage for s in tele.cost.by_stage} <= set(STAGE_ORDER)
    assert not (ws.telemetry / "usage.jsonl").exists(), "one ledger, one name"
    # a degraded verdict is unscored in the per-round cost table, never a 0.0
    rec.rounds[1].judgment = rec.rounds[1].judgment.model_copy(update={"summary": "judge_error: outage"})
    by_round = build_telemetry(ws, rec, write=False).cost.by_round
    assert [r["score"] for r in by_round] == [rec.rounds[0].judgment.overall, None]
