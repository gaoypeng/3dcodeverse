"""deliverable/ (addons/select.package) + telemetry/ (record.package_run), `3dcode show`, and
export / gallery on BOTH layouts (old runs must keep working)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from codeverse3d.addons import select
from codeverse3d.addons.dataset.export import export_samples
from codeverse3d.addons.gallery import build_static
from codeverse3d.addons.gallery.index import entry_from_record
from codeverse3d.cli.main import app
from codeverse3d.contracts.run import RunRecord
from codeverse3d.record.deliverable import build_deliverable, deliverable_path, load_deliverable
from codeverse3d.record.record import load_record, package_run
from codeverse3d.record.telemetry import (
    build_telemetry,
    ledger_rows,
    live_ledger_path,
    load_telemetry,
    stage_order,
)
from codeverse3d.workspace import LAYOUT_ALIASES, Workspace

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
    # the manifest does NOT list itself: no file can carry its own hash, and the
    # returned object used to have one more file (and a smaller total) than the disk
    assert {"code", "model", "sheet"} <= roles and "manifest" not in roles
    assert load_deliverable(ws) == d and d.total_bytes == sum(f.bytes for f in d.files)
    assert d.round == 1 and d.code_source == "commit"
    assert d.entry == "deliverable/src/model.py"
    assert (ws.deliverable / "src" / "model.py").read_text().startswith("# round 1")
    assert (ws.deliverable / "object.glb").is_file() and (ws.deliverable / "sheet.png").is_file()
    for f in d.files:
        assert (ws.root / f.path).is_file() and f.bytes > 0 and len(f.sha256) == 64
    assert ws.settings_path.is_file() and ws.cost_path.is_file() and ws.usage_path.is_file()
    cost = json.loads(ws.cost_path.read_text())
    assert cost["total_usd"] == rec.total_usage.cost_usd
    assert cost["by_round"]
    settings = json.loads(ws.settings_path.read_text())
    assert {r["role"] for r in settings["roles"]} == {"planner", "generator", "judge", "captioner"}
    assert settings["rubric"] and settings["rubric_hash"] and settings["price_table_version"]
    assert deliverable_path(ws, "object.glb") == ws.deliverable / "object.glb"
    assert deliverable_path(ws, "nope.glb") is None

    # Rebuilding unchanged content is byte-for-byte stable.
    first = d
    files_before = sorted(p.relative_to(ws.deliverable).as_posix() for p in ws.deliverable.rglob("*") if p.is_file())
    second = build_deliverable(ws, rec, 1)
    files_after = sorted(p.relative_to(ws.deliverable).as_posix() for p in ws.deliverable.rglob("*") if p.is_file())
    assert files_before == files_after
    assert first.generated_at == second.generated_at  # unchanged content → no diff
    assert [(f.path, f.sha256) for f in first.files] == [(f.path, f.sha256) for f in second.files]

    # Package metadata can be recovered from files even before the caller saves rec.
    bare = load_record(ws)
    assert bare.telemetry is None
    assert load_telemetry(ws, bare) is not None and load_deliverable(ws) is not None

    # Delivered assets are copies: editing a hand-over cannot mutate evidence.
    delivered = ws.deliverable / "object.glb"
    assert delivered.stat().st_nlink == 1 and (ws.artifacts / "object.glb").stat().st_nlink == 1
    delivered.write_bytes(b"user edited the hand-over copy")
    assert (ws.artifacts / "object.glb").read_bytes().startswith(b"glTF")


def test_telemetry_handles_reconstructed_live_and_unavailable_ledgers(fake_run, monkeypatch):
    ws, rec = fake_run
    # Historic records reconstruct rows and reconcile them to the recorded total.
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

    # A live ledger is aliased, not copied.
    live = live_ledger_path(ws)
    live.write_text(json.dumps({"stage": "plan", "role": "planner", "label": "plan", "cost_usd": 0.5,
                                "model": "gemini-3.7-flash", "n_calls": 1}) + "\n")
    tele = build_telemetry(ws, rec)
    assert tele.files["usage_source"] == "live"
    assert ws.usage_path.is_symlink() and ws.usage_path.resolve() == live.resolve()
    assert tele.cost is not None and tele.cost.by_stage[0].stage == "plan"

    # A core-only install still emits settings and the record-level total.
    import codeverse3d.record.telemetry as T

    def boom(*_a, **_k):
        raise ImportError("codeverse3d.cost is not installed")

    monkeypatch.setattr(T, "live_ledger_path", boom)
    rows, source = T.ledger_rows(ws)
    assert rows == [] and source == "unavailable"
    tele = T.build_telemetry(ws, rec, write=False)
    assert tele.cost is not None and tele.cost.total_usd == rec.total_usage.cost_usd
    assert tele.cost.n_calls == 0 and tele.settings is not None  # settings never depend on the ledger


def test_the_settings_snapshot_records_the_temperature_the_track_planned_at(tmp_path):
    """The planner row reported ``planner.plan()``'s default 0.4 for every run — a default
    no track uses: each passes its own ``plan_temperature``, and graphics plans at 0.5."""
    from codeverse3d.contracts.common import Language
    from codeverse3d.record.telemetry import settings_snapshot

    for language, want in ((Language.GLSL_SHADER, 0.5), (Language.BLENDER, 0.4)):
        _ws, rec = make_fake_run(tmp_path / language.value, language=language)
        planner = next(r for r in settings_snapshot(rec).roles if r.role == "planner")
        assert (planner.temperature, planner.source) == (want, "default"), language


def test_rejected_texture_pass_is_not_delivered_or_linked(fake_run):
    """Belt and braces: a stray canonical object_textured.glb from a rejected pass
    is skipped by the deliverable AND by the gallery links (both gate on shipped)."""
    ws, rec = fake_run
    (ws.artifacts / "object_textured.glb").write_bytes(b"glTF\x02" + b"\0" * 16)
    rec.extra["texturing"] = {"shipped": False, "glb_textured": "artifacts/object_textured.glb"}
    build_deliverable(ws, rec, 1)
    assert not (ws.deliverable / "object_textured.glb").exists()
    entry = entry_from_record("runs", ws, rec)
    assert "textured glb" not in {ln.label for ln in entry.links}
    # ... and a SHIPPED pass is delivered and linked
    rec.extra["texturing"] = {"shipped": True, "glb_textured": "artifacts/object_textured.glb"}
    build_deliverable(ws, rec, 1)
    assert (ws.deliverable / "object_textured.glb").is_file()
    entry = entry_from_record("runs", ws, rec)
    assert "textured glb" in {ln.label for ln in entry.links}


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
    assert "static_object_v1" in out                       # evidence: rubric
    assert "spent / budget" in out and "models per role" in out and "price table" in out
    assert "gemini-cli" in out and "generator" in out      # settings: model id per role

    # A selected section stays isolated, and invalid selectors fail cleanly.
    r = _show(ws.root.name, ws.root.parent, "--section", "cost")
    assert r.exit_code == 0 and "COST & SETTINGS" in r.output and "DELIVERABLE" not in r.output
    assert _show(ws.root.name, ws.root.parent, "--section", "bogus").exit_code == 1

    # The legacy status view stays available and points to the richer view.
    ws.write_json(ws.record_path, rec)
    r = runner.invoke(app, ["status", ws.root.name, "--runs-dir", str(ws.root.parent)])
    assert r.exit_code == 0, r.output
    assert "rounds" in r.output and "passed" in r.output
    assert f"3dcode show {ws.root.name}" in r.output


def test_show_works_on_an_old_layout_run(tmp_path: Path):
    ws, _ = old_layout_run(tmp_path)
    r = _show(ws.root.name, ws.root.parent)
    assert r.exit_code == 0, r.output
    assert "no deliverable/ yet" in r.output and "computed on the fly" in r.output
    assert "COST & SETTINGS" in r.output and "models per role" in r.output
    assert not ws.telemetry.exists()  # show never writes


# --------------------------------------------------------------------------- export / gallery on both layouts
def test_export_and_gallery_on_both_layouts(tmp_path: Path):
    runs = tmp_path / "runs"
    ws_new, rec_new = make_fake_run(runs, "run_new")
    package_run(ws_new, rec_new)
    ws_new.write_json(ws_new.record_path, rec_new)
    select.package(ws_new.root, 1)
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
    assert new_item.cost_by_stage and old_item.cost_by_stage == {}
    new_links = {ln.label: ln.rel for ln in new_item.links}
    old_links = {ln.label: ln.rel for ln in old_item.links}
    assert old_links["glb"] == "artifacts/object.glb"
    assert new_links["glb"] == "deliverable/object.glb"


def test_export_falls_back_to_the_packaged_snapshot_without_git(tmp_path: Path):
    runs = tmp_path / "runs"
    ws, rec = make_fake_run(runs, "run_new")
    package_run(ws, rec)
    ws.write_json(ws.record_path, rec)
    select.package(ws.root, 1)
    shutil.rmtree(ws.root / ".git")  # a run copied without its history
    rep = export_samples(runs, tmp_path / "ds")
    assert rep.n_exported == 1, rep.skipped
    meta = json.loads(next((tmp_path / "ds").rglob("meta.json")).read_text())
    assert meta["code_source"] == "deliverable"
    assert (next((tmp_path / "ds").rglob("meta.json")).parent / "src" / "model.py").is_file()
