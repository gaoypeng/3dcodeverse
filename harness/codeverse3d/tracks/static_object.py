"""StaticObjectTrack: plan → skeleton → baseline → refine rounds → finalise.

Languages: blender (bpy) · cadquery · threejs.  Per-part parallel refinement
is used when the language owns one file per part (threejs, blender) and ≥ 2
file-disjoint task groups exist; otherwise one whole-object refine task.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    RenderSet,
    RenderView,
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
from codeverse3d.tracks.common import RunContext
from codeverse3d.tracks.depth import (
    PartScope,
    budget_gate,
    scope_groups,
    scoped_generation_enabled,
)
from codeverse3d.tracks.generation import GenerationTask
from codeverse3d.tracks.lifecycle import BaseTrack
from codeverse3d.tracks.prompting import (
    base_prompt_context,
    budget_for,
    judge_digest,
    judged_sheet,
    language_system_prompt,
    measurement_vs_plan,
    reference_images,
    refine_inline_files,
    scope_context,
    skeleton_files,
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
        """Reference-image runs: front-view outline IoU vs the target image (WARN finding with the number)."""
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
    part_template = "tracks/generate_static_part.j2"
    assemble_template = "tracks/assemble_static.j2"

    def make_pipeline(self) -> ObjectPipeline:
        return ObjectPipeline()

    # ------------------------------------------------------------------ baseline
    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        scoped = self.scoped_baseline_tasks(ctx)
        if scoped:
            return scoped
        files = ctx.runtime.expected_files(ctx.plan)
        prompt = render(
            self.generate_template,
            **base_prompt_context(
                ctx,
                expected_files=files,
                skeleton_files=skeleton_files(ctx) if ctx.single_shot else {},
                previous_error="",
            ),
        )
        ctx.record_prompt("generate", prompt)
        return [
            GenerationTask(
                label="baseline",
                prompt=prompt,
                system=self.system_prompt(ctx),
                files_hint=files,
                round=0,
                kind="baseline",
                temperature=0.5,
                thinking="medium",
                images=reference_images(ctx),
            )
        ]

    # ------------------------------------------------------------------ scoped baseline
    def scopes(self, ctx: RunContext) -> list[PartScope]:
        """Per-part generation scopes, or ``[]`` when one session should own the object.

        Requires a language that owns one file per part (blender / threejs) and an
        agent session per scope — a single-shot envelope has no session to split.
        """
        if ctx.single_shot or not scoped_generation_enabled():
            return []
        cached = ctx.extra.get("scopes")
        if isinstance(cached, list):
            return cached
        got = scope_groups(
            ctx.plan,
            files_for=self.refine_file_for_target(ctx),
            max_groups=max(2, int(getattr(ctx.settings.limits, "max_parallel_agents", 6) or 6)),
        )
        ctx.extra["scopes"] = got
        return got

    def scoped_baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        """Fan the baseline out: one session per few parts (phase 0), then ONE assembly
        session that owns the entry file and the placement gates (phase 1).

        Measured motivation (wave "generation-depth"): within a fixed difficulty tier
        more built parts buy detail (ρ(n_built, geometry_detail) = +0.34 on static_v2
        r00) but also buy gate errors (+0.45), and a single session over 10+ parts
        spends its attention on the plan, not on the parts.  Each scoped session sees
        only its own parts plus the deterministic interface boxes of the neighbours it
        must weld to; the assembly session is the only one that sees the whole object.
        """
        scopes = self.scopes(ctx)
        if not scopes:
            return []
        images = reference_images(ctx)
        tasks: list[GenerationTask] = []
        for scope in scopes:
            files = list(scope.files)
            prompt = render(
                self.part_template, **scope_context(ctx, scope, files=files, expected_files=files)
            )
            ctx.record_prompt("generate", prompt)
            tasks.append(
                GenerationTask(
                    label=f"baseline_{scope.label}",
                    prompt=prompt,
                    system=self.scope_system_prompt(ctx, scope),
                    files_hint=files,
                    round=0,
                    kind="baseline",
                    phase=0,
                    temperature=0.5,
                    thinking="medium",
                    images=images,
                    # scope.files is this session's whole world; the entry belongs to assemble
                    edit_only=True,
                )
            )
        entry = ctx.runtime.expected_files(ctx.plan)[:1]  # the assembly session's files: the entry, nothing else
        assemble = render(
            self.assemble_template, **base_prompt_context(ctx, files=entry, expected_files=entry)
        )
        ctx.record_prompt("assemble", assemble)
        tasks.append(
            GenerationTask(
                label="assemble",
                prompt=assemble,
                system=self.system_prompt(ctx),
                files_hint=entry,
                round=0,
                kind="baseline",
                phase=1,
                temperature=0.3,
                thinking="high",
                images=images,
                owns_entry=True,  # the assembly session is the entry file's one owner
            )
        )
        ctx.events.emit(
            "generate.scoped",
            round=0,
            n_scopes=len(scopes),
            n_parts=sum(len(s.parts) for s in scopes),
            scopes=[{"label": s.label, "parts": s.names, "files": list(s.files)} for s in scopes],
        )
        return tasks

    def scope_system_prompt(self, ctx: RunContext, scope: PartScope) -> str:
        return language_system_prompt(ctx.language, role="scope",
                                      n_parts=len(scope.parts), names=", ".join(scope.names))

    # ------------------------------------------------------------------ refine (scaffold hooks)
    def extra_refine_tasks(self, ctx: RunContext, last: RoundRecord) -> Sequence[RefineTask]:
        # NB: the `detail_budget` WARN is deliberately NOT turned into a refine task.  Measured:
        # refine rounds that added > 2000 triangles while assembly was still open lost 0.075 of
        # assembly_fit and 0.025 of overall.
        return reference_refine_tasks(ctx, last)

    def _refine_task(
        self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool
    ) -> GenerationTask:
        # scope is real whenever the judge's targets resolved to files — parallel or not.
        # A group whose targets did not resolve (target "overall") keeps the whole tree.
        scoped = parallel or bool(group.files)
        files = group.files if scoped else ctx.runtime.expected_files(ctx.plan)
        lines = compact_instructions(group.tasks, max_lines=ctx.policy.max_instructions_per_task)
        prompt = render(
            self.refine_template,
            **base_prompt_context(
                ctx,
                round_index=index,
                tasks=lines,
                targets=group.targets,
                files=files,
                edit_only_these=scoped,
                judge_summary=judge_digest(last),
                measurement_notes=measurement_vs_plan(last, ctx.plan, ctx.language),
                # single-shot: always; agent session: under fewer_turns, when the scoped
                # set is ≤ 3 files / ≤ 12 k chars — the first turn is then the edit
                current_files=refine_inline_files(ctx, files, scoped=scoped),
            ),
        )
        ctx.record_prompt("refine", prompt)
        return GenerationTask(
            label=f"refine_{group.label}" if parallel else "refine",
            prompt=prompt,
            system=self.system_prompt(ctx),
            files_hint=files,
            round=index,
            kind="refine",
            temperature=0.4,
            thinking="medium",
            images=reference_images(ctx) + judged_sheet(last),
            edit_only=scoped,
            owns_entry=True,  # refine_object.j2 promises entry-file access even when scoped
        )


# ===================================================================== reference images
SILHOUETTE_GATE = "reference_silhouette"
IOU_REFINE_THRESHOLD = 0.6
FRONT_VIEW_NAMES: tuple[str, ...] = ("front", "front_right_high", "front_left_high",
                                     "front_right_34", "front_left_34")  # *_34 = pre-D47 stored runs


def target_reference(ctx: RunContext) -> str | None:
    """Path of the reference image to match the silhouette against (role ``target`` first)."""
    refs = [r for r in ctx.spec.references if Path(r.path).is_file()]
    if not refs:
        return None
    return next((r.path for r in refs if r.role == "target"), refs[0].path)


def front_view(renders: RenderSet) -> RenderView | None:
    for name in FRONT_VIEW_NAMES:
        for v in renders.views:
            if v.name == name:
                return v
    return renders.views[0] if renders.views else None


def silhouette_gate(ctx: RunContext, renders: RenderSet) -> GateReport | None:
    """``None`` when the spec has no reference images or nothing could be compared."""
    ref = target_reference(ctx)
    view = front_view(renders)
    if ref is None or view is None:
        return None
    try:
        res = ctx.services.silhouette(view.path, ref)
    except Exception as e:  # noqa: BLE001 — advisory measurement; never fails a round
        log.warning("compare_silhouette failed: %s", e)
        return GateReport(gate=SILHOUETTE_GATE, passed=True, findings=[GateFinding(
            gate=SILHOUETTE_GATE, severity=Severity.INFO, target="overall", message=f"silhouette comparison unavailable: {e}")])
    iou = float(res.get("iou", 0.0)) if isinstance(res, dict) else 0.0
    reliable = bool(res.get("reliable", True)) if isinstance(res, dict) else False
    data = {k: v for k, v in (res.items() if isinstance(res, dict) else []) if isinstance(v, (int, float, str, bool))}
    data["view"] = view.name
    data["reference"] = ref
    low = reliable and iou < IOU_REFINE_THRESHOLD
    msg = (f"front-view outline IoU vs reference = {iou:.3f}" + ("" if reliable else " (unreliable mask)")
           + (f" — below {IOU_REFINE_THRESHOLD:.1f}" if low else ""))
    finding = GateFinding(gate=SILHOUETTE_GATE, severity=Severity.WARN if low else Severity.INFO, target="overall",
                          message=msg, data=data,
                          fix_hint=("match the reference outline: compare proportions (aspect ratio), overall extents and the "
                                    "silhouette of each major part against the reference image" if low else ""))
    return GateReport(gate=SILHOUETTE_GATE, passed=True, findings=[finding])


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
    if iou >= IOU_REFINE_THRESHOLD or data.get("reliable") is False:
        return []
    bits = [f"Front-view silhouette IoU vs the reference image is {iou:.2f} (target ≥ {IOU_REFINE_THRESHOLD:.1f})."]
    aspect = data.get("aspect_ratio_err")
    if isinstance(aspect, (int, float)):
        ra, rr = data.get("ref_aspect"), data.get("render_aspect")
        if isinstance(ra, (int, float)) and isinstance(rr, (int, float)):
            bits.append(f"Aspect (w/h): reference {ra:.2f} vs model {rr:.2f} — " +
                        ("make the object wider relative to its height" if rr < ra else "make the object taller relative to its width") + ".")
    bits.append("Re-read the reference: match the outline of every major part (overall proportions first, then part shapes), "
                "keeping names and the plan's dimensions unless the reference clearly disagrees.")
    return [RefineTask(target="overall", kind="reference", instruction=" ".join(bits), priority=1, source="gate")]
