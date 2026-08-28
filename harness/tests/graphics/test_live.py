"""Live (network + Gemini keys): graphics planner → single-shot shader → real GL build → VLM judge."""

from __future__ import annotations

import pytest

from codeverse.config import get_settings
from codeverse.contracts.common import Backends, Budget, Language, Track
from codeverse.contracts.plan import GraphicsPlan
from codeverse.contracts.spec import Spec
from codeverse.judges.base import JudgeInput
from codeverse.judges.vlm_judge import VlmJudge
from codeverse.languages.glsl_shader.runtime import GlslShaderRuntime
from codeverse.tracks.generation import generate_files
from codeverse.tracks.graphics import (
    GraphicsTrack,
    frames_render_set,
    graphics_prompt_context,
    plan_graphics,
)
from codeverse.workspace import Workspace

pytestmark = pytest.mark.live
MODEL = "gemini:gemini-3.7-flash"


@pytest.fixture(scope="module")
def keys_ok():
    if not get_settings().gemini_api_keys:
        pytest.skip("no Gemini keys")


def test_live_plan_generate_build_judge(tmp_path, keys_ok):
    from codeverse.events import EventLog
    from codeverse.models import get_chat_model
    from codeverse.orchestrator.state import RunState
    from codeverse.prompts import render

    spec = Spec(id="live_gfx", track=Track.GRAPHICS, language=Language.GLSL_SHADER,
                prompt="a calm animated aurora borealis over snowy mountains with twinkling stars",
                budget=Budget(max_rounds=1, max_usd=0.5), backends=Backends(planner=MODEL, generator=f"single-shot:{MODEL}", judge=MODEL))
    ws = Workspace(tmp_path / "live_gfx").create()
    model = get_chat_model(MODEL)
    plan = plan_graphics(spec, MODEL, ws, model=model)
    assert isinstance(plan, GraphicsPlan) and plan.passes and plan.key_visuals and plan.motion
    rt = GlslShaderRuntime()
    rt.skeleton(ws, plan)
    track = GraphicsTrack(runtime=rt)
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState.load_or_new(ws, resume=False))
    ctx.plan = plan
    ctx.model = model
    task = track.baseline_tasks(ctx)[0]
    res = generate_files(ws, model=model, task=task)
    assert res.ok and any(c.path.endswith("shader.frag") for c in res.files_changed)
    lint = rt.lint(ws)
    build = rt.build(ws, width=640, height=360)
    assert lint.gate == "lint:glsl_shader"
    if not build.ok:
        pytest.skip(f"model wrote a shader that does not compile (repair loop not exercised here): {build.error_message[:200]}")
    renders = frames_render_set(ws, build, 0)
    assert len(renders.views) == 5 and renders.contact_sheet
    judge = VlmJudge(rubric="shader_v1", model_id=MODEL, n_samples=1)
    j = judge.judge(JudgeInput(spec=spec, renders=renders, gates=[lint], acceptance=plan.acceptance, plan_summary=plan.summary,
                               round_index=0, extra_context=""))
    assert 0.0 <= j.overall <= 1.0 and set(j.scores) >= {"brief_fidelity", "motion_quality"}
    assert render("tracks/refine_graphics.j2", **graphics_prompt_context(
        ctx, round_index=1, tasks=["x"], targets=["overall"], files=["src/shader.frag"], judge_summary="s", frame_notes="f", current_files={}))
