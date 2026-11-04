"""StaticObjectTrack."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from codeverse3d.agents.cli_common import HARD_GRACE_S
from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    RenderSet,
    Severity,
)
from codeverse3d.contracts.common import TRACK_INFO, Track
from codeverse3d.contracts.plan import Plan, StaticPlan
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.conventions import (
    BBOX_TOLERANCE_M,
    OBJECT_CLAY_VIEWS,
    OBJECT_VIEWS,
)
from codeverse3d.orchestrator import RefineTask, TaskGroup, compact_instructions
from codeverse3d.prompts import render
from codeverse3d.spatial.contract import planned_joins
from codeverse3d.spatial.silhouette import measure_reference
from codeverse3d.tracks.common import RunContext
from codeverse3d.tracks.depth import budget_gate, depth_budget
from codeverse3d.tracks.generation import GenerationTask
from codeverse3d.tracks.lifecycle import BaseTrack
from codeverse3d.tracks.prompting import (
    base_prompt_context,
    judge_digest,
    judged_sheet,
    measurement_vs_plan,
    reference_images,
    refine_inline_files,
)
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

#: the 4 clay views judged for holes/intersections; their own cameras, not a name filter over OBJECT_VIEWS
GEOMETRY_VIEWS = OBJECT_CLAY_VIEWS


class ObjectPipeline:
    """Measure → connectivity + contract → 14-view render."""

    views = OBJECT_VIEWS

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None:
        return ctx.services.measure(Path(build.glb_path)) if build.glb_path else None

    def gates(
        self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None
    ) -> list[GateReport]:
        out: list[GateReport] = []
        glb = Path(build.glb_path) if build.glb_path else None
        if glb is not None:
            out.append(  # fix hints in the author's frame; planned joins measured into the ledger
                ctx.services.connectivity(glb, ctx.language.value, planned_edges=planned_joins(ctx.plan, measurement))
            )
        if measurement is not None and ctx.plan is not None:
            out.append(
                ctx.services.contract(measurement, ctx.plan, BBOX_TOLERANCE_M, ctx.language.value)
            )
            # deterministic density check against the plan; the judge is never asked about detail
            out.append(budget_gate(measurement, build, depth_budget(ctx.plan, ctx.settings.limits.build_timeout_s)))
        return out

    def render(
        self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None
    ) -> RenderSet:
        r = ctx.settings.render
        return ctx.services.render_object(
            Path(build.glb_path),
            ctx.ws.renders_dir(round_index),
            views=self.views,
            width=r.width,
            height=r.height,
        )

    def post_render_gates(
        self, ctx: RunContext, round_index: int, renders: RenderSet
    ) -> list[GateReport]:
        """Reference-image runs: outline IoU vs the target image (WARN finding with the number)."""
        gate = silhouette_gate(ctx, renders)
        return [gate] if gate is not None else []

    def geometry_views(
        self, ctx: RunContext, round_index: int, build: BuildResult
    ) -> RenderSet | None:
        """4 clay views for the judge's geometry montage (holes, intersections)."""
        if not build.glb_path:
            return None
        out_dir = ctx.ws.renders_dir(round_index) / "clay"
        try:
            return ctx.services.render_geometry(Path(build.glb_path), out_dir, views=GEOMETRY_VIEWS)
        except Exception as e:  # noqa: BLE001 — optional judge context, never round-fatal
            log.warning("clay geometry render failed: %s", e)
            ctx.events.emit(
                "render.geometry_failed", round=round_index, error=f"{type(e).__name__}: {e}"
            )
            return None

    def judge_context(
        self,
        ws: Workspace,
        plan: Plan | None,
        round_index: int,
        build: BuildResult,
        gates: list[GateReport],
    ) -> str:
        """Nothing beyond ``gates_section``."""
        return ""


class StaticObjectTrack(BaseTrack):
    track = Track.STATIC_OBJECT
    rubric = TRACK_INFO[Track.STATIC_OBJECT].rubric
    plan_model = StaticPlan
    generate_template = "tracks/generate_static.j2"
    refine_template = "tracks/refine_object.j2"
    #: the part files interlock: one whole-object refine session per round
    allow_refine_fanout = False

    def make_pipeline(self) -> ObjectPipeline:
        return ObjectPipeline()

    # ------------------------------------------------------------------ refine (scaffold hooks)
    def extra_refine_tasks(self, ctx: RunContext, last: RoundRecord) -> Sequence[RefineTask]:
        # the `detail_budget` WARN is deliberately NOT a refine task (adding triangles while assembly is open lowers the score)
        return reference_refine_tasks(ctx, last)

    def _refine_task(
        self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool
    ) -> GenerationTask:
        """The round's ONE whole-object session."""
        fenced = ctx.single_shot and bool(group.files)
        files = list(group.files) if fenced else ctx.runtime.expected_files(ctx.plan)
        lines = compact_instructions(group.tasks, max_lines=ctx.policy.max_instructions_per_task)
        prompt = render(
            self.refine_template,
            **base_prompt_context(
                ctx,
                round_index=index,
                tasks=lines,
                targets=group.targets,
                files=files,
                edit_only_these=fenced,
                judge_summary=judge_digest(last),
                measurement_notes=measurement_vs_plan(last, ctx.plan, ctx.language),
                # single-shot only: the files the tasks name (all of them for an "overall" task)
                current_files=refine_inline_files(ctx, files),
            ),
        )
        ctx.record_prompt("refine", prompt)
        return GenerationTask(
            label="refine",
            prompt=prompt,
            system=self.system_prompt(ctx),
            files_hint=files,
            round=index,
            kind="refine",
            temperature=0.4,
            images=reference_images(ctx) + judged_sheet(last),
            edit_only=fenced,
            owns_entry=True,
            timeout_s=None if ctx.single_shot else object_session_window_s(ctx),
        )


REFINE_MIN_WINDOW_S = 120.0


def object_session_window_s(ctx: RunContext) -> int:
    """A refine session's window."""
    left = ctx.budget.left_s() - ctx.policy.judge_reserve_s - HARD_GRACE_S
    return int(max(REFINE_MIN_WINDOW_S, min(float(ctx.settings.limits.agent_timeout_s), left)))


# ===================================================================== reference images
SILHOUETTE_GATE = "reference_silhouette"
IOU_REFINE_THRESHOLD = 0.6


def silhouette_gate(ctx: RunContext, renders: RenderSet) -> GateReport | None:
    try:
        m = measure_reference(renders.views, ctx.spec, compare=ctx.services.silhouette)
    except Exception as e:  # noqa: BLE001 — advisory measurement; never fails a round
        log.warning("compare_silhouette failed: %s", e)
        return GateReport.of(SILHOUETTE_GATE, [GateFinding(
            gate=SILHOUETTE_GATE, severity=Severity.INFO, target="overall", message=f"silhouette comparison unavailable: {e}")])
    if m is None:
        return None
    low = m.scorable and m.iou < IOU_REFINE_THRESHOLD
    msg = (f"outline IoU vs reference = {m.iou:.3f} (best-matching view: {m.view})"
           + ("" if m.reliable else " (unreliable mask)")
           + (" (the reference's proportions contradict the brief: not a shape verdict)" if m.conflict else "")
           + (f" — below {IOU_REFINE_THRESHOLD:.1f}" if low else ""))
    finding = GateFinding(gate=SILHOUETTE_GATE, severity=Severity.WARN if low else Severity.INFO, target="overall",
                          message=msg, data=m.model_dump(exclude={"per_view"}),
                          fix_hint=("match the reference outline: compare proportions (aspect ratio), overall extents and the "
                                    "silhouette of each major part against the reference image" if low else ""))
    return GateReport.of(SILHOUETTE_GATE, [finding])


def silhouette_iou(rec: RoundRecord) -> tuple[float, dict] | None:
    """(iou, data) recorded by ``silhouette_gate`` in a round, if any."""
    for g in rec.gates:
        if g.gate != SILHOUETTE_GATE:
            continue
        for f in g.findings:
            if "iou" in f.data:
                try:
                    return float(f.data["iou"]), dict(f.data)
                except (TypeError, ValueError):
                    return None
    return None


def reference_refine_tasks(ctx: RunContext, last: RoundRecord) -> list[RefineTask]:
    """A priority-1 refine task when the last round's silhouette IoU was below threshold."""
    if not ctx.spec.references:
        return []
    hit = silhouette_iou(last)
    if hit is None:
        return []
    iou, data = hit
    if iou >= IOU_REFINE_THRESHOLD or data.get("reliable") is False or data.get("conflict"):
        return []
    seen = f" (best-matching view: {data['view']})" if data.get("view") else ""
    bits = [f"Silhouette IoU vs the reference image is {iou:.2f}{seen} (target ≥ {IOU_REFINE_THRESHOLD:.1f})."]
    aspect = data.get("aspect_ratio_err")
    if isinstance(aspect, (int, float)):
        ra, rr = data.get("ref_aspect"), data.get("render_aspect")
        if isinstance(ra, (int, float)) and isinstance(rr, (int, float)):
            bits.append(f"Aspect (w/h): reference {ra:.2f} vs model {rr:.2f} — " +
                        ("make the object wider relative to its height" if rr < ra else "make the object taller relative to its width") + ".")
    bits.append("Re-read the reference: match the outline of every major part (overall proportions first, then part shapes), "
                "keeping names and the plan's dimensions unless the reference clearly disagrees.")
    return [RefineTask(target="overall", kind="reference", instruction=" ".join(bits), priority=1, source="gate")]
