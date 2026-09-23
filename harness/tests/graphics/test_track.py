"""Offline: the graphics track with fakes (no GL, no network) — judge replay and planner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from codeverse3d.config import Settings
from codeverse3d.contracts.artifacts import BuildResult
from codeverse3d.contracts.common import Backends, Budget, Language, Track
from codeverse3d.contracts.plan import GraphicsPlan
from codeverse3d.contracts.spec import Constraints, Spec
from codeverse3d.languages._gl_common import finish_build, judge_times
from codeverse3d.languages.glsl_shader import GlslShaderRuntime
from codeverse3d.spatial.gl_render import GlFrame, GlResult, gif_times
from codeverse3d.tracks import planner
from codeverse3d.tracks.graphics import GraphicsTrack
from codeverse3d.tracks.planner import PlanningError, ensure_acceptance
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.fakes import FakeAgent, FakeChatModel, FakeJudge, FakeServices


def make_spec(*, max_rounds: int = 3) -> Spec:
    return Spec(id="g1", track=Track.GRAPHICS, language=Language.GLSL_SHADER, prompt="animated neon rain on a window with bokeh city lights",
                constraints=Constraints(must_have=["bokeh lights"]),
                budget=Budget(max_rounds=max_rounds, max_minutes=10, max_repair_attempts=2),
                backends=Backends(planner="fake:planner", generator="fake:fake-model", judge="fake:judge"))


def _frame(path: Path, t: float) -> None:
    x = np.linspace(0, 1, 64, dtype=np.float32)[None, :].repeat(36, 0)
    shift = 0.3 * t
    rgb = np.stack([0.5 + 0.5 * np.sin(6 * (x + shift)), x * 0.5 + 0.2, 0.6 - 0.4 * x], -1)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((rgb * 255).astype(np.uint8), "RGB").save(path)


class FakeGlRuntime(GlslShaderRuntime):
    """glsl_shader runtime without GL: writes synthetic frames through the real ``finish_build``
    (the real runtime's layout, skeleton and lint)."""

    entry_globs = ("src/shader.frag", "src/common.glsl")

    def build(self, ws: Workspace, *, timeout_s: int | None = None, **kw: Any) -> BuildResult:
        frames = []
        for i, t in enumerate(judge_times(8.0) + gif_times(8.0, 4)):
            p = ws.artifacts / "frames" / f"f{i:02d}_t{t:06.2f}.png"
            _frame(p, t)
            frames.append(GlFrame(index=i, time=t, path=str(p), judge=i < 5))
        res = GlResult(ok=True, mode="shader", stage="render", renderer="fake-gl", frames=frames)
        return finish_build(ws, res, language="glsl_shader", census={"convention": "mainImage"})


def _writer(job, ws):
    body = "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n  vec2 uv = fragCoord / u_resolution.xy;\n"
    body += f"  // {job.label} r{job.round}\n  fragColor = vec4(uv, 0.5 + 0.5 * sin(u_time), 1.0);\n}}\n"
    return {"src/shader.frag": body}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")


def _services(**kw):
    return FakeServices(runtime_factory=lambda lang: FakeGlRuntime(), **kw)


def plan_example() -> dict[str, Any]:
    return planner.plan_example(Track.GRAPHICS)


def _plan(spec, ws, model, budget=None):
    """The planner as the live path calls it (graphics dispatch happens on ``spec.track``)."""
    return planner.plan(spec, "fake:planner", GraphicsPlan, ws, model=model, budget=budget)


def test_a_replayed_round_reads_the_payload_the_in_run_judge_read(tmp_path, settings):
    """``3dcode judge`` / calibration rebuild the payload the in-run judge read (``judges.base.round_input``)."""
    from codeverse3d.cli._judge import build_judge_input
    from codeverse3d.record.record import load_record

    ws = Workspace(tmp_path / "runs" / "replay")
    judge = FakeJudge(scores=(0.55, 0.72), targets=("RainDrops",))
    track = GraphicsTrack(services=_services(), judge=judge, agent=FakeAgent(_writer),
                          planner_model=FakeChatModel(lambda req: plan_example()), settings=settings, runtime=FakeGlRuntime())
    rec = track.run(make_spec(max_rounds=1), ws)
    replay = build_judge_input(ws, load_record(ws), rec.rounds[-1])
    for field in ("plan_summary", "extra_context", "acceptance", "round_index"):
        assert getattr(replay, field) == getattr(judge.calls[-1], field), field
    assert "Passes: CityBokeh (fullscreen)" in replay.plan_summary and "Motion: drops slide down" in replay.plan_summary


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
    p2 = ensure_acceptance(GraphicsPlan.model_validate({**plan_example(), "acceptance": []}), spec)
    assert any(a.id.startswith("motion") for a in p2.acceptance) and not any("ground" in a.text.lower() for a in p2.acceptance)
    always_bad = FakeChatModel(lambda req: {"title": "x"})
    with pytest.raises(PlanningError):
        _plan(spec, tmp_ws, always_bad)
    sys_prompt = planner.build_system_prompt(spec, GraphicsPlan)
    assert "ART-DIRECTOR" in sys_prompt and "NeonRainWindow" in sys_prompt
    assert model.requests[0].temperature == 0.5 and model.requests[0].max_output_tokens == planner.PLAN_TOKENS_MAX
