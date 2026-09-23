"""deliverable/ (addons/select.package) + telemetry/ (record.package_run), `3dcode show`, and
export / gallery on BOTH layouts (old runs must keep working)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from codeverse3d.addons import select
from codeverse3d.addons.dataset.export import export_samples
from codeverse3d.addons.gallery.index import entry_from_record
from codeverse3d.addons.gallery.page import build_static
from codeverse3d.cli.main import app
from codeverse3d.contracts.run import RunRecord
from codeverse3d.cost.types import STAGE_ORDER
from codeverse3d.record.deliverable import build_deliverable, deliverable_path, load_deliverable
from codeverse3d.record.record import load_record, package_run
from codeverse3d.record.telemetry import (
    build_telemetry,
    ledger_rows,
    load_telemetry,
)
from codeverse3d.workspace import LAYOUT_ALIASES, Workspace

from .conftest import make_fake_run

runner = CliRunner()


def strip_layout(ws: Workspace) -> Workspace:
    """Turn a freshly created workspace back into a pre-2026-08-23 run directory: no
    telemetry/ or deliverable/, no per-round builds — only the canonical one, of the best
    round its record.json names."""
    for name, _ in LAYOUT_ALIASES:
        p = ws.root / name
        if p.is_symlink():
            p.unlink()
    for d in (ws.deliverable, ws.telemetry, *ws.artifacts.glob("r[0-9][0-9]")):
        shutil.rmtree(d, ignore_errors=True)
    rec = json.loads(ws.record_path.read_text())
    rec.pop("telemetry", None)
    rec.pop("deliverable", None)
    rec["best_round"] = max((r for r in rec["rounds"] if r.get("judgment")), key=lambda r: r["judgment"]["overall"])["index"]
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
    bare = load_record(ws)
    assert bare.telemetry is None
    assert load_telemetry(ws, bare) is not None and load_deliverable(ws) is not None

    # Delivered assets are copies: editing a hand-over cannot mutate evidence.
    delivered = ws.deliverable / "object.glb"
    assert delivered.stat().st_nlink == 1 and (ws.artifacts / "object.glb").stat().st_nlink == 1
    delivered.write_bytes(b"user edited the hand-over copy")
    assert (ws.artifacts / "object.glb").read_bytes().startswith(b"glTF")


def test_telemetry_summarises_the_runs_own_ledger(fake_run):
    ws, rec = fake_run
    ledger = ws.root / "telemetry" / "cost.jsonl"
    ledger.unlink()
    assert ledger_rows(ws) == []  # a run with no ledger has no rows (nothing is reconstructed)
    tele = build_telemetry(ws, rec, write=False)
    assert tele.cost is not None and tele.cost.n_calls == 0 and tele.settings is not None
    ledger.write_text(json.dumps({"stage": "plan", "role": "planner", "label": "plan", "cost_usd": 0.5,
                                  "model": "gemini-3.7-flash", "n_calls": 1}) + "\n")
    tele = build_telemetry(ws, rec)
    assert tele.cost is not None and tele.cost.by_stage[0].stage == "plan"
    assert {s.stage for s in tele.cost.by_stage} <= set(STAGE_ORDER)
    assert not (ws.telemetry / "usage.jsonl").exists(), "one ledger, one name"
    # a degraded verdict is unscored in the per-round cost table, never a 0.0
    rec.rounds[1].judgment = rec.rounds[1].judgment.model_copy(update={"summary": "judge_error: outage"})
    by_round = build_telemetry(ws, rec, write=False).cost.by_round
    assert [r["score"] for r in by_round] == [rec.rounds[0].judgment.overall, None]


def test_the_settings_snapshot_records_the_temperature_the_track_planned_at(tmp_path):
    """The planner row names the temperature the track planned at (graphics 0.5), never a default."""
    from types import SimpleNamespace

    from codeverse3d.contracts.common import Language
    from codeverse3d.record.telemetry import settings_snapshot
    from codeverse3d.tracks.graphics import GraphicsTrack
    from codeverse3d.tracks.static_object import StaticObjectTrack

    judge = SimpleNamespace(temperature=0.2, thinking="low", n_samples=3)
    for language, track, want in ((Language.GLSL_SHADER, GraphicsTrack(), 0.5),
                                  (Language.BLENDER, StaticObjectTrack(), 0.4)):
        _ws, rec = make_fake_run(tmp_path / language.value, language=language)
        assert next(r for r in settings_snapshot(rec).roles if r.role == "planner").temperature is None
        rec.extra["sampling"] = track.sampling(judge)
        roles = {r.role: r for r in settings_snapshot(rec).roles}
        assert (roles["planner"].temperature, roles["planner"].source) == (want, "run"), language
        assert (roles["judge"].temperature, roles["judge"].thinking, roles["judge"].n_samples) == (0.2, "low", 3)


def test_rejected_texture_pass_is_not_delivered_or_linked(fake_run):
    """Belt and braces: a stray canonical object_textured.glb from a rejected pass
    is skipped by the deliverable AND by the gallery links (both gate on shipped)."""
    from codeverse3d.texturing.run import report_path

    ws, rec = fake_run
    (ws.artifacts / "object_textured.glb").write_bytes(b"glTF\x02" + b"\0" * 16)
    report_path(ws).parent.mkdir(parents=True, exist_ok=True)

    def texturing(shipped: bool) -> None:  # the pass report names the round GLB it started from
        rec.extra["texturing"] = {"shipped": shipped, "glb_textured": "artifacts/object_textured.glb"}
        ws.write_json(report_path(ws), {**rec.extra["texturing"], "glb_in": "artifacts/r01/object.glb"})

    texturing(False)
    build_deliverable(ws, rec, 1)
    assert not (ws.deliverable / "object_textured.glb").exists()
    entry = entry_from_record("runs", ws, rec)
    assert "textured glb" not in {ln.label for ln in entry.links}
    # ... and a SHIPPED pass is delivered and linked — with the round it textured, only
    texturing(True)
    build_deliverable(ws, rec, 0)
    assert not (ws.deliverable / "object_textured.glb").exists()
    build_deliverable(ws, rec, 1)
    assert (ws.deliverable / "object_textured.glb").is_file()
    entry = entry_from_record("runs", ws, rec)
    assert "textured glb" in {ln.label for ln in entry.links}


def test_a_cards_links_are_the_picked_rounds_never_the_last_builds(tmp_path):
    """The gallery linked artifacts/object.glb and artifacts/frames/ — the LAST build's — when the
    picked round had no copy of its own (and frames/ always)."""
    from .conftest import tiny_png

    ws, rec = make_fake_run(tmp_path / "runs", scores=(0.90, 0.50))  # r00 is picked, r01 built last
    tiny_png(ws.artifacts / "frames" / "f00_t0.png")                  # the last build's frames
    tiny_png(ws.renders_dir(0) / "frame_t00.00.png")                  # r00's own
    links = {ln.label: ln.rel for ln in entry_from_record("runs", ws, rec).links}
    assert links["glb"] == "artifacts/r00/object.glb" and links["frames/"] == "artifacts/renders/r00"
    shutil.rmtree(ws.round_artifacts(0))                               # r00 kept no build outputs
    links = {ln.label: ln.rel for ln in entry_from_record("runs", ws, rec).links}
    assert "glb" not in links


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


def test_show_on_a_run_without_a_record_is_its_status(tmp_path: Path):
    """Mid-run there is no record.json: `show` prints where the run stands and says what the other
    sections wait for (it used to exit 1 and point at `3dcode status`); a named section still fails."""
    from codeverse3d.contracts.common import Backends, Language, Track
    from codeverse3d.contracts.spec import Spec
    from codeverse3d.proc import EventLog
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "runs" / "stool").create()
    ws.write_json(ws.spec_path, Spec(id="stool", track=Track.STATIC_OBJECT, language=Language.BLENDER,
                                     prompt="a small wooden stool", backends=Backends(generator="gemini-cli:x")))
    EventLog(ws.events_path).emit("run.start")
    r = _show("stool", ws.root.parent)
    assert r.exit_code == 0, r.output
    assert "a small wooden stool" in r.output and "run.start" in r.output and "need the record" in r.output
    assert _show("stool", ws.root.parent, "--section", "cost").exit_code == 1


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

    # a packaged run copied without its history exports from its deliverable snapshot
    shutil.rmtree(ws_new.root / ".git")
    shutil.rmtree(ws_old.root)
    rep = export_samples(runs, tmp_path / "ds_nogit")
    assert rep.n_exported == 1, rep.skipped
    meta_path = next((tmp_path / "ds_nogit").rglob("meta.json"))
    assert json.loads(meta_path.read_text())["code_source"] == "deliverable"
    assert (meta_path.parent / "src" / "model.py").is_file()
