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

from codeverse.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse.contracts.common import TRACK_INFO, Track
from codeverse.contracts.plan import Plan, StaticPlan
from codeverse.contracts.run import RoundRecord
from codeverse.conventions import (
    BBOX_TOLERANCE_M,
    LANGUAGE_FRAME,
    OBJECT_VIEWS,
    Frame,
    to_pascal,
    to_snake,
)
from codeverse.orchestrator import DETAIL_KIND, RefineTask, TaskGroup, compact_instructions
from codeverse.prompts import render
from codeverse.spatial.contract import planned_joins
from codeverse.tracks.common import RunContext
from codeverse.tracks.depth import (
    PartScope,
    budget_gate,
    scope_groups,
    scoped_generation_enabled,
)
from codeverse.tracks.generation import GenerationTask
from codeverse.tracks.lifecycle import BaseTrack
from codeverse.tracks.prompting import (
    base_prompt_context,
    budget_for,
    expected_files,
    file_for_target_factory,
    glb_to_plan_frame,
    judge_digest,
    judged_sheet,
    language_system_prompt,
    measurement_vs_plan,
    reference_images,
    refine_inline_files,
    scope_context,
    skeleton_files,
)
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

#: the 4 clay views judged for holes/intersections (subset of OBJECT_VIEWS by name)
GEOMETRY_VIEW_NAMES: tuple[str, ...] = ("front_right_34", "back_left_34", "top", "low_front_left")
GEOMETRY_VIEWS = tuple(v for v in OBJECT_VIEWS if v.name in GEOMETRY_VIEW_NAMES)


class ObjectPipeline:
    """Measure → connectivity + contract → 8-view render."""

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
        # a DETAIL round promised not to move anything: prove it in code, not with the judge
        if measurement is not None and ctx.extra.get("detail_round") == round_index:
            out.append(
                drift_gate(
                    ctx.extra.get("detail_baseline"),
                    measurement,
                    tol_m=ctx.policy.detail_bbox_tol_m,
                    language=ctx.language.value,
                )
            )
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

    def plan_summary(self, ctx: RunContext) -> str:
        plan = ctx.plan
        if plan is None:
            return ""
        parts = ", ".join(
            f"{p.name}×{p.instances}" if p.instances > 1 else p.name for p in plan.parts
        )
        # reorder into the measurement table's frame (W×H×D, Y-up): the (x,|z|,y) swap is
        # self-inverse for extents, so Z-up plans (W,D,H) → (W,H,D), threejs is identity —
        # otherwise the judge compares the digest position-wise against swapped numbers.
        e = glb_to_plan_frame(plan.overall_bbox.extents, ctx.language, extents=True)
        return f"{plan.object_name}: {plan.summary} Overall {e[0]:.2f}×{e[1]:.2f}×{e[2]:.2f} m (W×H×D). Parts: {parts}."

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
        ledger — the report is stored in ``rounds/rNN.json``, so ``3dcv judge <slug>``,
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
    detail_template = "tracks/detail_object.j2"
    supports_detail_round = True

    def make_pipeline(self) -> ObjectPipeline:
        return ObjectPipeline()

    # ------------------------------------------------------------------ baseline
    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        scoped = self.scoped_baseline_tasks(ctx)
        if scoped:
            return scoped
        files = expected_files(ctx)
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
            files_for=file_for_target_factory(ctx),
            max_groups=max(2, int(getattr(ctx.settings.limits, "max_parallel_agents", 6) or 6)),
        )
        ctx.extra["scopes"] = got
        return got

    def entry_files(self, ctx: RunContext) -> list[str]:
        """The assembly session's files: the entry, and nothing else."""
        files = expected_files(ctx)
        return files[:1] or ["src/model.py"]

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
        entry = self.entry_files(ctx)
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

    def round_files_hint(self, ctx: RunContext) -> list[str]:
        return expected_files(ctx)

    # ------------------------------------------------------------------ refine (scaffold hooks)
    def extra_refine_tasks(self, ctx: RunContext, last: RoundRecord) -> Sequence[RefineTask]:
        # NB: the `detail_budget` WARN is deliberately NOT turned into a refine task.  Measured:
        # refine rounds that added > 2000 triangles while assembly was still open lost 0.075 of
        # assembly_fit and 0.025 of overall.  Density is the DETAIL round's job (detail_tasks),
        # which runs only once the structure gates are clean.
        return reference_refine_tasks(ctx, last)

    def _refine_task(
        self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool
    ) -> GenerationTask:
        # scope is real whenever the judge's targets resolved to files — parallel or not.
        # A group whose targets did not resolve (target "overall") keeps the whole tree.
        scoped = parallel or bool(group.files)
        files = group.files if scoped else expected_files(ctx)
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

    # ------------------------------------------------------------------ detail round
    def detail_tasks(
        self, ctx: RunContext, last: RoundRecord, index: int
    ) -> tuple[list[GenerationTask], list[str]]:
        """One surface-detail round: bevels, seams, fasteners, wear, material variation —
        with the silhouette, the placement and the part list frozen.

        Fanned out over the same per-part scopes as the baseline when the language owns
        one file per part, because detail is embarrassingly parallel and file-disjoint.
        ``ctx.extra`` records the round index and the measurement to diff against, which
        is what ``ObjectPipeline.gates`` turns into the ``detail_drift`` gate.
        """
        lines = detail_instructions(last, max_lines=ctx.policy.max_instructions_per_task + 2)
        if not lines:
            return [], []
        ctx.extra["detail_round"] = index
        ctx.extra["detail_baseline"] = last.measurement
        scopes = self.scopes(ctx)
        tasks: list[GenerationTask] = []
        # one scoped task per scope, or one whole-object task.  The two arms differed in
        # four values — the context builder, the label, the file list, and which of
        # edit_only / owns_entry is set (detail is file-disjoint per scope, so a scoped
        # pass never touches the entry; the whole-object pass owns the full tree).
        for scope in scopes or [None]:
            files = list(scope.files) if scope is not None else expected_files(ctx)
            context = (scope_context(ctx, scope, round_index=index, tasks=lines, files=files,
                                     judge_summary=judge_digest(last),
                                     current_files=refine_inline_files(ctx, files, scoped=False))
                       if scope is not None else
                       base_prompt_context(ctx, round_index=index, tasks=lines, files=files,
                                           judge_summary=judge_digest(last),
                                           current_files=refine_inline_files(ctx, files, scoped=False)))
            tasks.append(
                GenerationTask(
                    label=f"detail_{scope.label}" if scope is not None else "detail",
                    prompt=render(self.detail_template, **context),
                    system=self.detail_system_prompt(ctx),
                    files_hint=files,
                    round=index,
                    kind=DETAIL_KIND,
                    temperature=0.6,
                    thinking="high",
                    edit_only=scope is not None,
                    owns_entry=scope is None,
                )
            )
        ctx.record_prompt("detail", tasks[0].prompt)
        ctx.events.emit(
            "detail.planned",
            round=index,
            n_tasks=len(tasks),
            n_lines=len(lines),
            tol_mm=round(ctx.policy.detail_bbox_tol_m * 1000, 1),
        )
        return tasks, lines

    def detail_system_prompt(self, ctx: RunContext) -> str:
        return language_system_prompt(ctx.language, role="detail")


# ===================================================================== the DETAIL round
# --------------------------------------------------------------------------- detail-round drift gate
DRIFT_GATE = "detail_drift"


def drift_findings(before: Measurement | None, after: Measurement | None, *, tol_m: float,
                   language: str = "") -> list[GateFinding]:
    """Did a detail round move anything?  Findings for the ``detail_drift`` gate.

    The detail round's whole contract is "surface only": the silhouette, the part
    list and every part box stay put.  This is the deterministic check of that
    promise — the judge is never asked whether the shape moved, code answers it.
    """
    out: list[GateFinding] = []
    if before is None or after is None:
        return out
    for axis, a, b in zip(_axes(language), before.extents, after.extents, strict=True):
        d = float(b) - float(a)
        if abs(d) > tol_m:
            out.append(GateFinding(
                gate=DRIFT_GATE, severity=Severity.ERROR, target="overall",
                message=f"the detail round changed the overall {axis} extent by {d * 1000:+.1f} mm "
                        f"({a:.3f} → {b:.3f} m); a detail round may not change the silhouette",
                fix_hint="revert whatever grew the object (a bevel that widened a part, a fastener sticking out) "
                         "and keep the added geometry inside the existing surfaces",
                data={"kind": "detail_drift", "axis": axis, "delta_m": round(d, 5)}))
    was = {to_snake(str(p.name)): p for p in (before.parts or ())}
    now = {to_snake(str(p.name)): p for p in (after.parts or ())}
    for key in sorted(set(was) - set(now)):
        out.append(GateFinding(
            gate=DRIFT_GATE, severity=Severity.ERROR, target=to_pascal(key),
            message=f"part '{to_pascal(key)}' disappeared during the detail round",
            fix_hint=f"restore '{to_pascal(key)}' exactly as it was before this round",
            data={"kind": "detail_drift", "removed": key}))
    for key in sorted(set(now) - set(was)):
        out.append(GateFinding(
            gate=DRIFT_GATE, severity=Severity.WARN, target=to_pascal(key),
            message=f"the detail round introduced a new top-level part '{to_pascal(key)}'",
            fix_hint="detail belongs inside an existing part; merge it into the part it decorates "
                     "(or accept it only if the plan names it)",
            data={"kind": "detail_drift", "added": key}))
    moved = 0
    for key in sorted(set(was) & set(now)):
        a, b = was[key], now[key]
        dc = max(abs((bl + bh) / 2 - (al + ah) / 2)
                 for al, ah, bl, bh in zip(a.bbox_min, a.bbox_max, b.bbox_min, b.bbox_max, strict=True))
        de = max(abs((bh - bl) - (ah - al))
                 for al, ah, bl, bh in zip(a.bbox_min, a.bbox_max, b.bbox_min, b.bbox_max, strict=True))
        if max(dc, de) <= tol_m:
            continue
        moved += 1
        if moved > 8:
            continue
        out.append(GateFinding(
            gate=DRIFT_GATE, severity=Severity.ERROR, target=to_pascal(key),
            message=f"part '{to_pascal(key)}' moved {dc * 1000:.1f} mm / resized {de * 1000:.1f} mm during the "
                    f"detail round (tolerance {tol_m * 1000:.0f} mm)",
            fix_hint="put the part back on its previous centre and extents; add the detail inside that box",
            data={"kind": "detail_drift", "part": key, "centre_mm": round(dc * 1000, 2),
                  "extent_mm": round(de * 1000, 2)}))
    if moved > 8:
        out.append(GateFinding(gate=DRIFT_GATE, severity=Severity.ERROR, target="overall",
                               message=f"{moved} parts moved during the detail round ({moved - 8} more not listed)",
                               fix_hint="revert the placement changes; this round may only add surface geometry",
                               data={"kind": "detail_drift", "moved": moved}))
    if not out:
        d_tri = int(getattr(after, "tri_count", 0) or 0) - int(getattr(before, "tri_count", 0) or 0)
        out.append(GateFinding(gate=DRIFT_GATE, severity=Severity.INFO, target="overall",
                               message=f"detail round held the contract: no part moved more than "
                                       f"{tol_m * 1000:.0f} mm; {d_tri:+,} triangles added",
                               data={"kind": "detail_drift", "delta_tris": d_tri}))
    return out


def _axes(language: str) -> tuple[str, str, str]:
    """Axis letters as the AUTHOR sees them.  ``Measurement.extents`` is in the GLB frame
    (Y-up); a blender/cadquery/urdf author thinks Z-up, where glb (x, y, z) reads (x, z, y)
    — so naming the GLB axis in a fix hint would send them to the wrong dimension."""
    if LANGUAGE_FRAME.get(language) is Frame.Z_UP_NEG_Y_FRONT:
        return ("x", "z", "y")
    return ("x", "y", "z")


def drift_gate(before: Measurement | None, after: Measurement | None, *, tol_m: float, language: str = "") -> GateReport:
    """``detail_drift`` GateReport (passing when nothing moved)."""
    findings = drift_findings(before, after, tol_m=tol_m, language=language)
    return GateReport(gate=DRIFT_GATE, findings=findings,
                      passed=not any(f.severity is Severity.ERROR for f in findings))


# --------------------------------------------------------------------------- detail-round tasks
#: judge improvement-plan kinds a DETAIL round is allowed to act on.  Assembly/placement
#: work is the repair loop's job and would break the no-drift contract.
DETAIL_KINDS = frozenset({"geometry", "detail", "material", "materials", "craftsmanship", "texture", "finish"})

#: what a detail round always does, whatever the judge said.  Ordered by measured value per
#: triangle: edge treatment first (it changes how every surface reads under light), then the
#: features a viewer counts, then wear.
DEFAULT_DETAIL_LINES: tuple[str, ...] = (
    "Bevel or chamfer every hard edge that a real version of this object would have "
    "(2-6 mm on furniture and cast parts, 0.5-2 mm on sheet metal and small mechanisms).",
    "Add the panel lines, seams and shut-lines where the real object's shells meet "
    "(lids, drawers, housings, trays): 1-2 mm wide, ~1 mm deep insets.",
    "Add the fasteners the real object is held together with — bolt heads, rivets, screws, "
    "hinges — arrayed in a loop at the joints, sized 3-10 mm.",
    "Give each material family a distinct roughness/metalness and a slightly different value; "
    "no two different materials may share the same flat grey.",
)


def detail_instructions(last: Any, *, max_lines: int = 8) -> list[str]:
    """Instruction lines for a detail round: the measured density gap first, then the
    judge's detail-shaped asks, then the standing detail vocabulary; deduped and capped."""
    lines: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        key = text.strip().lower()[:80]
        if key and key not in seen:
            seen.add(key)
            lines.append(text.strip())

    for g in getattr(last, "gates", None) or ():
        for f in getattr(g, "findings", None) or ():
            if (f.data or {}).get("kind") == "under_tri_budget":
                _add(f"{f.message}. {f.fix_hint}")
    j = getattr(last, "judgment", None)
    for item in sorted(getattr(j, "improvement_plan", None) or (),
                       key=lambda i: (i.priority, -getattr(i, "expected_gain", 0.0))):
        if str(getattr(item, "kind", "")).lower() not in DETAIL_KINDS:
            continue
        target = getattr(item, "target", "") or "overall"
        _add(f"{target}: {item.instruction}")
    for issue in getattr(j, "issues", None) or ():
        if str(getattr(issue, "kind", "")).lower() in ("material", "materials", "detail"):
            _add(f"{issue.target or 'overall'}: {issue.detail}")
    for text in DEFAULT_DETAIL_LINES:
        _add(text)
    return lines[:max_lines]


# ===================================================================== reference images
SILHOUETTE_GATE = "reference_silhouette"
IOU_REFINE_THRESHOLD = 0.6
FRONT_VIEW_NAMES: tuple[str, ...] = ("front", "front_right_34", "front_left_34")


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
