"""Offline: GraphicsTrack end-to-end with fakes (no GL, no network) + planner + prompts + rubric."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from codeverse.config import Settings
from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.common import Backends, Budget, Language, Track
from codeverse.contracts.plan import GraphicsPlan
from codeverse.contracts.run import RunStatus
from codeverse.contracts.spec import Constraints, Spec
from codeverse.judges.rubrics import load_rubric
from codeverse.languages._gl_common import finish_build, judge_times, preview_times
from codeverse.languages.glsl_shader import lint_workspace, write_skeleton
from codeverse.proc import EventLog
from codeverse.prompts import render
from codeverse.spatial.gl_render import GlFrame, GlResult
from codeverse.tracks import get_track
from codeverse.tracks.graphics import (
    GraphicsPipeline,
    GraphicsTrack,
    ensure_graphics_acceptance,
    graphics_prompt_context,
    plan_example,
)
from codeverse.tracks.planner import PlanningError
from codeverse.workspace import Workspace
from tests.orchestrator_tracks.fakes import FakeAgent, FakeChatModel, FakeJudge, FakeServices

FAIL_MARK = "RAISE_BUILD_ERROR"
STATIC_MARK = "STATIC_FRAMES"


def make_spec(language: Language = Language.GLSL_SHADER, *, generator: str = "fake:fake-model", max_rounds: int = 3) -> Spec:
    return Spec(id="g1", track=Track.GRAPHICS, language=language, prompt="animated neon rain on a window with bokeh city lights",
                constraints=Constraints(must_have=["bokeh lights"]),
                budget=Budget(max_rounds=max_rounds, max_minutes=10, max_repair_attempts=2),
                backends=Backends(planner="fake:planner", generator=generator, judge="fake:judge"))


def _frame(path: Path, t: float, static: bool) -> None:
    x = np.linspace(0, 1, 64, dtype=np.float32)[None, :].repeat(36, 0)
    shift = 0.0 if static else 0.3 * t
    rgb = np.stack([0.5 + 0.5 * np.sin(6 * (x + shift)), x * 0.5 + 0.2, 0.6 - 0.4 * x], -1)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((rgb * 255).astype(np.uint8), "RGB").save(path)


class FakeGlRuntime:
    """glsl_shader runtime without GL: writes synthetic frames through the real ``finish_build``."""

    language = Language.GLSL_SHADER
    entry_globs = ("src/shader.frag", "src/common.glsl")

    def __init__(self) -> None:
        self.builds = 0

    def skeleton(self, ws: Workspace, plan: Any) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    def build(self, ws: Workspace, *, timeout_s: int | None = None, **kw: Any) -> BuildResult:
        self.builds += 1
        src = (ws.src / "shader.frag").read_text()
        if FAIL_MARK in src:
            res = GlResult(ok=False, mode="shader", stage="compile", error_type="GlslCompileError",
                           error_message="src/shader.frag:3: error: `nope' undeclared")
            return finish_build(ws, res, language="glsl_shader", error_file="src/shader.frag", error_line=3)
        frames = []
        for i, t in enumerate(judge_times(8.0) + preview_times(8.0, 4)):
            p = ws.artifacts / "frames" / f"f{i:02d}_t{t:06.2f}.png"
            _frame(p, t, STATIC_MARK in src)
            frames.append(GlFrame(index=i, time=t, path=str(p), judge=i < 5))
        res = GlResult(ok=True, mode="shader", stage="render", renderer="fake-gl", frames=frames)
        return finish_build(ws, res, language="glsl_shader", census={"convention": "mainImage"})

    def contract_doc(self) -> str:
        return "FAKE glsl_shader authoring contract"  # the runtime's contract_doc is what the prompt sees


def _writer(job, ws):
    body = "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n  vec2 uv = fragCoord / u_resolution.xy;\n"
    body += f"  // {job.label} r{job.round}\n  fragColor = vec4(uv, 0.5 + 0.5 * sin(u_time), 1.0);\n}}\n"
    return {"src/shader.frag": body}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")


def _services(**kw):
    return FakeServices(runtime_factory=lambda lang: FakeGlRuntime(), **kw)


def _plan(spec, ws, model, budget=None):
    """What plan_graphics() used to be: run_planner with GraphicsTrack's own hooks.
    The wrapper was a second spelling of _plan_kwargs and had no production caller,
    so the tests go through the hooks the live path actually uses."""
    from codeverse.tracks.graphics import GraphicsTrack
    from codeverse.tracks.planner import plan as run_planner
    return run_planner(spec, "fake:planner", GraphicsPlan, ws, model=model, budget=budget,
                       **GraphicsTrack()._plan_kwargs(spec))


def test_graphics_track_end_to_end(tmp_path, settings):
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "rain")
    judge = FakeJudge(scores=(0.55, 0.72, 0.9), targets=("RainDrops", "CityBokeh"))
    agent = FakeAgent(_writer)
    runtime = FakeGlRuntime()
    track = GraphicsTrack(services=_services(), judge=judge, agent=agent, planner_model=FakeChatModel(lambda req: plan_example()),
                          settings=settings, runtime=runtime)
    assert isinstance(get_track(Track.GRAPHICS), GraphicsTrack)
    rec = track.run(spec, ws)
    assert rec.status is RunStatus.PASSED and [r.kind for r in rec.rounds] == ["baseline", "refine", "refine"]
    assert rec.baseline_score == pytest.approx(0.55) and rec.final_score == pytest.approx(0.9) and rec.best_round == 2
    assert rec.extra["rubric"] == "shader_v2"
    plan = GraphicsPlan.model_validate(json.loads(ws.plan_path.read_text()))
    assert any(a.text.startswith("Includes: bokeh") for a in plan.acceptance)
    # renders are the frames (views named t=<s>s) + a sheet, per round
    r0 = rec.rounds[0]
    assert r0.renders is not None and [v.name for v in r0.renders.views] == ["t=0s", "t=1s", "t=2.5s", "t=4s", "t=6s"]
    assert r0.renders.views[2].time_s == 2.5 and Path(r0.renders.contact_sheet).is_file()
    assert (ws.renders_dir(0) / "sheet.png").is_file() and (ws.artifacts / "preview.gif").is_file()
    # gates: lint + gl_frames; no measurement
    assert {g.gate for g in r0.gates} == {"lint:glsl_shader", "gl_frames"} and r0.measurement is None
    assert (ws.gates_dir(0) / "gl_frames.json").is_file()
    # judge saw frame metrics in the extra context and the plan digest
    inp = judge.calls[0]
    assert "FRAME METRICS" in inp.extra_context and "mean_lum" in inp.extra_context and "CityBokeh" in inp.plan_summary
    # prompts: baseline is concrete (passes table, key visuals, contract, tools), refine carries judge + metrics
    p0 = agent.jobs[0].prompt
    assert "| RainDrops |" in p0 and "rain drops with trails" in p0 and "glsl_shader authoring contract" in p0 and "gl_frames" in p0
    # the brief's recipes were seeded into the harness-owned src/recipes.glsl before the session (measured
    # 2026-08-26, seed_v1: seeded into common.glsl they were overwritten), the prompt names them, every
    # session's job carries the file as read-only, and common.glsl stays the agent's (skeleton, minus the
    # helpers recipes.glsl now provides)
    recipes = (ws.src / "recipes.glsl").read_text()
    assert recipes.startswith("// harness-owned:") and "vec3 bokehSoft(vec2 p, float t)" in recipes and "vec2 dropsLayer(" in recipes
    common = (ws.src / "common.glsl").read_text()
    assert "// harness-owned:" not in common and "bokehSoft" not in common and common.startswith("// src/common.glsl")
    assert "provided by src/recipes.glsl" in common.splitlines()[1]      # the skeleton's own hash/noise/fbm were trimmed
    assert all(j.read_only == ["src/recipes.glsl"] for j in agent.jobs) and all(j.edit_only is False for j in agent.jobs[:1])
    assert "Verified helpers in the harness-owned, read-only `src/recipes.glsl`" in p0 and "`vec3 bokehSoft(vec2 p, float t)`" in p0
    assert "harness-owned, read-only `src/recipes.glsl`" in agent.jobs[1].prompt and "`bokehSoft`" in agent.jobs[1].prompt
    p1 = agent.jobs[1].prompt
    assert "Refine" in p1 and "Frame metrics" in p1 and "Previous score 0.55" in p1
    assert rec.rounds[1].instructions and all("[judge/" in i or "[gate/" in i for i in rec.rounds[1].instructions)
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    for k in ("plan.done", "recipes.seeded", "round.start", "build.done", "gates.done", "judge.done", "refine.planned", "stop", "run.done"):
        assert k in kinds, k
    seeded = next(e for e in EventLog(ws.events_path).read() if e["event"] == "recipes.seeded")
    assert {"dropsLayer", "bokehSoft"} <= set(seeded["names"]) and seeded["present"] == seeded["names"]   # + the night-sky chapter


def test_static_frames_become_a_gate_warning_and_refine_task(tmp_path, settings):
    spec = make_spec(max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "static")

    def writer(job, ws_):
        return {"src/shader.frag": f"// {STATIC_MARK}\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) {{ fragColor = vec4(1.0, 0.5, 0.2, 1.0); }}\n"}

    judge = FakeJudge(scores=(0.5, 0.5), targets=("overall",))
    track = GraphicsTrack(services=_services(), judge=judge, agent=FakeAgent(writer), planner_model=FakeChatModel(lambda req: plan_example()),
                          settings=settings, runtime=FakeGlRuntime())
    rec = track.run(spec, ws)
    gl = next(g for g in rec.rounds[0].gates if g.gate == "gl_frames")
    assert gl.passed and any(f.data["kind"] == "static" for f in gl.findings)
    # the gate's warning is visible to the judge input and the refine round carried a task
    assert "static" in judge.calls[0].extra_context.lower()


def test_build_failure_routes_to_rebuild_task(tmp_path, settings):
    spec = make_spec(max_rounds=2)
    ws = Workspace(tmp_path / "runs" / "broken")
    calls = {"n": 0}

    def writer(job, ws_):
        calls["n"] += 1
        if calls["n"] <= 3:  # baseline + 2 repair attempts keep failing
            return {"src/shader.frag": f"void mainImage(out vec4 fragColor, in vec2 fragCoord) {{ fragColor = vec4(nope); }} // {FAIL_MARK} {calls['n']}\n"}
        return _writer(job, ws_)

    agent = FakeAgent(writer)
    track = GraphicsTrack(services=_services(), judge=FakeJudge(scores=(0.8,)), agent=agent, planner_model=FakeChatModel(lambda req: plan_example()),
                          settings=settings, runtime=FakeGlRuntime())
    rec = track.run(spec, ws)
    assert rec.rounds[0].build is not None and not rec.rounds[0].build.ok
    assert rec.rounds[0].build.error_file == "src/shader.frag" and rec.rounds[0].build.error_line == 3
    assert rec.rounds[1].kind == "refine" and rec.rounds[1].instructions == ["rebuild: previous round did not build"]
    assert rec.rounds[1].build is not None and rec.rounds[1].build.ok
    labels = [j.label for j in agent.jobs]
    assert labels[0] == "baseline" and any(lb.startswith("r00_baseline_repair") for lb in labels) and "rebuild" in labels


def test_planner_reask_and_acceptance(tmp_ws):
    spec = make_spec()
    attempts = {"n": 0}

    def responder(req):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return {"title": "x", "summary": "y", "style": "z", "passes": []}  # invalid: passes min_length=1
        return plan_example()

    model = FakeChatModel(responder)
    plan = _plan(spec, tmp_ws, model)
    assert attempts["n"] == 2 and plan.title == "Neon rain on a window" and tmp_ws.plan_path.is_file()
    assert "Fix EXACTLY these problems" in model.requests[1].messages[-1].text
    ids = [a.id for a in plan.acceptance]
    assert "must1" in ids and "a3" in ids  # spec must_have appended, motion item already present in the example
    # a plan without a motion acceptance item gets one; no 'ground contact' ever
    p2 = ensure_graphics_acceptance(GraphicsPlan.model_validate({**plan_example(), "acceptance": []}), spec)
    assert any(a.id.startswith("motion") for a in p2.acceptance) and not any("ground" in a.text.lower() for a in p2.acceptance)
    always_bad = FakeChatModel(lambda req: {"title": "x"})
    with pytest.raises(PlanningError):
        _plan(spec, tmp_ws, always_bad)
    from codeverse.tracks.graphics import PLAN_TEMPLATE
    from codeverse.tracks.planner import build_system_prompt
    sys_prompt = build_system_prompt(spec, GraphicsPlan, template=PLAN_TEMPLATE, example=plan_example())
    assert "ART-DIRECTOR" in sys_prompt and "NeonRainWindow" in sys_prompt


def test_templates_render_and_rubric_loads(tmp_path, settings):
    rubric = load_rubric("shader_v2")
    assert rubric.track_hint == "graphics" and {c.id for c in rubric.criteria} >= {"likeness", "motion_quality", "technical_cleanliness"}
    assert {c.id for c in rubric.caps} >= {"nan_pixels", "static_frames", "black_or_blown", "build_error"}
    spec = make_spec(language=Language.OPENGL_PYTHON, generator="single-shot:fake:fake-model")
    ws = Workspace(tmp_path / "runs" / "tpl").create()
    track = GraphicsTrack(services=_services(), settings=settings, runtime=FakeGlRuntime())
    from codeverse.orchestrator import RunState

    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState.load_or_new(ws, resume=False))
    ctx.plan = GraphicsPlan.model_validate(plan_example())
    base = graphics_prompt_context(ctx, skeleton_files={"src/program.py": "# x"}, previous_error="")
    assert base["expected_files"] == ["src/program.py"] and base["single_shot"] and "=== FILE:" in base["output_format"]
    gen = render("tracks/generate_graphics.j2", **base)
    assert "src/program.py" in gen and "| CityBokeh |" in gen and "1280x720" in gen
    ref = render("tracks/refine_graphics.j2", **graphics_prompt_context(
        ctx, round_index=1, tasks=["[judge/effect] overall: more bokeh"], targets=["overall"], files=["src/program.py"],
        judge_summary="Previous score 0.5", frame_notes="(no frame metrics)", current_files={"src/program.py": "# y"}))
    assert "more bokeh" in ref and "--- src/program.py ---" in ref
    pipe = GraphicsPipeline()
    assert "CityBokeh" in pipe.plan_summary(ctx)
