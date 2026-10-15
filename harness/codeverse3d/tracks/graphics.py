"""GraphicsTrack: plan (GraphicsPlan) → skeleton → baseline → refine rounds → finalise.

Languages: glsl_shader (Shadertoy-style fragment shader) · opengl_python (raw
moderngl program).  Reuses ``BaseTrack`` (lifecycle) and ``run_round`` (steps)
unchanged; the track-specific pieces are the prompt context (no 3D frame) and the
render step (the sampled frames + contact sheet as the RenderSet
the ``shader_v2`` judge sees); the ``gl_frames`` gate is the build's own
(``BuildResult.gates``).  Planning is ``tracks/planner.py``'s, like every track's
(template, worked example, T = 0.5, acceptance: all dispatched on the track there).
Refinement is always one whole-program task.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateReport,
    Measurement,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.common import TRACK_INFO, Track
from codeverse3d.contracts.plan import GraphicsPlan, Plan
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.languages._gl_common import JUDGE_TIMES, METRICS_NAME, SHEET_NAME, read_metrics
from codeverse3d.orchestrator import TaskGroup
from codeverse3d.prompts import render
from codeverse3d.record.deliverable import legacy_canonical_round
from codeverse3d.tracks.common import RunContext
from codeverse3d.tracks.generation import GenerationTask
from codeverse3d.tracks.lifecycle import MATERIALIZE, SKELETON, BaseTrack
from codeverse3d.tracks.prompting import (
    base_prompt_context,
    judge_digest,
    judged_sheet,
    reference_images,
    refine_inline_files,
)
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


# ===================================================================== prompt context + frames
def passes_table(plan: GraphicsPlan | None) -> str:
    if plan is None or not plan.passes:
        return "(no passes)"
    rows = ["| pass | kind | what it draws / computes |", "|---|---|---|"]
    rows += [f"| {p.name} | {p.kind} | {p.description} |" for p in plan.passes]
    return "\n".join(rows)


def graphics_prompt_context(ctx: RunContext, **extra: Any) -> dict[str, Any]:
    """Every variable the graphics templates may reference (StrictUndefined): the base
    context (``prompting.base_prompt_context``) plus the graphics plan's own fields."""
    plan = ctx.plan if isinstance(ctx.plan, GraphicsPlan) else None
    res = plan.resolution if plan else (1280, 720)
    return base_prompt_context(ctx, **{
        "title": plan.title if plan else "Untitled effect",
        "style": plan.style if plan else "", "resolution": f"{res[0]}x{res[1]}", "duration": f"{plan.duration_s:g}" if plan else "8",
        "passes_table": passes_table(plan), "motion": plan.motion if plan else "", "key_visuals": list(plan.key_visuals) if plan else [],
        "uniforms": ", ".join(plan.uniforms) if plan and plan.uniforms else "u_time, u_resolution",
        "expected_files": ctx.runtime.expected_files(ctx.plan), "judge_times": ", ".join(f"{t:g}" for t in JUDGE_TIMES) + " s", **extra})


# ----------------------------------------------------------------------------- renders / gates
def frames_render_set(ws: Workspace, build: BuildResult, round_index: int) -> RenderSet:
    """Copy the judged frames + sheet into renders/rNN and describe them as a RenderSet (views ``t=<s>s``)."""
    out = ws.renders_dir(round_index)
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(build.extra_paths.get("frames", ws.artifacts / "frames"))
    metrics = read_metrics(ws)
    if metrics is not None:  # the round's own copy: what a replay of THIS round quotes (frame_stats_text)
        shutil.copy2(ws.artifacts / METRICS_NAME, out / METRICS_NAME)
    stats = metrics[0] if metrics else None
    views: list[RenderView] = []
    frames = stats.frames if stats else []
    for f in frames:
        src = Path(f.path)
        if not src.is_file():
            continue
        dst = out / f"frame_t{f.time:05.2f}.png"
        shutil.copy2(src, dst)
        views.append(RenderView(name=f"t={f.time:g}s", path=str(dst), time_s=f.time))
    if not views and frames_dir.is_dir():  # metrics missing: fall back to the raw frame files
        for p in sorted(frames_dir.glob("f*_t*.png")):
            dst = out / p.name
            shutil.copy2(p, dst)
            views.append(RenderView(name=p.stem.split("_t", 1)[-1] + "s", path=str(dst)))
    sheet_src = Path(build.extra_paths.get("sheet", ws.artifacts / SHEET_NAME))
    sheet = None
    if sheet_src.is_file():
        sheet = out / "sheet.png"
        shutil.copy2(sheet_src, sheet)
    renderer = str(build.census.get("renderer", "")) if isinstance(build.census, dict) else ""
    return RenderSet(views=views, contact_sheet=str(sheet) if sheet else None, renderer=renderer or "moderngl",
                     duration_ms=build.duration_ms)


def frame_stats_text(ws: Workspace, round_index: int | None = None) -> str:
    """The frame metrics as judge / refine text: round ``round_index``'s own copy
    (``renders/rNN/metrics.json``), else the canonical file, quoted only for the round it belongs
    to — the round an old run's finalise rebuilt (``legacy_canonical_round``: its best), else
    the LAST build's (no later round in the journal)."""
    where = ws.renders_dir(round_index) if round_index is not None else None
    if round_index is not None and where is not None and not (where / METRICS_NAME).is_file():
        owner = legacy_canonical_round(ws)
        later = any(int(p.stem[1:]) > round_index for p in (ws.root / "rounds").glob("r[0-9][0-9].json"))
        if (owner != round_index) if owner is not None else later:
            return "(not kept for this round: the metrics on disk are a later build's; judge motion from the frames)"
        where = None
    m = read_metrics(ws, where)
    if m is None:
        return "(no frame metrics)"
    stats, gate = m
    lines = stats.summary_lines()
    for f in gate.findings:
        lines.append(f"- GATE gl_frames {f.as_line(with_severity=True, with_hint=f.severity is not Severity.INFO)}")
    return "\n".join(lines)


class GraphicsPipeline:
    """No measurement; no gates of its own — the build's ``gl_frames`` report is the round's
    gate (``BuildResult.gates``, appended by ``steps``); render = the frames."""

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None:
        return None

    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]:
        return []

    def render(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> RenderSet:
        return frames_render_set(ctx.ws, build, round_index)

    def judge_context(self, ws: Workspace, plan: Plan | None, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        renderer = build.census.get("renderer", "") if isinstance(build.census, dict) else ""
        return f"FRAME METRICS (harness-measured, renderer {renderer or 'moderngl'}):\n{frame_stats_text(ws, round_index)}"


class GraphicsTrack(BaseTrack):
    track = Track.GRAPHICS
    rubric = TRACK_INFO[Track.GRAPHICS].rubric
    plan_model = GraphicsPlan
    generate_template = "tracks/generate_graphics.j2"
    refine_template = "tracks/refine_graphics.j2"
    # refinement is always ONE whole-program task
    allow_refine_fanout = False
    baseline_temperature = 0.6
    stages = (SKELETON, MATERIALIZE)

    def make_pipeline(self) -> GraphicsPipeline:
        return GraphicsPipeline()

    def generate_context(self, ctx: RunContext, **extra: Any) -> dict[str, Any]:
        return graphics_prompt_context(ctx, **extra)

    # ------------------------------------------------------------------ refine (scaffold hook; never fans out)
    def _refine_task(self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool) -> GenerationTask:
        files = ctx.runtime.expected_files(ctx.plan)
        prompt = render(self.refine_template, **graphics_prompt_context(
            ctx, round_index=index, tasks=[t.line() for t in group.tasks], targets=group.targets, files=files,
            judge_summary=judge_digest(last), frame_notes=frame_stats_text(ctx.ws),
            current_files=refine_inline_files(ctx, files)))
        ctx.record_prompt("refine", prompt)
        return GenerationTask(label="refine", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=index,
                              kind="refine", temperature=0.5, owns_entry=True,
                              images=reference_images(ctx) + judged_sheet(last))
