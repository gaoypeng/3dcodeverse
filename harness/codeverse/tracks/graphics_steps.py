"""Graphics-track helpers: planner hooks (GraphicsPlan), prompt context, frame RenderSet.

The shared planner assumes a 3D frame in its acceptance fixups
(``ensure_acceptance`` adds a ground-contact item); shaders have none, so the
graphics track supplies its own template / example / acceptance hooks to the
ONE planner loop in ``tracks/planner.py`` and reuses everything else from
``tracks/lifecycle.py`` + ``tracks/steps.py`` unchanged.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from codeverse.contracts.artifacts import BuildResult, RenderSet, RenderView, Severity
from codeverse.contracts.common import Language
from codeverse.contracts.plan import AcceptanceItem, GraphicsPlan
from codeverse.contracts.spec import Spec
from codeverse.languages._gl_common import SHEET_NAME, read_metrics
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import SINGLE_SHOT_FORMAT
from codeverse.tracks.graphics_recipes import EXTRA_KEY as SEEDED_KEY
from codeverse.tracks.graphics_recipes import graphics_brief
from codeverse.tracks.planner import add_acceptance_item, build_system_prompt
from codeverse.tracks.planner import plan as run_planner
from codeverse.tracks.prompting import (
    AGENT_OUTPUT_RULES,
    acceptance_lines,
    constraints_text,
    reference_note,
    select_cookbook_excerpt,
)
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

PLAN_TEMPLATE = "tracks/plan_graphics.j2"
PLAN_TEMPERATURE = 0.5
PLAN_MAX_OUTPUT_TOKENS = 16000
EXPECTED_FILES: dict[Language, list[str]] = {
    Language.GLSL_SHADER: ["src/shader.frag", "src/common.glsl"],
    Language.OPENGL_PYTHON: ["src/program.py"],
}


# ----------------------------------------------------------------------------- planner
def plan_example() -> dict[str, Any]:
    return {
        "title": "Neon rain on a window", "summary": "Looking through a rain-streaked window at a neon-lit street at night; "
        "drops run down the glass, city lights turn into coloured bokeh discs that pulse.",
        "style": "cyberpunk night: deep indigo/black base, magenta + cyan neon accents, warm sodium highlights; soft, filmic",
        "resolution": [1280, 720], "duration_s": 8.0,
        "passes": [
            {"name": "CityBokeh", "kind": "fullscreen", "description": "background: 40-60 blurred bokeh discs (hash-placed, 3 depth layers, cyan/magenta/amber), slow horizontal parallax, pulsing brightness"},
            {"name": "RainDrops", "kind": "fullscreen", "description": "grid-cell drops with hash offsets, running trails (fract(t) per cell), refraction offset applied when sampling the background"},
            {"name": "Grade", "kind": "postprocess", "description": "vignette, slight chromatic aberration, tonemap + gamma"},
        ],
        "uniforms": ["u_time", "u_resolution"],
        "motion": "drops slide down with gravity and wobble; bokeh drifts left 0.02/s and pulses at 0.5-1 Hz; no hard cuts",
        "key_visuals": ["rain drops with trails on glass", "blurred neon bokeh discs", "dark night street behind", "magenta/cyan palette"],
        "acceptance": [
            {"id": "a1", "text": "Raindrops with trails visibly run down the glass (compare t=0 and t=1)", "how": "visual", "priority": "must"},
            {"id": "a2", "text": "Blurred coloured bokeh lights are visible in the background", "how": "visual", "priority": "must"},
            {"id": "a3", "text": "Frames change over time (no static image)", "how": "probe", "priority": "must"},
        ],
    }


def build_plan_system_prompt(spec: Spec, *, runtime: Any | None = None) -> str:
    """The graphics plan system prompt = the shared builder with the graphics
    template + worked example (``plan_graphics.j2`` references no 3D frame)."""
    return build_system_prompt(spec, GraphicsPlan, runtime=runtime, template=PLAN_TEMPLATE, example=plan_example())


def ensure_graphics_acceptance(plan: GraphicsPlan, spec: Spec) -> GraphicsPlan:
    """Spec must_have / must_not → visual items; planned motion → a probe item (never 'ground contact')."""
    items: list[AcceptanceItem] = list(plan.acceptance)
    for m in spec.constraints.must_have:
        add_acceptance_item(items, "must", f"Includes: {m}", "visual")
    for m in spec.constraints.must_not:
        add_acceptance_item(items, "not", f"Does NOT include: {m}", "visual")
    if plan.motion.strip() and not any("static" in a.text.lower() or "motion" in a.text.lower() or "change over time" in a.text.lower() for a in items):
        add_acceptance_item(items, "motion", "Frames change over time as planned (not a static image)", "probe")
    plan.acceptance = items
    return plan


def graphics_event_stats(plan: GraphicsPlan) -> dict[str, Any]:
    """``plan.done`` payload for graphics (passes, not parts/zones)."""
    return {"n_passes": len(plan.passes), "n_acceptance": len(plan.acceptance)}


def plan_graphics(spec: Spec, model_id: str, ws: Workspace, *, model: Any | None = None, events: Any | None = None,
                  budget: Any | None = None, runtime: Any | None = None) -> GraphicsPlan:
    """Structured planner call → validated GraphicsPlan: the ONE planner loop
    (``tracks/planner.plan``) parameterised with the graphics hooks."""
    return run_planner(spec, model_id, GraphicsPlan, ws, model=model, events=events, budget=budget, runtime=runtime,
                       template=PLAN_TEMPLATE, example=plan_example(), temperature=PLAN_TEMPERATURE,
                       max_output_tokens=PLAN_MAX_OUTPUT_TOKENS,
                       finalise=lambda p: ensure_graphics_acceptance(p, spec), event_stats=graphics_event_stats)


# ----------------------------------------------------------------------------- prompt context
def graphics_expected_files(ctx: RunContext) -> list[str]:
    return list(EXPECTED_FILES.get(ctx.language, ["src/shader.frag"]))


def passes_table(plan: GraphicsPlan | None) -> str:
    if plan is None or not plan.passes:
        return "(no passes)"
    rows = ["| pass | kind | what it draws / computes |", "|---|---|---|"]
    rows += [f"| {p.name} | {p.kind} | {p.description} |" for p in plan.passes]
    return "\n".join(rows)


def graphics_prompt_context(ctx: RunContext, **extra: Any) -> dict[str, Any]:
    """Every variable the graphics templates may reference (StrictUndefined)."""
    plan = ctx.plan if isinstance(ctx.plan, GraphicsPlan) else None
    res = plan.resolution if plan else (1280, 720)
    d: dict[str, Any] = {
        "track": ctx.track.value, "language": ctx.language.value, "contract": ctx.contract_text,
        "cookbook_rel": ctx.cookbook_rel, "cookbook_excerpt": select_cookbook_excerpt(ctx, graphics_brief(ctx)), "tool_cards": ctx.tool_cards,
        # the recipes tracks/graphics_recipes.py put in the harness-owned src/recipes.glsl before the session ([] = no block)
        "seeded_recipes": list((getattr(ctx, "extra", None) or {}).get(SEEDED_KEY) or []),
        "single_shot": ctx.single_shot, "output_format": SINGLE_SHOT_FORMAT if ctx.single_shot else AGENT_OUTPUT_RULES,
        "spec_prompt": ctx.spec.prompt, "constraints": constraints_text(ctx.spec),
        "title": plan.title if plan else "Untitled effect", "plan_summary": plan.summary if plan else "",
        "style": plan.style if plan else "", "resolution": f"{res[0]}x{res[1]}", "duration": f"{plan.duration_s:g}" if plan else "8",
        "passes_table": passes_table(plan), "motion": plan.motion if plan else "", "key_visuals": list(plan.key_visuals) if plan else [],
        "uniforms": ", ".join(plan.uniforms) if plan and plan.uniforms else "u_time, u_resolution",
        "acceptance": acceptance_lines(plan), "entry_files": ", ".join(getattr(ctx.runtime, "entry_globs", ()) or ()),
        "expected_files": graphics_expected_files(ctx), "reference_note": reference_note(ctx),
        "judge_times": "0, 1, 2.5, 4, 6 s",
    }
    d.update(extra)
    return d


# ----------------------------------------------------------------------------- renders / gates
def frames_render_set(ws: Workspace, build: BuildResult, round_index: int) -> RenderSet:
    """Copy the judged frames + sheet into renders/rNN and describe them as a RenderSet (views ``t=<s>s``)."""
    out = ws.renders_dir(round_index)
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(build.extra_paths.get("frames", ws.artifacts / "frames"))
    metrics = read_metrics(ws)
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


def frame_stats_text(ws: Workspace) -> str:
    m = read_metrics(ws)
    if m is None:
        return "(no frame metrics)"
    stats, gate = m
    lines = stats.summary_lines()
    for f in gate.findings:
        lines.append(f"- GATE gl_frames {f.as_line(with_severity=True, with_hint=f.severity is not Severity.INFO)}")
    return "\n".join(lines)
