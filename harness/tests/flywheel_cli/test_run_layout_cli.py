"""deliverable/ + telemetry/ packaging, `3dcv show`, `3dcv migrate-runs`, and
export / gallery on BOTH layouts (old runs must keep working)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from codeverse.cli.main import app
from codeverse.contracts.run import RunRecord
from codeverse.flywheel.deliverable import build_deliverable, deliverable_path, load_deliverable
from codeverse.flywheel.export import export_samples
from codeverse.flywheel.migrate import MIGRATED, UP_TO_DATE, migrate_run, migrate_runs
from codeverse.flywheel.record import load_record, package_run
from codeverse.flywheel.telemetry import (
    build_telemetry,
    ledger_rows,
    live_ledger_path,
    load_telemetry,
    stage_order,
)
from codeverse.gallery import build_static
from codeverse.gallery.index import entry_from_record
from codeverse.workspace import LAYOUT_ALIASES, Workspace

from .conftest import make_fake_run

runner = CliRunner()


def strip_layout(ws: Workspace) -> Workspace:
    """Turn a freshly created workspace back into a pre-2026-08-23 run directory."""
    for name, _ in LAYOUT_ALIASES:
        p = ws.root / name
        if p.is_symlink():
            p.unlink()
    for d in (ws.deliverable, ws.telemetry):
        shutil.rmtree(d, ignore_errors=True)
    rec = json.loads(ws.record_path.read_text())
    rec.pop("telemetry", None)
    rec.pop("deliverable", None)
    ws.record_path.write_text(json.dumps(rec, indent=2))
    return ws


def old_layout_run(tmp_path: Path, slug: str = "wooden_chair_ab12cd34") -> tuple[Workspace, RunRecord]:
    ws, rec = make_fake_run(tmp_path / "runs", slug)
    strip_layout(ws)
    return ws, load_record(ws)


# --------------------------------------------------------------------------- packaging
def test_package_run_builds_deliverable_and_telemetry(fake_run):
    ws, rec = fake_run
    package_run(ws, rec)
    assert rec.deliverable is not None and rec.telemetry is not None
    d = rec.deliverable
    roles = {f.role for f in d.files}
    assert {"code", "model", "sheet", "manifest"} <= roles
    assert d.best_round == 1 and d.code_source == "commit"
    assert d.entry == "deliverable/src/model.py"
    assert (ws.deliverable / "src" / "model.py").read_text().startswith("# round 1")
    assert (ws.deliverable / "object.glb").is_file() and (ws.deliverable / "sheet.png").is_file()
    for f in d.files:
        assert (ws.root / f.path).is_file() and f.bytes > 0 and len(f.sha256) == 64
    assert ws.settings_path.is_file() and ws.cost_path.is_file() and ws.usage_path.is_file()
    cost = json.loads(ws.cost_path.read_text())
    assert cost["total_usd"] == rec.total_usage.cost_usd
    assert cost["by_round"] and cost["budget_usd"] == rec.spec.budget.max_usd
    settings = json.loads(ws.settings_path.read_text())
    assert {r["role"] for r in settings["roles"]} == {"planner", "generator", "judge", "captioner"}
    assert settings["rubric"] and settings["rubric_hash"] and settings["price_table_version"]


def test_deliverable_rebuild_is_idempotent(fake_run):
    ws, rec = fake_run
    first = build_deliverable(ws, rec)
    files_before = sorted(p.relative_to(ws.deliverable).as_posix() for p in ws.deliverable.rglob("*") if p.is_file())
    second = build_deliverable(ws, rec)
    files_after = sorted(p.relative_to(ws.deliverable).as_posix() for p in ws.deliverable.rglob("*") if p.is_file())
    assert files_before == files_after
    assert first.generated_at == second.generated_at  # unchanged content → no diff
    assert [(f.path, f.sha256) for f in first.files] == [(f.path, f.sha256) for f in second.files]


def test_deliverable_path_prefers_deliverable_then_artifacts(fake_run):
    ws, rec = fake_run
    assert deliverable_path(ws, "object.glb") == ws.artifacts / "object.glb"
    build_deliverable(ws, rec)
    assert deliverable_path(ws, "object.glb") == ws.deliverable / "object.glb"
    assert deliverable_path(ws, "nope.glb") is None


def test_cost_summary_reconciles_with_the_record_total(fake_run):
    """The rows come from codeverse.cost (one ledger in the harness); the summary is
    the run-layout view of them and must always add up to record.total_usage."""
    ws, rec = fake_run
    rows, source = ledger_rows(ws)
    assert source == "reconstructed"  # no live cost_ledger.jsonl in this run
    assert rows and all("stage" in r and "cost_usd" in r for r in rows)
    tele = build_telemetry(ws, rec, write=False)
    cost = tele.cost
    assert cost is not None
    assert cost.total_usd == rec.total_usage.cost_usd
    assert abs(cost.ledger_usd - cost.total_usd) < 1e-9
    assert cost.by_role["judge"] > 0 and cost.unattributed_usd > 0  # judge verdicts + the residual
    assert {s.stage for s in cost.by_stage} <= set(stage_order())
    assert cost.budget_usd == rec.spec.budget.max_usd


def test_usage_rows_alias_a_live_ledger_instead_of_copying_it(fake_run):
    ws, rec = fake_run
    live = live_ledger_path(ws)
    live.write_text(json.dumps({"stage": "plan", "role": "planner", "label": "plan", "cost_usd": 0.5,
                                "model": "gemini-3.7-flash", "n_calls": 1}) + "\n")
    tele = build_telemetry(ws, rec)
    assert tele.files["usage_source"] == "live"
    assert ws.usage_path.is_symlink() and ws.usage_path.resolve() == live.resolve()
    assert tele.cost is not None and tele.cost.by_stage[0].stage == "plan"


def test_telemetry_degrades_when_the_cost_package_is_unavailable(fake_run, monkeypatch):
    ws, rec = fake_run
    import codeverse.flywheel.telemetry as T

    def boom(*_a, **_k):
        raise ImportError("codeverse.cost is not installed")

    monkeypatch.setattr(T, "live_ledger_path", boom)
    rows, source = T.ledger_rows(ws)
    assert rows == [] and source == "unavailable"
    tele = T.build_telemetry(ws, rec, write=False)
    assert tele.cost is not None and tele.cost.total_usd == rec.total_usage.cost_usd
    assert tele.cost.n_calls == 0 and tele.settings is not None  # settings never depend on the ledger


# --------------------------------------------------------------------------- migration
def test_migrate_run_is_additive_and_idempotent(tmp_path: Path):
    ws, _ = old_layout_run(tmp_path)
    evidence_before = sorted(p.relative_to(ws.root).as_posix() for p in ws.artifacts.rglob("*"))
    src_before = (ws.src / "model.py").read_bytes()

    plan = migrate_run(ws, dry_run=True)
    assert plan.status == MIGRATED and plan.layout
    assert not ws.deliverable.exists() and not ws.telemetry.exists()

    first = migrate_run(ws)
    assert first.status == MIGRATED and first.record_updated
    assert ws.deliverable.is_dir() and ws.cost_path.is_file() and ws.evidence.is_symlink()
    record = load_record(ws)
    assert record.deliverable is not None and record.telemetry is not None

    before = ws.record_path.read_bytes()
    second = migrate_run(ws)
    assert second.status == UP_TO_DATE and not second.record_updated
    assert ws.record_path.read_bytes() == before
    # nothing was moved or deleted
    assert sorted(p.relative_to(ws.root).as_posix() for p in ws.artifacts.rglob("*")) == evidence_before
    assert (ws.src / "model.py").read_bytes() == src_before


def test_migrate_runs_batch_and_partial_runs(tmp_path: Path):
    runs = tmp_path / "runs"
    ws_a, _ = make_fake_run(runs, "run_a")
    strip_layout(ws_a)
    ws_b, _ = make_fake_run(runs, "run_b")
    strip_layout(ws_b)
    ws_b.record_path.unlink()  # interrupted run: layout only, no packaging
    (runs / "not_a_run").mkdir()
    rep = migrate_runs(runs)
    assert rep.n_runs == 2 and rep.n_failed == 0  # not_a_run has no spec.json
    by_run = {Path(m.run).name: m for m in rep.runs}
    assert by_run["run_a"].deliverable_files and by_run["run_a"].telemetry_rows is not None
    assert by_run["run_b"].status == MIGRATED and "record.json" in by_run["run_b"].reason
    assert (ws_b.telemetry).is_dir() and not (ws_b.deliverable / "manifest.json").exists()
    assert migrate_runs(runs).n_migrated == 0


def test_migrate_runs_cli(tmp_path: Path):
    ws, _ = old_layout_run(tmp_path)
    r = runner.invoke(app, ["migrate-runs", str(ws.root.parent), "--dry-run"])
    assert r.exit_code == 0 and "would migrate" in r.output
    assert not ws.deliverable.exists()
    r = runner.invoke(app, ["migrate-runs", str(ws.root)])  # a single run directory works too
    assert r.exit_code == 0 and "migrated" in r.output
    assert ws.deliverable_manifest_path.is_file()


# --------------------------------------------------------------------------- show
def _show(slug: str, runs_dir: Path, *args: str):
    return runner.invoke(app, ["show", slug, "--runs-dir", str(runs_dir), *args])


def test_show_prints_three_separated_sections(fake_run):
    ws, rec = fake_run
    package_run(ws, rec)
    r = _show(ws.root.name, ws.root.parent)
    assert r.exit_code == 0, r.output
    out = r.output
    assert "DELIVERABLE" in out and "QUALITY EVIDENCE" in out and "COST & SETTINGS" in out
    assert out.index("DELIVERABLE") < out.index("QUALITY EVIDENCE") < out.index("COST & SETTINGS")
    assert "deliverable/object.glb" in out and "deliverable/src/model.py" in out
    assert "static_object_v1" in out                       # evidence: rubric
    assert "spent / budget" in out and "models per role" in out and "price table" in out
    assert "api-agent" in out and "generator" in out       # settings: model id per role


def test_show_sections_can_be_selected(fake_run):
    ws, rec = fake_run
    package_run(ws, rec)
    r = _show(ws.root.name, ws.root.parent, "--section", "cost")
    assert r.exit_code == 0 and "COST & SETTINGS" in r.output and "DELIVERABLE" not in r.output
    assert _show(ws.root.name, ws.root.parent, "--section", "bogus").exit_code == 1


def test_status_still_works_and_points_at_show(fake_run):
    """`3dcv status` keeps its own (unchanged) output and links to the new view."""
    ws, rec = fake_run
    package_run(ws, rec)
    ws.write_json(ws.record_path, rec)
    r = runner.invoke(app, ["status", ws.root.name, "--runs-dir", str(ws.root.parent)])
    assert r.exit_code == 0, r.output
    assert "rounds" in r.output and "passed" in r.output
    assert f"3dcv show {ws.root.name}" in r.output


def test_show_works_on_an_old_layout_run(tmp_path: Path):
    ws, _ = old_layout_run(tmp_path)
    r = _show(ws.root.name, ws.root.parent)
    assert r.exit_code == 0, r.output
    assert "old layout" in r.output and "computed on the fly" in r.output
    assert "COST & SETTINGS" in r.output and "models per role" in r.output
    assert not ws.telemetry.exists()  # show never writes


# --------------------------------------------------------------------------- export / gallery on both layouts
def test_export_and_gallery_on_both_layouts(tmp_path: Path):
    runs = tmp_path / "runs"
    ws_new, rec_new = make_fake_run(runs, "run_new")
    package_run(ws_new, rec_new)
    ws_new.write_json(ws_new.record_path, rec_new)
    ws_old, _ = make_fake_run(runs, "run_old")
    strip_layout(ws_old)

    rep = export_samples(runs, tmp_path / "ds")
    assert rep.n_exported == 2 and not rep.skipped, rep.skipped
    metas = {p.parent.name: json.loads(p.read_text()) for p in (tmp_path / "ds").rglob("meta.json")}
    assert metas["run_new"]["telemetry"]["total_usd"] == rec_new.total_usage.cost_usd
    assert metas["run_new"]["telemetry"]["models"]["judge"]
    assert metas["run_old"]["telemetry"] == {}
    for meta in metas.values():
        assert meta["code_source"] == "commit" and "renders/sheet.png" in meta["renders"]

    out_html = tmp_path / "g.html"
    path, n, _ = build_static([runs], out_html)
    assert n == 2 and "deliverable" in path.read_text()
    new_item = entry_from_record("runs", ws_new, load_record(ws_new))
    old_item = entry_from_record("runs", ws_old, load_record(ws_old))
    assert new_item.cost_by_stage == {} or isinstance(new_item.cost_by_stage, dict)
    new_links = {ln.label: ln.rel for ln in new_item.links}
    old_links = {ln.label: ln.rel for ln in old_item.links}
    assert old_links["glb"] == "artifacts/object.glb"
    assert new_links["glb"] == "deliverable/object.glb"


def test_export_falls_back_to_the_packaged_snapshot_without_git(tmp_path: Path):
    runs = tmp_path / "runs"
    ws, rec = make_fake_run(runs, "run_new")
    package_run(ws, rec)
    ws.write_json(ws.record_path, rec)
    shutil.rmtree(ws.root / ".git")  # a run copied without its history
    rep = export_samples(runs, tmp_path / "ds")
    assert rep.n_exported == 1, rep.skipped
    meta = json.loads(next((tmp_path / "ds").rglob("meta.json")).read_text())
    assert meta["code_source"] == "deliverable"
    assert (next((tmp_path / "ds").rglob("meta.json")).parent / "src" / "model.py").is_file()


def test_load_helpers_fall_back_to_files_then_none(fake_run):
    ws, rec = fake_run
    assert load_telemetry(ws, rec) is None and load_deliverable(ws, rec) is None
    package_run(ws, rec)
    bare = load_record(ws)  # record.json on disk has no blocks yet (package_run only mutates in memory)
    assert bare.telemetry is None
    assert load_telemetry(ws, bare) is not None and load_deliverable(ws, bare) is not None
