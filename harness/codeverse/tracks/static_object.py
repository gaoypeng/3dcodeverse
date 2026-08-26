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
from codeverse.orchestrator.rounds import DETAIL_KIND, RefineTask, TaskGroup, compact_instructions
from codeverse.prompts import render
from codeverse.tracks.common import RunContext
from codeverse.tracks.depth import (
    PartScope,
    budget_gate,
    scope_groups,
    scoped_generation_enabled,
)
from codeverse.tracks.detailing import detail_instructions, drift_gate
from codeverse.tracks.generation import GenerationTask
from codeverse.tracks.lifecycle import BaseTrack
from codeverse.tracks.prompting import (
    base_prompt_context,
    budget_for,
    current_files,
    expected_files,
    file_for_target_factory,
    glb_to_plan_frame,
    judge_digest,
    judged_sheet,
    measurement_vs_plan,
    reference_images,
    refine_inline_files,
    scope_context,
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

    def gates(
        self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None
    ) -> list[GateReport]:
        out: list[GateReport] = []
        glb = Path(build.glb_path) if build.glb_path else None
        if glb is not None:
            out.append(
                ctx.services.connectivity(glb, ctx.language.value)
            )  # fix hints in the author's frame
        if measurement is not None and ctx.plan is not None:
            out.append(
                ctx.services.contract(measurement, ctx.plan, BBOX_TOLERANCE_M, ctx.language.value)
            )
        extra = getattr(ctx.runtime, "extra_gates", None)
        if callable(extra):
            out.extend(extra(ctx.ws, build))
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
        return (
            f"You are an expert {ctx.language.value} 3D modeller writing RAW code (no SDKs, no helper libraries). "
            f"You own {len(scope.parts)} part(s) of a larger object: {', '.join(scope.names)}. "
            f"Other sessions own the rest — never write a file outside your list. "
            f"Follow the contract exactly; exact numbers beat adjectives; real detail beats a correctly sized box."
        )

    def system_prompt(self, ctx: RunContext) -> str:
        return (
            f"You are an expert {ctx.language.value} 3D modeller writing RAW code (no SDKs, no helper libraries). "
            f"Follow the contract exactly; exact numbers beat adjectives."
        )

    def round_files_hint(self, ctx: RunContext) -> list[str]:
        return expected_files(ctx)

    # ------------------------------------------------------------------ refine (scaffold hooks)
    def refine_file_for_target(self, ctx: RunContext) -> Any:
        return file_for_target_factory(ctx)

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
        lines = detail_instructions(
            last, ctx.plan, max_lines=ctx.policy.max_instructions_per_task + 2
        )
        if not lines:
            return [], []
        ctx.extra["detail_round"] = index
        ctx.extra["detail_baseline"] = last.measurement
        scopes = self.scopes(ctx)
        tasks: list[GenerationTask] = []
        if scopes:
            for scope in scopes:
                files = list(scope.files)
                prompt = render(
                    self.detail_template,
                    **scope_context(
                        ctx,
                        scope,
                        round_index=index,
                        tasks=lines,
                        files=files,
                        judge_summary=judge_digest(last),
                        current_files=current_files(ctx, files) if ctx.single_shot else {},
                    ),
                )
                tasks.append(
                    GenerationTask(
                        label=f"detail_{scope.label}",
                        prompt=prompt,
                        system=self.detail_system_prompt(ctx),
                        files_hint=files,
                        round=index,
                        kind=DETAIL_KIND,
                        temperature=0.6,
                        thinking="high",
                    )
                )
        else:
            files = expected_files(ctx)
            prompt = render(
                self.detail_template,
                **base_prompt_context(
                    ctx,
                    round_index=index,
                    tasks=lines,
                    files=files,
                    judge_summary=judge_digest(last),
                    current_files=current_files(ctx, files) if ctx.single_shot else {},
                ),
            )
            tasks.append(
                GenerationTask(
                    label="detail",
                    prompt=prompt,
                    system=self.detail_system_prompt(ctx),
                    files_hint=files,
                    round=index,
                    kind=DETAIL_KIND,
                    temperature=0.6,
                    thinking="high",
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
        return (
            f"You are an expert {ctx.language.value} 3D modeller adding SURFACE DETAIL to a model whose structure "
            f"is already accepted. You may not move, resize, rename, add or remove a part — a deterministic gate "
            f"compares every part's bounding box against the previous round and fails the round if one moved. "
            f"Everything you add lives inside or on an existing part's surface."
        )
