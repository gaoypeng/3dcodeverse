"""StaticObjectTrack: plan → skeleton → baseline → refine rounds → finalise.

Languages: blender (bpy) · cadquery · threejs.  Per-part parallel refinement
is used when the language owns one file per part (threejs) and ≥ 3
file-disjoint task groups exist; otherwise one whole-object refine task.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse.contracts.artifacts import BuildResult, GateReport, Measurement, RenderSet
from codeverse.contracts.common import TRACK_INFO, Track
from codeverse.contracts.plan import Plan, StaticPlan
from codeverse.contracts.run import RoundRecord
from codeverse.conventions import BBOX_TOLERANCE_M, OBJECT_VIEWS
from codeverse.orchestrator.rounds import RefineTask, TaskGroup, compact_instructions
from codeverse.prompts import render
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import GenerationTask
from codeverse.tracks.lifecycle import BaseTrack
from codeverse.tracks.prompting import (
    MAX_SKELETON_CHARS,  # noqa: F401 — re-exported: this was their import path
    base_prompt_context,
    current_files,
    expected_files,
    file_for_target_factory,
    glb_to_plan_frame,
    judge_digest,
    measurement_vs_plan,
    reference_images,
    skeleton_files,
)
from codeverse.tracks.reference import reference_refine_tasks, silhouette_gate
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

#: the 4 clay views judged for holes/intersections (subset of OBJECT_VIEWS by name)
GEOMETRY_VIEW_NAMES: tuple[str, ...] = ("front_right_34", "back_left_34", "top", "low_front_left")
GEOMETRY_VIEWS = tuple(v for v in OBJECT_VIEWS if v.name in GEOMETRY_VIEW_NAMES)


class ObjectPipeline:
    """Measure → connectivity + contract (+ runtime extra gates) → 8-view render."""

    views = OBJECT_VIEWS

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None:
        return ctx.services.measure(Path(build.glb_path)) if build.glb_path else None

    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]:
        out: list[GateReport] = []
        glb = Path(build.glb_path) if build.glb_path else None
        if glb is not None:
            out.append(ctx.services.connectivity(glb, ctx.language.value))  # fix hints in the author's frame
        if measurement is not None and ctx.plan is not None:
            out.append(ctx.services.contract(measurement, ctx.plan, BBOX_TOLERANCE_M, ctx.language.value))
        extra = getattr(ctx.runtime, "extra_gates", None)
        if callable(extra):
            out.extend(extra(ctx.ws, build))
        return out

    def render(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> RenderSet:
        r = ctx.settings.render
        return ctx.services.render_object(Path(build.glb_path), ctx.ws.renders_dir(round_index), views=self.views,
                                          width=r.width, height=r.height)

    def post_render_gates(self, ctx: RunContext, round_index: int, renders: RenderSet) -> list[GateReport]:
        """Reference-image runs: front-view outline IoU vs the target image (WARN finding with the number)."""
        gate = silhouette_gate(ctx, renders)
        return [gate] if gate is not None else []

    def geometry_views(self, ctx: RunContext, round_index: int, build: BuildResult) -> RenderSet | None:
        """4 clay views for the judge's geometry montage (holes, intersections).

        The plain-geometry render exposes defects that materials hide; cached, so
        near-free.  Object tracks only — scenes/graphics have no single GLB."""
        if not build.glb_path:
            return None
        out_dir = ctx.ws.renders_dir(round_index) / "clay"
        try:
            return ctx.services.render_geometry(Path(build.glb_path), out_dir, views=GEOMETRY_VIEWS)
        except Exception as e:  # noqa: BLE001 — optional judge context, never round-fatal
            log.warning("clay geometry render failed: %s", e)
            ctx.events.emit("render.geometry_failed", round=round_index, error=f"{type(e).__name__}: {e}")
            return None

    def plan_summary(self, ctx: RunContext) -> str:
        plan = ctx.plan
        if plan is None:
            return ""
        parts = ", ".join(f"{p.name}×{p.instances}" if p.instances > 1 else p.name for p in plan.parts)
        # reorder into the measurement table's frame (W×H×D, Y-up): the (x,|z|,y) swap is
        # self-inverse for extents, so Z-up plans (W,D,H) → (W,H,D), threejs is identity —
        # otherwise the judge compares the digest position-wise against swapped numbers.
        e = glb_to_plan_frame(plan.overall_bbox.extents, ctx.language, extents=True)
        return f"{plan.object_name}: {plan.summary} Overall {e[0]:.2f}×{e[1]:.2f}×{e[2]:.2f} m (W×H×D). Parts: {parts}."

    def judge_context(self, ws: Workspace, plan: Plan | None, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        return ""


class StaticObjectTrack(BaseTrack):
    track = Track.STATIC_OBJECT
    rubric = TRACK_INFO[Track.STATIC_OBJECT].rubric
    plan_model = StaticPlan
    generate_template = "tracks/generate_static.j2"
    refine_template = "tracks/refine_object.j2"

    def make_pipeline(self) -> ObjectPipeline:
        return ObjectPipeline()

    # ------------------------------------------------------------------ baseline
    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        files = expected_files(ctx)
        prompt = render(self.generate_template, **base_prompt_context(
            ctx, expected_files=files, skeleton_files=skeleton_files(ctx) if ctx.single_shot else {}, previous_error=""))
        ctx.record_prompt("generate", prompt)
        return [GenerationTask(label="baseline", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=0,
                               kind="baseline", temperature=0.5, thinking="medium", images=reference_images(ctx))]

    def system_prompt(self, ctx: RunContext) -> str:
        return (f"You are an expert {ctx.language.value} 3D modeller writing RAW code (no SDKs, no helper libraries). "
                f"Follow the contract exactly; exact numbers beat adjectives.")

    def round_files_hint(self, ctx: RunContext) -> list[str]:
        return expected_files(ctx)

    # ------------------------------------------------------------------ refine (scaffold hooks)
    def refine_file_for_target(self, ctx: RunContext) -> Any:
        return file_for_target_factory(ctx)

    def extra_refine_tasks(self, ctx: RunContext, last: RoundRecord) -> Sequence[RefineTask]:
        return reference_refine_tasks(ctx, last)

    def _refine_task(self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool) -> GenerationTask:
        files = group.files if parallel else (group.files or expected_files(ctx))
        lines = compact_instructions(group.tasks, max_lines=ctx.policy.max_instructions_per_task)
        prompt = render(self.refine_template, **base_prompt_context(
            ctx, round_index=index, tasks=lines, targets=group.targets, files=files,
            edit_only_these=parallel, judge_summary=judge_digest(last), measurement_notes=measurement_vs_plan(last, ctx.plan, ctx.language),
            current_files=current_files(ctx, files) if ctx.single_shot else {}))
        ctx.record_prompt("refine", prompt)
        return GenerationTask(label=f"refine_{group.label}" if parallel else "refine", prompt=prompt, system=self.system_prompt(ctx),
                              files_hint=files, round=index, kind="refine", temperature=0.4, thinking="medium",
                              images=reference_images(ctx))



# ----------------------------------------------------------------------------- helpers
def refine_task_lines(tasks: Sequence[RefineTask]) -> list[str]:
    return [t.line() for t in tasks]
