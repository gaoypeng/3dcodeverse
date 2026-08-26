"""GraphicsTrack: plan (GraphicsPlan) → skeleton → baseline → refine rounds → finalise.

Languages: glsl_shader (Shadertoy-style fragment shader) · opengl_python (raw
moderngl program).  Reuses ``BaseTrack`` (lifecycle) and ``run_round`` (steps)
unchanged; the track-specific pieces are the planner hooks (template / example /
acceptance in ``graphics_steps``), the prompt context (no 3D frame), the
``gl_frames`` gate (frame statistics from the build) and the render step (the
sampled frames + contact sheet as the RenderSet the ``shader_v2`` judge sees).
Refinement is always one whole-program task.
"""

from __future__ import annotations

import logging
from typing import Any

from codeverse.contracts.artifacts import BuildResult, GateReport, Measurement, RenderSet
from codeverse.contracts.common import TRACK_INFO, Language, Track
from codeverse.contracts.plan import GraphicsPlan, Plan
from codeverse.contracts.run import RoundRecord
from codeverse.contracts.spec import Spec
from codeverse.languages._gl_common import read_metrics
from codeverse.orchestrator.rounds import TaskGroup
from codeverse.prompts import render
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import GenerationTask
from codeverse.tracks.graphics_steps import (
    PLAN_MAX_OUTPUT_TOKENS,
    PLAN_TEMPERATURE,
    PLAN_TEMPLATE,
    ensure_graphics_acceptance,
    frame_stats_text,
    frames_render_set,
    graphics_event_stats,
    graphics_expected_files,
    graphics_prompt_context,
)
from codeverse.tracks.graphics_steps import plan_example as graphics_plan_example
from codeverse.tracks.lifecycle import BaseTrack
from codeverse.tracks.prompting import (
    current_files,
    judge_digest,
    judged_sheet,
    reference_images,
    skeleton_files,
)
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)


class GraphicsPipeline:
    """No measurement; gates = gl_frames (from artifacts/metrics.json); render = the frames."""

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None:
        return None

    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]:
        m = read_metrics(ctx.ws)
        if m is None:
            return []
        return [m[1]]

    def render(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> RenderSet:
        return frames_render_set(ctx.ws, build, round_index)

    def plan_summary(self, ctx: RunContext) -> str:
        plan = ctx.plan
        if not isinstance(plan, GraphicsPlan):
            return ""
        passes = ", ".join(f"{p.name} ({p.kind})" for p in plan.passes)
        visuals = "; ".join(plan.key_visuals)
        return (f"{plan.title}: {plan.summary} Style: {plan.style}. Passes: {passes}. Key visuals: {visuals}. "
                f"Motion: {plan.motion or '(none planned)'}. {plan.resolution[0]}x{plan.resolution[1]}, loop {plan.duration_s:g}s.")

    def judge_context(self, ws: Workspace, plan: Plan | None, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        renderer = build.census.get("renderer", "") if isinstance(build.census, dict) else ""
        return f"FRAME METRICS (harness-measured, renderer {renderer or 'moderngl'}):\n{frame_stats_text(ws)}"


class GraphicsTrack(BaseTrack):
    track = Track.GRAPHICS
    rubric = TRACK_INFO[Track.GRAPHICS].rubric
    plan_model = GraphicsPlan
    generate_template = "tracks/generate_graphics.j2"
    refine_template = "tracks/refine_graphics.j2"
    # planner hooks: own template/example/acceptance, T=0.5, 16k tokens (no 3D frame)
    plan_template = PLAN_TEMPLATE
    plan_temperature = PLAN_TEMPERATURE
    plan_max_output_tokens = PLAN_MAX_OUTPUT_TOKENS
    # refinement is always ONE whole-program task
    allow_refine_fanout = False

    def make_pipeline(self) -> GraphicsPipeline:
        return GraphicsPipeline()

    # ------------------------------------------------------------------ planner hooks
    def plan_example(self, spec: Spec) -> dict[str, Any]:
        return graphics_plan_example()

    def finalise_plan(self, plan_obj: Any, spec: Spec) -> Any:
        return ensure_graphics_acceptance(plan_obj, spec)

    def plan_event_stats(self, plan_obj: Any) -> dict[str, Any]:
        return graphics_event_stats(plan_obj)

    # ------------------------------------------------------------------ baseline
    def system_prompt(self, ctx: RunContext) -> str:
        what = "GLSL fragment-shader artist (Shadertoy style)" if ctx.language is Language.GLSL_SHADER else "raw OpenGL (moderngl) graphics programmer"
        return (f"You are an expert {what} writing RAW code for a headless harness. Follow the contract exactly: "
                f"no #version/uniform redeclarations (shader) · no window/context creation (program) · everything animates with time. "
                f"Render and LOOK at your frames before finishing.")

    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        files = graphics_expected_files(ctx)
        prompt = render(self.generate_template, **graphics_prompt_context(
            ctx, skeleton_files=skeleton_files(ctx) if ctx.single_shot else {}, previous_error=""))
        ctx.record_prompt("generate", prompt)
        return [GenerationTask(label="baseline", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=0,
                               kind="baseline", temperature=0.6, thinking="medium", images=reference_images(ctx))]

    def round_files_hint(self, ctx: RunContext) -> list[str]:
        return graphics_expected_files(ctx)

    def generate_context(self, ctx: RunContext, **extra: Any) -> dict[str, Any]:
        return graphics_prompt_context(ctx, **extra)

    # ------------------------------------------------------------------ refine (scaffold hook; never fans out)
    def _refine_task(self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool) -> GenerationTask:
        files = graphics_expected_files(ctx)
        prompt = render(self.refine_template, **graphics_prompt_context(
            ctx, round_index=index, tasks=[t.line() for t in group.tasks], targets=group.targets, files=files,
            judge_summary=judge_digest(last), frame_notes=frame_stats_text(ctx.ws),
            current_files=current_files(ctx, files) if ctx.single_shot else {}))
        ctx.record_prompt("refine", prompt)
        return GenerationTask(label="refine", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=index,
                              kind="refine", temperature=0.5, thinking="medium",
                              images=reference_images(ctx) + judged_sheet(last))
