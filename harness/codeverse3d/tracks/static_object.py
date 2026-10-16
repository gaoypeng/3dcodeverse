"""StaticObjectTrack: plan → skeleton → draft → refine rounds → finalise (D110, baseline-first).

Languages: blender (bpy) · cadquery · threejs (articulated_object subclasses it for urdf_blender).
Round 0 is ONE agent session that drafts the WHOLE object — every part file and the entry
(``BaseTrack.baseline_tasks`` over ``generate_static.j2``); every refine round is ONE session that
edits the whole object in place (``allow_refine_fanout = False``), driven by the gate ERRORs
(placeholder parts first), the failed must-acceptance items and the judge's improvement plan.
No per-part scoped baseline and no parallel part sessions: they ran serially behind the
per-workspace session lock since 2026-08-28 and left 13-14 of 15 argon runs with placeholder parts.
"""

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
from codeverse3d.tracks.depth import budget_gate
from codeverse3d.tracks.generation import GenerationTask
from codeverse3d.tracks.lifecycle import BaseTrack
from codeverse3d.tracks.prompting import (
    base_prompt_context,
    budget_for,
    judge_digest,
    judged_sheet,
    measurement_vs_plan,
    reference_images,
    refine_inline_files,
)
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

#: the 4 clay views judged for holes/intersections — their own measured cameras (D47),
#: NOT a name-filter over OBJECT_VIEWS (which silently shrank under renamed rigs)
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
        if measurement is not None and ctx.plan is not None:
            # is the object as dense as its own plan says?  Deterministic, so the judge is
            # never asked "does it look detailed enough" (tracks/depth.py).
            out.append(budget_gate(measurement, build, budget_for(ctx)))
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
        """Nothing beyond ``gates_section``.  The measured-structure block the judge reads
        (contacts, planned joins, floor gaps, the weld line) is rendered by
        ``judges.prompt_builder.gates_section`` from the connectivity report's contact
        ledger — the report is stored in ``rounds/rNN.json``, so ``3dcode judge <slug>``,
        calibration and every other reader of the round record get the same block as the
        in-run judge without this method restating it (2026-08-30)."""
        return ""


class StaticObjectTrack(BaseTrack):
    track = Track.STATIC_OBJECT
    rubric = TRACK_INFO[Track.STATIC_OBJECT].rubric
    plan_model = StaticPlan
    generate_template = "tracks/generate_static.j2"
    refine_template = "tracks/refine_object.j2"
    #: ONE whole-object refine session per round (D110): the part files interlock, and a split
    #: round ran its sessions one after another behind the workspace lock anyway
    allow_refine_fanout = False

    def make_pipeline(self) -> ObjectPipeline:
        return ObjectPipeline()

    # ------------------------------------------------------------------ refine (scaffold hooks)
    def extra_refine_tasks(self, ctx: RunContext, last: RoundRecord) -> Sequence[RefineTask]:
        # NB: the `detail_budget` WARN is deliberately NOT turned into a refine task.  Measured:
        # refine rounds that added > 2000 triangles while assembly was still open lost 0.075 of
        # assembly_fit and 0.025 of overall.
        return reference_refine_tasks(ctx, last)

    def _refine_task(
        self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool
    ) -> GenerationTask:
        """The round's ONE whole-object session: every file in scope, edited in place; each
        instruction line names the files its target owns.  ``group`` is the round's single group
        (no fan-out), its tasks already ordered gate ERRORs → acceptance → judge plan.

        A single-shot call has no read tool: it sees only the files inlined for it, so when the
        tasks name files it is fenced to them (``edit_only``, the entry still writable) — a model
        told "the whole object" once could rewrite a part file it was never shown."""
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


#: the shortest refine window opened: below it the hard run clock decides anyway
REFINE_MIN_WINDOW_S = 120.0


def object_session_window_s(ctx: RunContext) -> int:
    """A refine session's window: the agent default, clipped so that the session — which the
    watchdog lets run ``HARD_GRACE_S`` past its window while it is still working — ends
    ``RoundPolicy.judge_reserve_s`` before the run's HARD ceiling (the round's build, gates, renders and
    judge: 0.7 min measured for an object, 2026-10-15; one reserve for every track since D112 — it was
    ``OBJECT_ROUND_RESERVE_S``, 180 s, beside the refine task's own ``GenerationTask.reserve_s``).

    WHY.  With no window of its own the session ran to the run's deadline: the baseline-first
    smoke's blender refine (started at 25.8 min, 36 min allowed) was killed by the run clock at
    60.0 min after $4.63 of work, and the round was dropped unbuilt and unjudged — as were all
    three refines of iteration 0's dining chair.  Ended earlier, its edits are built and judged
    and the picker chooses between the rounds."""
    left = ctx.budget.left_s() - ctx.policy.judge_reserve_s - HARD_GRACE_S
    return int(max(REFINE_MIN_WINDOW_S, min(float(ctx.settings.limits.agent_timeout_s), left)))


# ===================================================================== reference images
SILHOUETTE_GATE = "reference_silhouette"
IOU_REFINE_THRESHOLD = 0.6


def silhouette_gate(ctx: RunContext, renders: RenderSet) -> GateReport | None:
    """The round's :func:`~codeverse3d.spatial.silhouette.measure_reference` — the measurement the
    reference judge scores — as a finding: WARN below the refine threshold when the IoU is a shape
    verdict (reliable mask, reference agrees with the brief), else INFO.  ``None`` when the spec
    has no reference or nothing could be compared."""
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
