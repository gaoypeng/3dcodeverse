"""CLI tests (typer CliRunner, offline): make --no-run, status, flywheel, doctor pieces, tools list."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from codeverse3d.cli.main import app
from codeverse3d.cli.main import make as make_cmd
from codeverse3d.contracts.spec import Spec

runner = CliRunner()


@pytest.fixture
def made_run(tmp_path: Path):
    """`3dcode make --no-run` into a fresh runs dir → ``(runs_dir, run_dir)``."""
    def make(*extra: str) -> tuple[Path, Path]:
        runs = tmp_path / "runs"
        r = runner.invoke(app, ["make", "a clay pot", "--runs-dir", str(runs), "--no-run", *extra])
        assert r.exit_code == 0, r.output
        return runs, next(d for d in runs.iterdir() if d.is_dir() and not d.name.startswith("."))

    return make


@pytest.fixture
def stub_track(monkeypatch):
    """Replace ``tracks.get_track`` with a stub whose ``run`` calls ``on_run(spec, resume,
    force)`` and then raises ``exc`` — KeyboardInterrupt by default, which the CLI turns
    into exit code 130, so the resume path is exercised without any real work."""
    import codeverse3d.tracks as tracks_pkg

    def install(on_run=None, exc: BaseException | None = None):
        class _T:
            def run(self, spec, ws_, *, resume=False, force=False):
                if on_run is not None:
                    on_run(spec, resume, force)
                raise exc if exc is not None else KeyboardInterrupt

        monkeypatch.setattr(tracks_pkg, "get_track", lambda track, **options: _T())

    return install


def test_help_and_version():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0 and "flywheel" in r.output and "doctor" in r.output
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and "3dcode" in r.output


def test_make_no_run_creates_valid_workspace(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a wooden dining chair", "--track", "static_object", "--language", "blender",
                            "--runs-dir", str(runs), "--no-run", "--dim", "width=0.5", "--must", "four legs",
                            "--rounds", "2", "--generator", "gemini-cli:gemini-3.7-flash"])
    assert r.exit_code == 0, r.output
    # `.locks/` (runlock.exclusive) lives beside the run dirs on purpose — a lock inside
    # the directory a --force wipe deletes is no lock
    dirs = [d for d in runs.iterdir() if d.is_dir() and not d.name.startswith(".")]
    assert len(dirs) == 1
    ws = dirs[0]
    assert ws.name.startswith("a_wooden_dining_chair_") and (ws / "spec.json").is_file() and (ws / ".git").is_dir()
    spec = Spec.model_validate_json((ws / "spec.json").read_text())
    assert spec.constraints.dimensions_m == {"width": 0.5} and spec.constraints.must_have == ["four legs"]
    assert spec.budget.max_rounds == 2 and spec.budget.max_minutes == 60.0
    assert spec.backends.generator == "gemini-cli:gemini-3.7-flash"
    assert (ws / "src").is_dir() and (ws / "artifacts" / "renders").is_dir()
    # same prompt again → same slug → refuse without --force
    r2 = runner.invoke(app, ["make", "a wooden dining chair", "--runs-dir", str(runs), "--no-run"])
    assert r2.exit_code == 1 and "already exists" in (r2.output + str(r2.stderr if hasattr(r2, "stderr") else ""))
    r3 = runner.invoke(app, ["make", "a wooden dining chair", "--runs-dir", str(runs), "--no-run", "--force"])
    assert r3.exit_code == 0


def test_flywheel_commands(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    r = runner.invoke(app, ["flywheel", "export", str(runs_dir), str(out), "--pack"])
    assert r.exit_code == 0, r.output
    assert (out / "metadata.parquet").is_file() and (out / "samples-000.tar").is_file()
    r = runner.invoke(app, ["flywheel", "pairs", str(runs_dir), str(tmp_path / "p.jsonl")])
    assert r.exit_code == 0 and "pairs" in r.output
    r = runner.invoke(app, ["flywheel", "index", str(runs_dir), str(tmp_path / "i.sqlite")])
    assert r.exit_code == 0 and "indexed 3 runs" in r.output


def test_tools_list_handles_an_optional_spatial_install():
    r = runner.invoke(app, ["tools", "list"])
    # spatial tools may or may not be installed yet; either a table or a clear message, never a traceback
    assert r.exit_code in (0, 2), r.output
    assert "Traceback" not in r.output


def test_doctor_json(monkeypatch):
    """The CLI serialises doctor rows; the checks themselves are tested by install tests."""
    import codeverse3d.cli.doctor as doctor_cli

    seen = {}

    def fake_doctor(*, live, gpu, skills):
        seen.update(live=live, gpu=gpu, skills=skills)
        return [("python", "OK", "3.x"), ("node", "WARN", "missing"), ("mcp", "FAIL", "broken")]

    monkeypatch.setattr(doctor_cli, "run_doctor", fake_doctor)
    r = runner.invoke(app, ["doctor", "--no-gpu", "--json"])
    assert r.exit_code == 1, r.output
    assert seen == {"live": False, "gpu": False, "skills": False}
    rows = json.loads(r.output[r.output.index("["):])
    assert rows == [
        {"check": "python", "status": "OK", "detail": "3.x"},
        {"check": "node", "status": "WARN", "detail": "missing"},
        {"check": "mcp", "status": "FAIL", "detail": "broken"},
    ]


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
    from codeverse3d.contracts.plan import AcceptanceItem, BBox, PartPlan, StaticPlan

    plan = StaticPlan(object_name="Chair", summary="a chair", overall_bbox=BBox(center=(0, 0, 0.4), extents=(0.5, 0.5, 0.8)),
                      parts=[PartPlan(name="Seat", role="r", description="d", bbox=BBox(center=(0, 0, 0.4), extents=(0.4, 0.4, 0.04)))],
                      acceptance=[AcceptanceItem(id="a1", text="four legs", how="visual", priority="must")])
    ws.write_json(ws.plan_path, plan)


def test_judge_rejudges_with_in_run_inputs(runs_dir: Path, monkeypatch):
    import codeverse3d.judges.vlm_judge as vj
    from codeverse3d.workspace import Workspace
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
    import codeverse3d.judges.vlm_judge as ref

    class _FakeRef(_FakeVlm):
        pass

    monkeypatch.setattr(ref, "ReferenceJudge", _FakeRef)
    _FakeRef.captured.clear()
    r = runner.invoke(app, ["judge", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir),
                            "--rubric", "reference_v1"])
    assert r.exit_code == 0, r.output  # no ValueError traceback any more
    assert _FakeRef.captured["rubric"] == "reference_v1"


def test_judge_wraps_value_error_as_cli_error(runs_dir: Path, monkeypatch):
    import codeverse3d.judges.vlm_judge as vj

    class _Boom(_FakeVlm):
        def judge(self, inp):
            raise ValueError("rubric has measured criteria but VlmJudge computes none")

    monkeypatch.setattr(vj, "VlmJudge", _Boom)
    r = runner.invoke(app, ["judge", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir)])
    assert r.exit_code == 1
    out = r.output + str(getattr(r, "stderr", "") or "")
    assert "judge failed" in out and "Traceback" not in r.output


def test_calibration_rubric_map_includes_graphics(tmp_path: Path):
    """A graphics round with no stored judgment is re-judged with the track's rubric, not static_object_v1."""
    from codeverse3d.addons.calibration import load_run_cases
    from codeverse3d.contracts.artifacts import RenderSet, RenderView
    from codeverse3d.contracts.common import Language, Track
    from codeverse3d.contracts.run import RoundRecord

    run = tmp_path / "shader_run"
    (run / "rounds").mkdir(parents=True)
    spec = Spec(id="shader_run", track=Track.GRAPHICS, language=Language.GLSL_SHADER, prompt="neon rain on a window")
    (run / "spec.json").write_text(spec.model_dump_json())
    renders = RenderSet(views=[RenderView(name="frame_0", path=str(run / "artifacts" / "renders" / "r00" / "frame_0.png"))],
                        renderer="fake")
    (run / "rounds" / "r00.json").write_text(RoundRecord(index=0, kind="baseline", renders=renders).model_dump_json())
    assert [c.rubric for c in load_run_cases(run)] == ["shader_v2"]


def test_make_invalid_combo_leaves_no_orphan_workspace(tmp_path: Path):
    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "x", "--track", "scene", "--language", "blender",
                            "--runs-dir", str(runs), "--no-run", "--slug", "bad"])
    assert r.exit_code == 1
    assert not (runs / "bad").exists(), "invalid spec must not leave an orphan run dir"


def test_resume_budget_flags_rewrite_spec_and_emit_event(made_run, stub_track):
    runs, ws = made_run("--max-minutes", "1.0", "--rounds", "1")
    seen = {}
    stub_track(lambda spec, resume, force: seen.__setitem__("spec", spec))
    r2 = runner.invoke(app, ["resume", ws.name, "--runs-dir", str(runs), "--max-minutes", "60", "--rounds", "3"])
    assert r2.exit_code == 130
    spec = Spec.model_validate_json((ws / "spec.json").read_text())
    assert spec.budget.max_rounds == 3 and spec.budget.max_minutes == 60.0
    assert seen["spec"].budget.max_minutes == 60.0, "the resumed run must see the raised ceiling"
    events = [json.loads(line) for line in (ws / "events.jsonl").read_text().splitlines()]
    raised = [e for e in events if e.get("event") == "budget.raised"]
    assert raised and raised[0]["max_rounds"] == 3


def test_resume_refuses_a_finished_run_and_never_re_enters_it(made_run, stub_track):
    """A finished run never re-enters the track unless forced."""
    from codeverse3d.contracts.run import RunStatus
    from codeverse3d.orchestrator import RunState
    from codeverse3d.workspace import Workspace

    runs, run_dir = made_run()
    ws = Workspace(run_dir)
    # a run recorded before 2026-09-22 that stopped on a pass (its state names the best it kept)
    ws.state_path.write_text(json.dumps({"status": "passed", "stop_reason": "pass", "best_round": 1,
                                         "best_commit": "abc", "best_score": 0.7436}))
    assert RunState.load(ws).status is RunStatus.STOPPED

    entered = []
    stub_track(lambda spec, resume, force: entered.append((resume, force)))
    r = runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)])
    assert r.exit_code == 1 and "already finished" in r.output and "stop_reason='pass'" in r.output
    assert entered == [], "the pipeline must not be re-entered"
    assert RunState.load(ws).status is RunStatus.STOPPED, "the terminal state must survive"
    # ... and the escape hatch still works, loudly
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--force"]).exit_code == 130
    assert entered == [(True, True)]

    # every round ran: raising --rounds is how a max_rounds stop goes on (from its last round)
    RunState(status=RunStatus.MAX_ROUNDS, stop_reason="max_rounds").save(ws)
    r = runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)])
    assert r.exit_code == 1 and "status=max_rounds" in r.output
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--max-minutes", "90"]).exit_code == 1
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--rounds", "5"]).exit_code == 130

    # a budget stop is the documented exception: it resumes when a cap is raised
    RunState(status=RunStatus.BUDGET, stop_reason="budget").save(ws)
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)]).exit_code == 1
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs), "--max-minutes", "60"]).exit_code == 130

    # an interrupted run is untouched by the guard
    RunState(status=RunStatus.REFINING).save(ws)
    assert runner.invoke(app, ["resume", ws.root.name, "--runs-dir", str(runs)]).exit_code == 130


def test_a_spec_change_refusal_is_a_clean_cli_error(made_run, stub_track):
    """Spec drift is a typed CLI refusal, not a traceback."""
    from codeverse3d.tracks.lifecycle import SpecChanged

    runs, ws = made_run()
    stub_track(exc=SpecChanged("spec.json changed under this run; fork a new run or resume with --force"))
    r = runner.invoke(app, ["resume", ws.name, "--runs-dir", str(runs)])
    out = r.output + str(getattr(r, "stderr", "") or "")
    assert r.exit_code == 2 and "--force" in out
    assert "Traceback" not in r.output


def test_status_shows_candidates_and_texturing(runs_dir: Path):
    import json as _json

    from codeverse3d.workspace import Workspace

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
    import codeverse3d.languages as langs
    from codeverse3d.contracts.artifacts import BuildResult
    from codeverse3d.contracts.common import Language
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
    """Reference preprocessing under --no-run must disclose its paid work."""
    import codeverse3d.reference as REF

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
    """An unknown profile is a clean user error."""
    r = runner.invoke(app, ["make", "a chair", "--profile", "turbo", "--no-run",
                            "--runs-dir", str(tmp_path / "runs"), "--slug", "prof"])
    assert r.exit_code == 2, "the CLI's typed configuration-error code (see SM-10)"
    assert "unknown profile" in r.output and "economy, balanced, quality" in r.output
    assert "Traceback" not in r.output and "ValueError" not in r.output
    assert not (tmp_path / "runs" / "prof").exists(), "no workspace for a rejected flag"


def test_texture_is_not_offered_on_tracks_that_have_no_glb(tmp_path: Path):
    """Impossible explicit texturing fails early; profiles degrade cleanly by track."""
    from codeverse3d.contracts.common import Track
    from codeverse3d.texturing.run import texture_requested, texture_supported

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


def test_best_of_n_is_not_offered_on_a_scene(tmp_path: Path):
    """F1: explicit --candidates N on a scene is refused, a profile's is 1, a stale spec runs one."""
    from codeverse3d.config import get_settings
    from codeverse3d.contracts.common import Track
    from codeverse3d.tracks.scene import SceneTrack

    runs = tmp_path / "runs"
    r = runner.invoke(app, ["make", "a kitchen", "--track", "scene", "--candidates", "2",
                            "--no-run", "--runs-dir", str(runs), "--slug", "scn2"])
    assert r.exit_code == 1 and "--candidates" in r.output and not (runs / "scn2").exists()
    r = runner.invoke(app, ["make", "a kitchen", "--track", "scene", "--profile", "quality",
                            "--no-run", "--runs-dir", str(runs), "--slug", "scnq"])
    assert r.exit_code == 0
    spec = Spec.model_validate(json.loads((runs / "scnq" / "spec.json").read_text()))
    assert spec.options.candidates == 1
    r = runner.invoke(app, ["resume", "scnq", "--runs-dir", str(runs), "--candidates", "2"])
    assert r.exit_code == 1 and "--candidates" in r.output
    # a scene spec recorded before the fix (options.candidates=2) runs, and records, one
    stale = spec.model_copy(update={"options": spec.options.model_copy(update={"candidates": 2})})
    assert SceneTrack(n_candidates=2)._resolve_candidates(stale, get_settings()) == 1
    assert spec.track is Track.SCENE


# --------------------------------------------------- render must not publish the wrong round
def _round_guard_ws(tmp_path: Path, *, tree: int, best: int | None = None):
    """A workspace whose render tree sits at `tree` (an old record may still name a `best`)."""
    import json as _json

    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "wronground")
    ws.create()
    for i in range(tree + 1):
        (ws.artifacts / "renders" / f"r{i:02d}").mkdir(parents=True, exist_ok=True)
    if best is not None:
        ws.record_path.write_text(_json.dumps({"best_round": best}))
    return ws


def test_render_only_labels_the_working_tree_round(tmp_path: Path):
    """A plain render is the working tree's round; a --round naming another one is refused."""
    from codeverse3d.cli._common import CliError
    from codeverse3d.cli.inspect_cmd import _render_round_or_refuse

    ws = _round_guard_ws(tmp_path, tree=4, best=3)
    assert _render_round_or_refuse(ws, None) == 4, "an old record's best_round no longer matters"
    with pytest.raises(CliError) as ei:
        _render_round_or_refuse(ws, 3)
    msg = str(ei.value)
    assert "working tree, which is at round 4" in msg and "3dcode pick" in msg and ei.value.exit_code == 2
    assert _render_round_or_refuse(ws, 4) == 4, "rendering the tree's own round is fine"
    assert _render_round_or_refuse(_round_guard_ws(tmp_path / "b", tree=1), None) == 1


def test_flywheel_caption_defaults_to_the_configured_captioner(runs_dir: Path, monkeypatch):
    """No --model: Settings.default_captioner, not a literal the CLI kept (a configured one was ignored)."""
    import codeverse3d.addons.dataset.captions as cap
    from codeverse3d.config import get_settings

    seen: list[str] = []
    monkeypatch.setattr(get_settings(), "default_captioner", "fake:configured")
    monkeypatch.setattr(cap, "caption_sample", lambda ws, rec, model, **kw: seen.append(model) or SimpleNamespace(instruction="x"))
    r = runner.invoke(app, ["flywheel", "caption", "wooden_chair_ab12cd34", "--runs-dir", str(runs_dir), "--out",
                            str(runs_dir.parent / "side")])
    assert r.exit_code == 0, r.output
    assert seen == ["fake:configured"]


# --------------------------------------------------- the configuration sweep (2026-09-23): refused up front, cleanly
@pytest.mark.parametrize("flag,value", [
    ("--generator", "antigravity:gemini-3.7-flash"), ("--generator", "nocolon"), ("--generator", "single-shot:nope"),
    ("--generator", "single-shot:gemini:"), ("--planner", "gemini"), ("--judge", "bogus:model"),
    ("--captioner", "gemini:"),
], ids=lambda v: v.strip("-"))
def test_make_refuses_a_backend_id_it_cannot_build_before_the_run_exists(tmp_path: Path, flag: str, value: str):
    """An unknown id used to surface after the PAID plan stage, as a traceback and an orphan run."""
    r = runner.invoke(app, ["make", "a cup", flag, value, "--no-run", "--runs-dir", str(tmp_path / "runs"), "--slug", "b"])
    assert r.exit_code == 2 and flag in r.output and "Traceback" not in r.output
    assert not (tmp_path / "runs" / "b").exists()


@pytest.mark.parametrize("args", [["--dim", "height=0"], ["--dim", "height=-1"], ["--dim", "height=nan"],
                                  ["--dim", "height=inf"], ["--dim", "=1"]], ids=lambda a: a[-1])
def test_make_refuses_a_dimension_that_is_not_a_positive_length(tmp_path: Path, args: list[str]):
    r = runner.invoke(app, ["make", "a cup", *args, "--no-run", "--runs-dir", str(tmp_path / "runs"), "--slug", "d"])
    assert r.exit_code == 1 and "--dim" in r.output and not (tmp_path / "runs" / "d").exists()


def test_make_refuses_a_blank_prompt(tmp_path: Path):
    r = runner.invoke(app, ["make", "  ", "--no-run", "--runs-dir", str(tmp_path / "runs")])
    assert r.exit_code == 2 and "prompt is empty" in r.output
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


