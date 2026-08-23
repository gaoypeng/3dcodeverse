"""StaticObjectTrack: plan → skeleton → baseline → refine rounds → finalise.

Languages: blender (bpy) · cadquery · threejs.  Per-part parallel refinement
is used when the language owns one file per part (threejs) and ≥ 3
file-disjoint task groups exist; otherwise one whole-object refine task.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from codeverse.contracts.artifacts import BuildResult, GateReport, Measurement, RenderSet
from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import Plan, StaticPlan
from codeverse.contracts.run import RoundRecord
from codeverse.conventions import BBOX_TOLERANCE_M, OBJECT_VIEWS, to_snake
from codeverse.orchestrator.rounds import (
    RefineTask,
    TaskGroup,
    build_refine_instructions,
    plan_parallel_groups,
)
from codeverse.prompts import render
from codeverse.tracks.common import (
    RunContext,
    base_prompt_context,
    file_for_target_factory,
    glb_to_plan_frame,
)
from codeverse.tracks.generation import GenerationTask
from codeverse.tracks.lifecycle import BaseTrack
from codeverse.tracks.repair import format_error_report
from codeverse.tracks.steps import failed_acceptance

log = logging.getLogger(__name__)

MAX_SKELETON_CHARS = 14_000


class ObjectPipeline:
    """Measure → connectivity + contract (+ runtime extra gates) → 8-view render."""

    views = OBJECT_VIEWS

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None:
        return ctx.services.measure(Path(build.glb_path)) if build.glb_path else None

    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]:
        out: list[GateReport] = []
        glb = Path(build.glb_path) if build.glb_path else None
        if glb is not None:
            out.append(ctx.services.connectivity(glb))
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

    def plan_summary(self, ctx: RunContext) -> str:
        plan = ctx.plan
        if plan is None:
            return ""
        parts = ", ".join(f"{p.name}×{p.instances}" if p.instances > 1 else p.name for p in plan.parts)
        e = plan.overall_bbox.extents
        return f"{plan.object_name}: {plan.summary} Overall {e[0]:.2f}×{e[1]:.2f}×{e[2]:.2f} m. Parts: {parts}."

    def judge_context(self, ctx: RunContext, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        return ""


class StaticObjectTrack(BaseTrack):
    track = Track.STATIC_OBJECT
    rubric = "static_object_v1"
    plan_model = StaticPlan
    generate_template = "tracks/generate_static.j2"
    refine_template = "tracks/refine_object.j2"

    def make_pipeline(self, ctx: RunContext) -> ObjectPipeline:
        return ObjectPipeline()

    # ------------------------------------------------------------------ baseline
    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        files = expected_files(ctx)
        prompt = render(self.generate_template, **base_prompt_context(
            ctx, expected_files=files, skeleton_files=skeleton_files(ctx) if ctx.single_shot else {}, previous_error=""))
        ctx.record_prompt("generate", prompt)
        return [GenerationTask(label="baseline", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=0,
                               kind="baseline", temperature=0.5, thinking="medium")]

    def system_prompt(self, ctx: RunContext) -> str:
        return (f"You are an expert {ctx.language.value} 3D modeller writing RAW code (no SDKs, no helper libraries). "
                f"Follow the contract exactly; exact numbers beat adjectives.")

    def round_files_hint(self, ctx: RunContext) -> list[str]:
        return expected_files(ctx)

    # ------------------------------------------------------------------ refine
    def refine_tasks(self, ctx: RunContext, last: RoundRecord, history: Sequence[RoundRecord]) -> tuple[list[GenerationTask], list[str]]:
        index = len(history)
        if last.build is None or not last.build.ok:
            return [self._rebuild_task(ctx, last, index)], ["rebuild: previous round did not build"]
        fft = file_for_target_factory(ctx)
        tasks = build_refine_instructions(last.judgment, last.gates, failed_acceptance(ctx, last.judgment), ctx.plan,
                                          file_for_target=fft, max_tasks=ctx.policy.max_refine_tasks)
        if not tasks:
            return [], []
        groups = plan_parallel_groups(tasks)
        parallel = len(groups) >= ctx.policy.parallel_min_tasks and all(g.files for g in groups)
        if not parallel:
            groups = [TaskGroup(tasks=tasks, files=sorted({f for t in tasks for f in t.files}))]
        gen_tasks = [self._refine_task(ctx, g, last, index, parallel=parallel) for g in groups]
        ctx.events.emit("refine.planned", round=index, n_tasks=len(tasks), n_groups=len(groups), parallel=parallel,
                        targets=[g.targets for g in groups])
        return gen_tasks, [t.line() for t in tasks]

    def _refine_task(self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool) -> GenerationTask:
        files = group.files if parallel else (group.files or expected_files(ctx))
        prompt = render(self.refine_template, **base_prompt_context(
            ctx, round_index=index, tasks=[t.line() for t in group.tasks], targets=group.targets, files=files,
            edit_only_these=parallel, judge_summary=judge_digest(last), measurement_notes=measurement_vs_plan(last, ctx.plan, ctx.language),
            current_files=current_files(ctx, files) if ctx.single_shot else {}))
        ctx.record_prompt("refine", prompt)
        return GenerationTask(label=f"refine_{group.label}" if parallel else "refine", prompt=prompt, system=self.system_prompt(ctx),
                              files_hint=files, round=index, kind="refine", temperature=0.4, thinking="medium")

    def _rebuild_task(self, ctx: RunContext, last: RoundRecord, index: int) -> GenerationTask:
        files = expected_files(ctx)
        lint = next((g for g in last.gates if g.gate.startswith("lint")), GateReport(gate="lint", passed=True))
        report = format_error_report(last.build, lint, ctx.cookbook_text) if last.build else "build did not run"
        prompt = render(self.generate_template, **base_prompt_context(
            ctx, expected_files=files, skeleton_files=current_files(ctx, files) if ctx.single_shot else {},
            previous_error=report))
        return GenerationTask(label="rebuild", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=index,
                              kind="rebuild", temperature=0.7, thinking="high")


# ----------------------------------------------------------------------------- helpers
def expected_files(ctx: RunContext) -> list[str]:
    """Files the generator is expected to produce for this language + plan."""
    lang = ctx.language
    parts = getattr(ctx.plan, "parts", None) or []
    if lang is Language.THREEJS:
        return ["src/object.js"] + [f"src/parts/{to_snake(p.name)}.js" for p in parts]
    if lang is Language.URDF_BLENDER:
        return ["src/model.py", "src/robot.urdf"]
    if lang is Language.SCENE_THREEJS:
        return ["src/scene.js", "src/env.js"]
    return ["src/model.py"]


def skeleton_files(ctx: RunContext, max_chars: int = MAX_SKELETON_CHARS) -> dict[str, str]:
    """Current src/ files (the skeleton), trimmed, for single-shot prompts."""
    return current_files(ctx, [str(p.relative_to(ctx.ws.root)) for p in sorted(ctx.ws.src.rglob("*")) if p.is_file()], max_chars)


def current_files(ctx: RunContext, rels: Sequence[str], max_chars: int = MAX_SKELETON_CHARS) -> dict[str, str]:
    out: dict[str, str] = {}
    total = 0
    for rel in rels:
        p = ctx.ws.root / rel
        if not p.is_file():
            continue
        text = p.read_text(errors="replace")
        room = max_chars - total
        if room <= 0:
            break
        if len(text) > room:
            text = text[:room] + "\n# ... truncated ...\n"
        out[rel] = text
        total += len(text)
    return out


def judge_digest(last: RoundRecord, max_issues: int = 8) -> str:
    j = last.judgment
    if j is None:
        return "(no judgment for the previous round)"
    lines = [f"Previous score {j.overall:.2f} ({'passed' if j.passed else 'not passed'}). {j.summary}".strip()]
    for k, v in sorted(j.scores.items(), key=lambda kv: kv[1])[:6]:
        lines.append(f"- {k}: {v:.2f}")
    for i in j.issues[:max_issues]:
        lines.append(f"- [{i.severity}/{i.kind}] {i.target}: {i.detail}" + (f" (seen in {i.evidence})" if i.evidence else ""))
    return "\n".join(lines)


def measurement_vs_plan(last: RoundRecord, plan: Plan | None, language: Language = Language.THREEJS) -> str:
    """Exact numbers (in the plan's frame): measured overall/part bboxes vs planned ones."""
    m = last.measurement
    if m is None or plan is None or not hasattr(plan, "overall_bbox"):
        return ""
    pe = plan.overall_bbox.extents
    me = glb_to_plan_frame(m.extents, language, extents=True)
    lines = [f"Measured overall extents {me[0]:.3f}×{me[1]:.3f}×{me[2]:.3f} m vs plan "
             f"{pe[0]:.3f}×{pe[1]:.3f}×{pe[2]:.3f} m; ground gap {m.ground_gap_m:+.3f} m; footprint offset {m.footprint_offset_m:.3f} m; "
             f"{m.tri_count} tris, {m.n_meshes} meshes, {m.n_islands} islands."]
    planned = {to_snake(p.name): p for p in getattr(plan, "parts", [])}
    for pm in m.parts[:24]:
        p = planned.get(to_snake(pm.name))
        if p is None:
            continue
        ext = glb_to_plan_frame([b - a for a, b in zip(pm.bbox_min, pm.bbox_max, strict=True)], language, extents=True)
        cen = glb_to_plan_frame([(a + b) / 2 for a, b in zip(pm.bbox_min, pm.bbox_max, strict=True)], language)
        lines.append(f"- {p.name}: measured centre ({cen[0]:.3f}, {cen[1]:.3f}, {cen[2]:.3f}) extents ({ext[0]:.3f}, {ext[1]:.3f}, {ext[2]:.3f})"
                     f" | plan centre ({p.bbox.center[0]:.3f}, {p.bbox.center[1]:.3f}, {p.bbox.center[2]:.3f}) extents "
                     f"({p.bbox.extents[0]:.3f}, {p.bbox.extents[1]:.3f}, {p.bbox.extents[2]:.3f})")
    gate_lines = [f"- GATE {g.gate}: {f.message}" + (f" FIX: {f.fix_hint}" if f.fix_hint else "")
                  for g in last.gates for f in g.errors][:12]
    return "\n".join(lines + gate_lines)


def refine_task_lines(tasks: Sequence[RefineTask]) -> list[str]:
    return [t.line() for t in tasks]
