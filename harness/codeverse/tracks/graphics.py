"""GraphicsTrack: plan (GraphicsPlan) → skeleton → baseline → refine rounds → finalise.

Languages: glsl_shader (Shadertoy-style fragment shader) · opengl_python (raw
moderngl program).  Reuses ``BaseTrack`` (lifecycle) and ``run_round`` (steps)
unchanged; the track-specific pieces are the planner template, the prompt
context (no 3D frame), the ``gl_frames`` gate (frame statistics from the build)
and the render step (the sampled frames + contact sheet as the RenderSet the
``shader_v1`` judge sees).  Refinement is always one whole-program task.

The shared prompt helpers look the language up in ``conventions.LANGUAGE_FRAME``
(repair prompts); graphics languages have no 3D frame, so they are registered
here as Y-up/+Z-front (GL clip space) until conventions.py carries them.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from codeverse.contracts.artifacts import BuildResult, GateReport, Measurement, RenderSet
from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import GraphicsPlan, Plan
from codeverse.contracts.run import RoundRecord
from codeverse.conventions import LANGUAGE_FRAME, Frame
from codeverse.events import EventLog
from codeverse.languages.glsl_shader.gl_build import read_metrics
from codeverse.orchestrator.rounds import TaskGroup, build_refine_instructions
from codeverse.prompts import render
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import GenerationTask
from codeverse.tracks.graphics_steps import (
    expected_files,
    frame_stats_text,
    frames_render_set,
    graphics_prompt_context,
    plan_graphics,
)
from codeverse.tracks.lifecycle import BaseTrack
from codeverse.tracks.repair import format_error_report
from codeverse.tracks.static_object import current_files, judge_digest, skeleton_files
from codeverse.tracks.steps import failed_acceptance
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

for _lang in (Language.GLSL_SHADER, Language.OPENGL_PYTHON):
    LANGUAGE_FRAME.setdefault(_lang.value, Frame.Y_UP_POS_Z_FRONT)


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

    def judge_context(self, ctx: RunContext, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        renderer = build.census.get("renderer", "") if isinstance(build.census, dict) else ""
        return f"FRAME METRICS (harness-measured, renderer {renderer or 'moderngl'}):\n{frame_stats_text(ctx.ws)}"


class GraphicsTrack(BaseTrack):
    track = Track.GRAPHICS
    rubric = "shader_v1"
    plan_model = GraphicsPlan
    generate_template = "tracks/generate_graphics.j2"
    refine_template = "tracks/refine_graphics.j2"

    def make_pipeline(self, ctx: RunContext) -> GraphicsPipeline:
        return GraphicsPipeline()

    # ------------------------------------------------------------------ planning (own template / acceptance)
    def plan(self, spec, ws: Workspace) -> Plan:
        ws.create()
        events = EventLog(ws.events_path)
        runtime = self._runtime or self.services.runtime(spec.language)
        return plan_graphics(spec, spec.backends.planner, ws, model=self._planner_model, events=events, runtime=runtime)

    def _plan_stage(self, ctx: RunContext) -> Plan:
        from codeverse.contracts.run import RunStatus

        ctx.state.status = RunStatus.PLANNING
        ctx.state.save(ctx.ws)
        plan = plan_graphics(ctx.spec, ctx.spec.backends.planner, ctx.ws, model=self._planner_model, events=ctx.events,
                             budget=ctx.budget, runtime=ctx.runtime)
        self._save_spent(ctx)
        return plan

    # ------------------------------------------------------------------ baseline
    def system_prompt(self, ctx: RunContext) -> str:
        what = "GLSL fragment-shader artist (Shadertoy style)" if ctx.language is Language.GLSL_SHADER else "raw OpenGL (moderngl) graphics programmer"
        return (f"You are an expert {what} writing RAW code for a headless harness. Follow the contract exactly: "
                f"no #version/uniform redeclarations (shader) · no window/context creation (program) · everything animates with time. "
                f"Render and LOOK at your frames before finishing.")

    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        files = expected_files(ctx)
        prompt = render(self.generate_template, **graphics_prompt_context(
            ctx, skeleton_files=skeleton_files(ctx) if ctx.single_shot else {}, previous_error=""))
        ctx.record_prompt("generate", prompt)
        return [GenerationTask(label="baseline", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=0,
                               kind="baseline", temperature=0.6, thinking="medium")]

    def round_files_hint(self, ctx: RunContext) -> list[str]:
        return expected_files(ctx)

    # ------------------------------------------------------------------ refine
    def refine_tasks(self, ctx: RunContext, last: RoundRecord, history: Sequence[RoundRecord]) -> tuple[list[GenerationTask], list[str]]:
        index = len(history)
        if last.build is None or not last.build.ok:
            return [self._rebuild_task(ctx, last, index)], ["rebuild: previous round did not build"]
        tasks = build_refine_instructions(last.judgment, last.gates, failed_acceptance(ctx, last.judgment), ctx.plan,
                                          file_for_target=None, max_tasks=ctx.policy.max_refine_tasks)
        if not tasks:
            return [], []
        group = TaskGroup(tasks=tasks, files=expected_files(ctx))
        prompt = render(self.refine_template, **graphics_prompt_context(
            ctx, round_index=index, tasks=[t.line() for t in tasks], targets=group.targets, files=group.files,
            judge_summary=judge_digest(last), frame_notes=frame_stats_text(ctx.ws),
            current_files=current_files(ctx, group.files) if ctx.single_shot else {}))
        ctx.record_prompt("refine", prompt)
        ctx.events.emit("refine.planned", round=index, n_tasks=len(tasks), n_groups=1, parallel=False, targets=[group.targets])
        task = GenerationTask(label="refine", prompt=prompt, system=self.system_prompt(ctx), files_hint=group.files, round=index,
                              kind="refine", temperature=0.5, thinking="medium")
        return [task], [t.line() for t in tasks]

    def _rebuild_task(self, ctx: RunContext, last: RoundRecord, index: int) -> GenerationTask:
        files = expected_files(ctx)
        lint = next((g for g in last.gates if g.gate.startswith("lint")), GateReport(gate="lint", passed=True))
        report = format_error_report(last.build, lint, ctx.cookbook_text) if last.build else "build did not run"
        prompt = render(self.generate_template, **graphics_prompt_context(
            ctx, skeleton_files=current_files(ctx, files) if ctx.single_shot else {}, previous_error=report))
        return GenerationTask(label="rebuild", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=index,
                              kind="rebuild", temperature=0.7, thinking="high")
