"""CLI tests (typer CliRunner, offline): make --no-run, status, flywheel, doctor pieces, tools list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codeverse.cli import _common as C
from codeverse.cli.main import app
from codeverse.contracts.spec import Spec

runner = CliRunner()


def test_help_and_version():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0 and "flywheel" in r.output and "doctor" in r.output
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and "3dcv" in r.output


def test_make_no_run_creates_valid_workspace(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a wooden dining chair", "--track", "static_object", "--language", "blender",
                            "--runs-dir", str(runs), "--no-run", "--dim", "width=0.5", "--must", "four legs",
                            "--rounds", "2", "--max-usd", "1.5", "--generator", "gemini-cli:gemini-3.7-flash"])
    assert r.exit_code == 0, r.output
    dirs = [d for d in runs.iterdir() if d.is_dir()]
    assert len(dirs) == 1
    ws = dirs[0]
    assert ws.name.startswith("a_wooden_dining_chair_") and (ws / "spec.json").is_file() and (ws / ".git").is_dir()
    spec = Spec.model_validate_json((ws / "spec.json").read_text())
    assert spec.constraints.dimensions_m == {"width": 0.5} and spec.constraints.must_have == ["four legs"]
    assert spec.budget.max_rounds == 2 and spec.budget.max_usd == 1.5
    assert spec.backends.generator == "gemini-cli:gemini-3.7-flash"
    assert (ws / "src").is_dir() and (ws / "artifacts" / "renders").is_dir()
    # same prompt again → same slug → refuse without --force
    r2 = runner.invoke(app, ["make", "a wooden dining chair", "--runs-dir", str(runs), "--no-run"])
    assert r2.exit_code == 1 and "already exists" in (r2.output + str(r2.stderr if hasattr(r2, "stderr") else ""))
    r3 = runner.invoke(app, ["make", "a wooden dining chair", "--runs-dir", str(runs), "--no-run", "--force"])
    assert r3.exit_code == 0
    # invalid language/track combination → typed error
    r4 = runner.invoke(app, ["make", "x", "--track", "scene", "--language", "blender", "--runs-dir", str(runs), "--no-run", "--slug", "bad"])
    assert r4.exit_code == 1


def test_status_on_fake_run(runs_dir: Path):
    r = runner.invoke(app, ["status", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 0, r.output
    assert "passed" in r.output and "0.800" in r.output and "rounds" in r.output
    assert "1*" in r.output  # best round marked
    r = runner.invoke(app, ["status", "nope", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 1


def test_flywheel_commands(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    r = runner.invoke(app, ["flywheel", "export", str(runs_dir), str(out), "--pack"])
    assert r.exit_code == 0, r.output
    assert (out / "metadata.parquet").is_file() and (out / "samples-000.tar").is_file()
    r = runner.invoke(app, ["flywheel", "pairs", str(runs_dir), str(tmp_path / "p.jsonl")])
    assert r.exit_code == 0 and "pairs" in r.output
    r = runner.invoke(app, ["flywheel", "index", str(runs_dir), str(tmp_path / "i.sqlite")])
    assert r.exit_code == 0 and "indexed 3 runs" in r.output
    r = runner.invoke(app, ["flywheel", "dedupe", str(out), "--no-mesh"])
    assert r.exit_code == 0 and "3 samples" in r.output
    r = runner.invoke(app, ["flywheel", "gallery", str(runs_dir), str(tmp_path / "g.html"), "--title", "batch 1"])
    assert r.exit_code == 0 and "gallery of 3 runs" in r.output, r.output
    assert "batch 1" in (tmp_path / "g.html").read_text()


def test_tools_list_and_unknown(tmp_path: Path):
    r = runner.invoke(app, ["tools", "list"])
    # spatial tools may or may not be installed yet; either a table or a clear message, never a traceback
    assert r.exit_code in (0, 2), r.output
    assert "Traceback" not in r.output


def test_lazy_import_message():
    with pytest.raises(C.CliError):
        C.lazy("codeverse.definitely_missing_module")


def test_doctor_json(tmp_path: Path):
    r = runner.invoke(app, ["doctor", "--no-gpu", "--json"])
    assert r.exit_code in (0, 1), r.output
    rows = json.loads(r.output[r.output.index("["):])
    checks = {row["check"] for row in rows}
    assert {"python", "python deps", "blender", "node", "three", "gemini keys", "git", "mcp"} <= checks


# --------------------------------------------------------------------------- finding: `3dcv judge` inputs (main.py:204)
class _FakeVlm:
    captured: dict = {}

    def __init__(self, *, rubric=None, model_id=None, n_samples=1, **kw):
        _FakeVlm.captured["rubric"] = rubric
        _FakeVlm.captured["n_samples"] = n_samples

    def judge(self, inp):
        from tests.flywheel_cli.conftest import _judgment

        _FakeVlm.captured["inp"] = inp
        return _judgment(0.7, False, ["polish"])


def _write_stool_plan(ws):
    from codeverse.contracts.plan import AcceptanceItem, BBox, PartPlan, StaticPlan

    plan = StaticPlan(object_name="Chair", summary="a chair", overall_bbox=BBox(center=(0, 0, 0.4), extents=(0.5, 0.5, 0.8)),
                      parts=[PartPlan(name="Seat", role="r", description="d", bbox=BBox(center=(0, 0, 0.4), extents=(0.4, 0.4, 0.04)))],
                      acceptance=[AcceptanceItem(id="a1", text="four legs", how="visual", priority="must")])
    ws.write_json(ws.plan_path, plan)


def test_judge_rejudges_with_in_run_inputs(runs_dir: Path, monkeypatch):
    import codeverse.judges.vlm_judge as vj
    from codeverse.workspace import Workspace
    from tests.flywheel_cli.conftest import tiny_png

    ws = Workspace(runs_dir / "wooden_chair_ab12cd34")
    _write_stool_plan(ws)
    tiny_png(ws.renders_dir(1) / "clay" / "view_top.png", (128, 128, 128))
    monkeypatch.setattr(vj, "VlmJudge", _FakeVlm)
    _FakeVlm.captured.clear()
    r = runner.invoke(app, ["judge", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 0, r.output
    inp = _FakeVlm.captured["inp"]
    assert _FakeVlm.captured["rubric"] == "static_object_v1"  # the rubric the round was judged with
    assert inp.round_index == 1  # best round
    assert "Chair" in inp.plan_summary  # plan digest, not empty
    assert [a.id for a in inp.acceptance] == ["a1"]
    assert inp.previous is not None and inp.previous.overall == 0.55  # round 0's verdict
    assert inp.gates and inp.renders.views
    assert inp.geometry_views is not None and inp.geometry_views.views[0].mode == "clay"
    assert (ws.artifacts / "judge" / "r01_cli.json").is_file()
    assert "prompt images" in r.output


def test_judge_uses_reference_judge_for_measured_rubrics(runs_dir: Path, monkeypatch):
    import codeverse.judges.reference as ref

    class _FakeRef(_FakeVlm):
        pass

    monkeypatch.setattr(ref, "ReferenceJudge", _FakeRef)
    _FakeRef.captured.clear()
    r = runner.invoke(app, ["judge", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir),
                            "--rubric", "reference_v1"])
    assert r.exit_code == 0, r.output  # no ValueError traceback any more
    assert _FakeRef.captured["rubric"] == "reference_v1"


def test_judge_wraps_value_error_as_cli_error(runs_dir: Path, monkeypatch):
    import codeverse.judges.vlm_judge as vj

    class _Boom(_FakeVlm):
        def judge(self, inp):
            raise ValueError("rubric has measured criteria but VlmJudge computes none")

    monkeypatch.setattr(vj, "VlmJudge", _Boom)
    r = runner.invoke(app, ["judge", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 1
    out = r.output + str(getattr(r, "stderr", "") or "")
    assert "judge failed" in out and "Traceback" not in r.output


def test_judge_rubric_map_includes_graphics():
    from codeverse.cli._judge import rubric_for
    from codeverse.contracts.common import TRACK_INFO, Track
    from codeverse.contracts.run import RoundRecord

    assert TRACK_INFO[Track.GRAPHICS].rubric == "shader_v1"

    class _R:  # minimal record stub
        class spec:
            track = Track.GRAPHICS

    rnd = RoundRecord(index=0, kind="baseline")
    assert rubric_for(_R, rnd, None) == "shader_v1"
    assert rubric_for(_R, rnd, "asset_v1") == "asset_v1"


# --------------------------------------------------------------------------- make --texture / status extras / render graphics
def test_make_texture_flag_sets_spec_options(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run", "--texture", "--candidates", "3"])
    assert r.exit_code == 0, r.output
    ws = next(d for d in runs.iterdir() if d.is_dir())
    spec = Spec.model_validate_json((ws / "spec.json").read_text())
    # run-shape options are frozen on the spec (no more magic 'texture' tag)
    assert spec.options.texture is True and spec.options.candidates == 3
    assert "texture" not in spec.tags


def test_make_language_defaults_to_the_tracks_first(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a harbour at night", "--track", "scene", "--runs-dir", str(runs), "--no-run"])
    assert r.exit_code == 0, r.output
    ws = next(d for d in runs.iterdir() if d.is_dir())
    spec = Spec.model_validate_json((ws / "spec.json").read_text())
    assert spec.language.value == "scene_threejs"


def test_make_invalid_combo_leaves_no_orphan_workspace(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "x", "--track", "scene", "--language", "blender",
                            "--runs-dir", str(runs), "--no-run", "--slug", "bad"])
    assert r.exit_code == 1
    assert not (runs / "bad").exists(), "invalid spec must not leave an orphan run dir"


def test_resume_budget_flags_rewrite_spec_and_emit_event(tmp_path: Path, monkeypatch):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run", "--max-usd", "1.0", "--rounds", "1"])
    assert r.exit_code == 0, r.output
    ws = next(d for d in runs.iterdir() if d.is_dir())

    seen = {}

    def fake_get_track(track, **options):
        class _T:
            def run(self, spec, ws_, *, resume=False):
                seen["spec"] = spec
                raise KeyboardInterrupt  # stop before any real work

        return _T()

    import codeverse.tracks as tracks_pkg

    monkeypatch.setattr(tracks_pkg, "get_track", fake_get_track)
    r2 = runner.invoke(app, ["resume", ws.name, "--runs-dir", str(runs), "--max-usd", "4.5", "--rounds", "3"])
    assert r2.exit_code == 130
    spec = Spec.model_validate_json((ws / "spec.json").read_text())
    assert spec.budget.max_usd == 4.5 and spec.budget.max_rounds == 3 and spec.budget.max_minutes == 60.0
    assert seen["spec"].budget.max_usd == 4.5, "the resumed run must see the raised budget"
    events = [json.loads(line) for line in (ws / "events.jsonl").read_text().splitlines()]
    raised = [e for e in events if e.get("event") == "budget.raised"]
    assert raised and raised[0]["max_usd"] == 4.5 and raised[0]["max_rounds"] == 3


def test_status_shows_candidates_and_texturing(runs_dir: Path):
    import json as _json

    from codeverse.workspace import Workspace

    ws = Workspace(runs_dir / "wooden_chair_ab12cd34")
    (ws.root / "rounds").mkdir(exist_ok=True)
    (ws.root / "rounds" / "candidates.json").write_text(_json.dumps({
        "n": 2, "selected": 1,
        "candidates": [{"index": 0, "label": "c0", "score": 0.51, "build_ok": True},
                       {"index": 1, "label": "c1", "score": 0.63, "build_ok": True}],
        "pairwise": {"a": "c0", "b": "c1", "winner": "b", "confidence": 0.8}}))
    rec = _json.loads(ws.record_path.read_text())
    rec.setdefault("extra", {})["texturing"] = {"shipped": True, "delta": 0.01, "reason": "", "n_textures": 3,
                                                "glb_textured": "artifacts/object_textured.glb"}
    ws.record_path.write_text(_json.dumps(rec))
    r = runner.invoke(app, ["status", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 0, r.output
    assert "candidates" in r.output and "c1" in r.output and "pairwise" in r.output
    assert "texturing" in r.output and "shipped" in r.output


def test_render_graphics_regenerates_frames(tmp_path: Path, monkeypatch):
    import codeverse.languages as langs
    from codeverse.contracts.artifacts import BuildResult
    from codeverse.contracts.common import Language
    from tests.flywheel_cli.conftest import make_fake_run, tiny_png

    ws, rec = make_fake_run(tmp_path / "runs", "rain_glsl", prompt="neon rain", language=Language.GLSL_SHADER)
    frames = ws.artifacts / "frames"
    tiny_png(frames / "f00_t0.00.png")
    tiny_png(frames / "f01_t1.00.png")
    sheet = tiny_png(ws.artifacts / "frames_sheet.png", (0, 0, 200))

    class _RT:
        def build(self, w, **kw):
            return BuildResult(ok=True, language="glsl_shader", glb_path=None, duration_ms=8,
                               census={"renderer": "fake-gl"},
                               extra_paths={"frames": str(frames), "sheet": str(sheet)})

    monkeypatch.setattr(langs, "get_runtime", lambda language: _RT())
    out = tmp_path / "out"
    r = runner.invoke(app, ["render", "rain_glsl", "--runs-dir", str(tmp_path / "runs"), "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert (out / "sheet.png").is_file() and len(list(out.glob("f*.png"))) == 2
    assert "fake-gl" in r.output
