"""CLI tests (typer CliRunner, offline): make --no-run, status, flywheel, doctor pieces, tools list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codeverse.cli import _common as C
from codeverse.cli.main import app
from codeverse.cli.main import make as make_cmd
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
    # `.locks/` (runlock.exclusive) lives beside the run dirs on purpose — a lock inside
    # the directory a --force wipe deletes is no lock
    dirs = [d for d in runs.iterdir() if d.is_dir() and not d.name.startswith(".")]
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

    assert TRACK_INFO[Track.GRAPHICS].rubric == "shader_v2"

    class _R:  # minimal record stub
        class spec:
            track = Track.GRAPHICS

    rnd = RoundRecord(index=0, kind="baseline")
    assert rubric_for(_R, rnd, None) == "shader_v2"
    assert rubric_for(_R, rnd, "asset_v1") == "asset_v1"


def test_calibration_rubric_map_includes_graphics(tmp_path: Path):
    """Same default as `3dcv judge`: a graphics round with no stored judgment used to be
    re-judged with static_object_v1 because calibration kept its own three-track
    TRACK_RUBRIC instead of reading TRACK_INFO."""
    from codeverse.contracts.artifacts import RenderSet, RenderView
    from codeverse.contracts.common import Language, Track
    from codeverse.contracts.run import RoundRecord
    from codeverse.judges.calibration import load_run_cases

    run = tmp_path / "shader_run"
    (run / "rounds").mkdir(parents=True)
    spec = Spec(id="shader_run", track=Track.GRAPHICS, language=Language.GLSL_SHADER, prompt="neon rain on a window")
    (run / "spec.json").write_text(spec.model_dump_json())
    renders = RenderSet(views=[RenderView(name="frame_0", path=str(run / "artifacts" / "renders" / "r00" / "frame_0.png"))],
                        renderer="fake")
    (run / "rounds" / "r00.json").write_text(RoundRecord(index=0, kind="baseline", renders=renders).model_dump_json())
    assert [c.rubric for c in load_run_cases(run)] == ["shader_v2"]


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
            def run(self, spec, ws_, *, resume=False, force=False):
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


def test_resume_refuses_a_finished_run_and_never_re_enters_it(tmp_path: Path, monkeypatch):
    """SMOKE1: `3dcv resume` called _run_track unconditionally, with no look at
    run_state.  On a run that had ended stop_reason='pass' it re-entered the pipeline,
    re-ran the plan stage as a real billed model call and rewrote status from 'passed'
    back to 'planning' — a finished run left stuck mid-pipeline, and money spent, from
    one accidental or scripted resume."""
    from codeverse.contracts.run import RunStatus
    from codeverse.orchestrator.state import RunState
    from codeverse.workspace import Workspace

    runs = tmp_path / "runs"
    assert runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run"]).exit_code == 0
    ws = Workspace(next(d for d in runs.iterdir() if d.is_dir()))
    state = RunState(status=RunStatus.PASSED, stop_reason="pass", best_score=0.7436)
    state.save(ws)

    entered = []

    def fake_get_track(track, **options):
        class _T:
            def run(self, spec, ws_, *, resume=False, force=False):
                entered.append(resume)
                raise KeyboardInterrupt

        return _T()

    import codeverse.tracks as tracks_pkg

    monkeypatch.setattr(tracks_pkg, "get_track", fake_get_track)
    r = runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)])
    assert r.exit_code == 1 and "already finished" in r.output
    assert entered == [], "the pipeline must not be re-entered"
    assert RunState.load(ws).status is RunStatus.PASSED, "the terminal state must survive"
    # ... and the escape hatch still works, loudly
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--force"]).exit_code == 130
    assert entered == [True]

    # a budget stop is the documented exception: it resumes when a cap is raised
    RunState(status=RunStatus.BUDGET, stop_reason="budget").save(ws)
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)]).exit_code == 1
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--max-usd", "4.5"]).exit_code == 130

    # an interrupted run is untouched by the guard
    RunState(status=RunStatus.REFINING).save(ws)
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)]).exit_code == 130


def test_resume_threads_force_through_to_the_track(tmp_path: Path, monkeypatch):
    """reconcile_resume (tracks/lifecycle.py) needs the CLI's --force to decide between
    refusing a spec edit (SpecChanged) and archiving the old rounds under
    rounds/pre_force/ — so `3dcv resume --force` must reach BaseTrack.run(force=...)."""
    runs = tmp_path / "runs"
    assert runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run"]).exit_code == 0
    ws = next(d for d in runs.iterdir() if d.is_dir())
    seen = {}

    def fake_get_track(track, **options):
        class _T:
            def run(self, spec, ws_, *, resume=False, force=False):
                seen["resume"], seen["force"] = resume, force
                raise KeyboardInterrupt

        return _T()

    import codeverse.tracks as tracks_pkg

    monkeypatch.setattr(tracks_pkg, "get_track", fake_get_track)
    assert runner.invoke(app, ["resume", ws.name, "--runs-dir", str(runs)]).exit_code == 130
    assert seen == {"resume": True, "force": False}
    assert runner.invoke(app, ["resume", ws.name, "--runs-dir", str(runs), "--force"]).exit_code == 130
    assert seen == {"resume": True, "force": True}


def test_a_spec_change_refusal_is_a_clean_cli_error(tmp_path: Path, monkeypatch):
    """SpecChanged is a refusal with instructions (fork, or --force to archive), not a
    crash: no traceback, and the CLI's typed configuration-error exit code."""
    from codeverse.tracks.lifecycle import SpecChanged

    runs = tmp_path / "runs"
    assert runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run"]).exit_code == 0
    ws = next(d for d in runs.iterdir() if d.is_dir())

    def fake_get_track(track, **options):
        class _T:
            def run(self, spec, ws_, *, resume=False, force=False):
                raise SpecChanged("spec.json changed under this run; fork a new run or resume with --force")

        return _T()

    import codeverse.tracks as tracks_pkg

    monkeypatch.setattr(tracks_pkg, "get_track", fake_get_track)
    r = runner.invoke(app, ["resume", ws.name, "--runs-dir", str(runs)])
    out = r.output + str(getattr(r, "stderr", "") or "")
    assert r.exit_code == 2 and "--force" in out
    assert "Traceback" not in r.output


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


def test_reference_with_no_run_says_it_is_about_to_spend_money(tmp_path: Path, monkeypatch):
    """SM-06: `--no-run` reads as "filesystem only" (its help said "only create the
    workspace + spec.json"), but main.py grounds the reference BEFORE honouring it: 1
    planner call + n_views image generations + n_views vision checks, ~$0.15, and a block
    of up to model_timeout_s on a degraded provider.  The pass is not wasted — the
    grounded spec is written back to spec.json — so it keeps running, but the command must
    say so instead of looking offline."""
    import codeverse.reference as REF

    calls = []

    def fake_ground_spec(spec, ws, *, n_views=2, events=None, **kw):
        calls.append(n_views)
        raise SystemExit(7)  # stop before any real model work

    monkeypatch.setattr(REF, "ground_spec", fake_ground_spec)
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a wooden stool", "--reference", "--no-run",
                            "--runs-dir", str(runs), "--slug", "refnorun"])
    out = " ".join(r.output.split())
    assert calls == [2], "the grounding pass still runs: the grounded spec is the artifact"
    assert "--reference makes model calls now" in out and "$0.15" in out
    import typing

    help_text = typing.get_type_hints(make_cmd, include_extras=True)["no_run"].__metadata__[0].help
    assert "with --reference the reference pass still runs first" in help_text


def test_an_unknown_profile_is_a_clean_error_not_a_traceback(tmp_path: Path):
    """SMOKE5: `--profile <unknown>` exited with a raw ValueError traceback out of
    cost/profiles.py:153, while every other rejected value (a slug collision, a missing
    battery) printed the clean "error: ..." line the CLI uses for user errors."""
    r = runner.invoke(app, ["make", "a chair", "--profile", "turbo", "--no-run",
                            "--runs-dir", str(tmp_path / "runs"), "--slug", "prof"])
    assert r.exit_code == 2, "the CLI's typed configuration-error code (see SM-10)"
    assert "unknown profile" in r.output and "economy, balanced, quality" in r.output
    assert "Traceback" not in r.output and "ValueError" not in r.output
    assert not (tmp_path / "runs" / "prof").exists(), "no workspace for a rejected flag"


def test_a_negative_budget_is_rejected_before_the_workspace_exists(tmp_path: Path):
    """SM-11: --max-usd took any float.  A negative ceiling is not a small budget, it is
    an unrunnable one — BudgetGuard.ok() is False before a single token is spent, the
    first charge raises "cost $0.001 exceeds max_usd $-2.50", and grant_grace cannot
    lift a hard ceiling back above zero.  `3dcv make` nonetheless printed
    `max_usd -3.0`, created the workspace and git-committed the spec."""
    runs = tmp_path / "runs"
    for flag, value in (("--max-usd", "-3"), ("--max-minutes", "-10")):
        r = runner.invoke(app, ["make", "a chair", flag, value, "--no-run",
                                "--runs-dir", str(runs), "--slug", "neg"])
        assert r.exit_code != 0, f"{flag} {value} was accepted"
        assert "range x>=0" in r.output.replace("\n", "")
        assert not (runs / "neg").exists(), "no workspace may be created for a rejected budget"

    # 0 stays legal (documented: a run at 0 degrades from its first check) ...
    r = runner.invoke(app, ["make", "a chair", "--max-usd", "0", "--no-run",
                            "--runs-dir", str(runs), "--slug", "zero"])
    assert r.exit_code == 0 and (runs / "zero" / "spec.json").is_file()
    # ... and the contract refuses a negative ceiling even when built directly
    import pytest as _pytest
    from pydantic import ValidationError

    from codeverse.contracts.common import Budget

    with _pytest.raises(ValidationError):
        Budget(max_usd=-3.0)
    assert Budget(max_usd=0.0).max_usd == 0.0


def test_texture_is_not_offered_on_tracks_that_have_no_glb(tmp_path: Path):
    """SM-08: `--texture` (and `--profile quality`, which forces it) was accepted on the
    scene and graphics tracks, frozen on the spec, and printed as `texture True` — but a
    scene has no GLB deliverable and neither graphics language produces one, so the
    finalise pass could ONLY raise FileNotFoundError('no GLB to texture'), swallowed by
    BaseTrack._texture_pass into a spurious `texture.failed`.  quality's advertised
    "+texture" was a guaranteed no-op on half the tracks, and scene runs never reached
    the command that would work.  `3dcv texture pass` already guards this; `3dcv make`
    did not."""
    from codeverse.contracts.common import Track
    from codeverse.texturing.run import texture_requested, texture_supported

    runs = tmp_path / "runs"
    # profile-implied: quality still works, it just has no texture pass on this track
    r = runner.invoke(app, ["make", "a plasma shader", "--track", "graphics", "--profile", "quality",
                            "--no-run", "--runs-dir", str(runs), "--slug", "gfxq"])
    assert r.exit_code == 0
    opts = json.loads((runs / "gfxq" / "spec.json").read_text())["options"]
    assert opts["texture"] is False, "a run must not record a pass it cannot run"
    assert opts["profile"] == "quality", "the rest of the dial still applies"

    # explicit: refused, and pointed at the command that does work
    r = runner.invoke(app, ["make", "a kitchen", "--track", "scene", "--texture",
                            "--no-run", "--runs-dir", str(runs), "--slug", "scnx"])
    assert r.exit_code == 1 and "texture scene-pack" in r.output.replace("\n", "")
    assert not (runs / "scnx").exists()

    # the object tracks are untouched
    r = runner.invoke(app, ["make", "a chair", "--profile", "quality",
                            "--no-run", "--runs-dir", str(runs), "--slug", "objq"])
    assert r.exit_code == 0
    assert json.loads((runs / "objq" / "spec.json").read_text())["options"]["texture"] is True

    # and the single owner of the decision knows the track scope, so a spec that already
    # carries texture=True on a scene track (recorded before this fix) is a no-op too
    assert texture_supported(Track.STATIC_OBJECT) and not texture_supported(Track.SCENE)
    stale = Spec.model_validate(json.loads((runs / "objq" / "spec.json").read_text()))
    assert texture_requested(stale)
    assert not texture_requested(stale.model_copy(update={"track": Track.GRAPHICS}))


# --------------------------------------------------- render must not publish the wrong round
def _round_guard_ws(tmp_path: Path, *, best: int, tree: int):
    """A workspace whose record names `best` while the render tree sits at `tree`."""
    import json as _json

    from codeverse.workspace import Workspace

    ws = Workspace(tmp_path / "wronground")
    ws.create()
    for i in range(tree + 1):
        (ws.artifacts / "renders" / f"r{i:02d}").mkdir(parents=True, exist_ok=True)
    ws.record_path.write_text(_json.dumps({"best_round": best}))
    return ws


def test_render_refuses_when_the_tree_is_not_the_best_round(tmp_path: Path):
    """`render` renders the WORKING TREE, and a run's tree sits at its LAST round — which
    is not always its best.  Measured 2026-08-25 on tsr_scn_neon_alley: judge by round
    0.338 / 0.375 / 0.529 / 0.632 / 0.000, round 4 rendering eight completely blank tiles.
    The harness correctly kept r3, but a plain `3dcv render` re-rendered the blank r4 and
    was very nearly published."""
    from codeverse.cli._common import CliError
    from codeverse.cli.inspect_cmd import _render_round_or_refuse

    ws = _round_guard_ws(tmp_path, best=3, tree=4)
    with pytest.raises(CliError) as ei:
        _render_round_or_refuse(ws, None)
    msg = str(ei.value)
    assert "BEST round is r3" in msg and "working tree is at r4" in msg
    assert "deliverable" in msg, "it must point at the packaged best round"
    assert "--round 4" in msg, "and offer the explicit escape"
    assert ei.value.exit_code == 2


def test_render_round_flag_cannot_mislabel_another_rounds_code(tmp_path: Path):
    """--round only chose the OUTPUT FOLDER, so `render X --round 3` wrote r03-labelled
    images of round 4's code.  Refuse rather than write a picture whose label is a lie."""
    from codeverse.cli._common import CliError
    from codeverse.cli.inspect_cmd import _render_round_or_refuse

    ws = _round_guard_ws(tmp_path, best=3, tree=4)
    with pytest.raises(CliError):
        _render_round_or_refuse(ws, 3)
    assert _render_round_or_refuse(ws, 4) == 4, "rendering the tree's own round is fine"


def test_render_is_unaffected_when_the_best_round_is_the_last(tmp_path: Path):
    """The common case must not become noisier: no record, or best == tree, just renders."""
    from codeverse.cli.inspect_cmd import _render_round_or_refuse

    assert _render_round_or_refuse(_round_guard_ws(tmp_path / "a", best=2, tree=2), None) == 2
    ws = _round_guard_ws(tmp_path / "b", best=1, tree=1)
    ws.record_path.unlink()  # a run that has not written a record yet
    assert _render_round_or_refuse(ws, None) == 1
