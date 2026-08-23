"""Graphics-track helpers: planner (GraphicsPlan), prompt context, frame RenderSet.

The shared planner / prompt helpers assume a 3D frame (``LANGUAGE_FRAME``,
``ensure_acceptance`` adds a ground-contact item); shaders have neither, so the
graphics track owns these three pieces and reuses everything else from
``tracks/lifecycle.py`` + ``tracks/steps.py`` unchanged.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from codeverse.contracts.artifacts import BuildResult, RenderSet, RenderView
from codeverse.contracts.chat import ChatMessage, ChatRequest
from codeverse.contracts.common import Language, Usage
from codeverse.contracts.plan import AcceptanceItem, GraphicsPlan
from codeverse.contracts.spec import Spec
from codeverse.languages.glsl_shader.gl_build import SHEET_NAME, read_metrics
from codeverse.prompts import prompt_hash, render
from codeverse.tracks.common import RunContext, language_contract
from codeverse.tracks.generation import SINGLE_SHOT_FORMAT
from codeverse.tracks.planner import PlanningError, _parse_json
from codeverse.tracks.prompting import (
    AGENT_OUTPUT_RULES,
    acceptance_lines,
    constraints_text,
    reference_note,
)
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

PLAN_TEMPLATE = "tracks/plan_graphics.j2"
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
    return render(PLAN_TEMPLATE, track=spec.track.value, language=spec.language.value,
                  contract=language_contract(spec.language, runtime)[:6000],
                  example_json=json.dumps(plan_example(), indent=1),
                  schema_fields=", ".join(GraphicsPlan.model_json_schema().get("properties", {}).keys()))


def build_plan_user_prompt(spec: Spec) -> str:
    return "\n".join([f"REQUEST: {spec.prompt}", "", "CONSTRAINTS:", constraints_text(spec), "",
                      "Return the plan as JSON matching the schema."])


def ensure_graphics_acceptance(plan: GraphicsPlan, spec: Spec) -> GraphicsPlan:
    """Spec must_have / must_not → visual items; planned motion → a probe item (never 'ground contact')."""
    items: list[AcceptanceItem] = list(plan.acceptance)
    have = {a.text.strip().lower() for a in items}
    ids = {a.id for a in items}

    def _add(prefix: str, text: str, how: str) -> None:
        if text.strip().lower() in have:
            return
        n = 1
        while f"{prefix}{n}" in ids:
            n += 1
        ids.add(f"{prefix}{n}")
        items.append(AcceptanceItem(id=f"{prefix}{n}", text=text, how=how, priority="must"))  # type: ignore[arg-type]

    for m in spec.constraints.must_have:
        _add("must", f"Includes: {m}", "visual")
    for m in spec.constraints.must_not:
        _add("not", f"Does NOT include: {m}", "visual")
    if plan.motion.strip() and not any("static" in a.text.lower() or "motion" in a.text.lower() or "change over time" in a.text.lower() for a in items):
        _add("motion", "Frames change over time as planned (not a static image)", "probe")
    plan.acceptance = items
    return plan


def plan_graphics(spec: Spec, model_id: str, ws: Workspace, *, model: Any | None = None, events: Any | None = None,
                  budget: Any | None = None, runtime: Any | None = None) -> GraphicsPlan:
    """Structured planner call → validated GraphicsPlan (one re-ask with the validation errors)."""
    if model is None:
        from codeverse.models import get_chat_model

        model = get_chat_model(model_id)
    system = build_plan_system_prompt(spec, runtime=runtime)
    messages = [ChatMessage.user(build_plan_user_prompt(spec))]
    schema = GraphicsPlan.model_json_schema()
    usage = Usage()
    last_error = ""
    for attempt in range(2):
        req = ChatRequest(messages=messages, system=system, response_schema=schema, temperature=0.5, thinking="medium",
                          max_output_tokens=16000, label=f"planner{'-retry' if attempt else ''}")
        resp = model.generate(req)
        usage = usage + resp.usage
        raw = resp.parsed if resp.parsed is not None else _parse_json(resp.text)
        try:
            if not isinstance(raw, dict):
                raise ValueError(f"planner returned {type(raw).__name__}, expected a JSON object")
            plan = GraphicsPlan.model_validate(raw)
        except (ValidationError, ValueError) as e:
            last_error = str(e)[:4000]
            if events is not None:
                events.emit("plan.invalid", attempt=attempt, error=last_error[:500])
            messages = messages + [ChatMessage.assistant(json.dumps(raw)[:20000] if raw is not None else (resp.text or "")[:20000]),
                                   ChatMessage.user("Your plan failed validation. Fix EXACTLY these problems and return the full corrected "
                                                    f"plan JSON again (same schema):\n{last_error}")]
            continue
        plan = ensure_graphics_acceptance(plan, spec)
        ws.write_json(ws.plan_path, plan)
        if budget is not None:
            budget.charge(usage)
        if events is not None:
            events.emit("plan.done", model=model_id, attempt=attempt, n_passes=len(plan.passes), n_acceptance=len(plan.acceptance),
                        cost_usd=round(usage.cost_usd, 4), prompt_hash=prompt_hash(system))
        return plan
    if budget is not None:
        budget.charge(usage)
    raise PlanningError(f"graphics plan did not validate after re-ask: {last_error}")


# ----------------------------------------------------------------------------- prompt context
def expected_files(ctx: RunContext) -> list[str]:
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
        "cookbook_rel": ctx.cookbook_rel, "cookbook_excerpt": ctx.cookbook_text[:7000], "tool_cards": ctx.tool_cards,
        "single_shot": ctx.single_shot, "output_format": SINGLE_SHOT_FORMAT if ctx.single_shot else AGENT_OUTPUT_RULES,
        "spec_prompt": ctx.spec.prompt, "constraints": constraints_text(ctx.spec),
        "title": plan.title if plan else "Untitled effect", "plan_summary": plan.summary if plan else "",
        "style": plan.style if plan else "", "resolution": f"{res[0]}x{res[1]}", "duration": f"{plan.duration_s:g}" if plan else "8",
        "passes_table": passes_table(plan), "motion": plan.motion if plan else "", "key_visuals": list(plan.key_visuals) if plan else [],
        "uniforms": ", ".join(plan.uniforms) if plan and plan.uniforms else "u_time, u_resolution",
        "acceptance": acceptance_lines(plan), "entry_files": ", ".join(getattr(ctx.runtime, "entry_globs", ()) or ()),
        "expected_files": expected_files(ctx), "reference_note": reference_note(ctx),
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
        lines.append(f"- GATE gl_frames [{f.severity.value}] {f.message}" + (f" FIX: {f.fix_hint}" if f.fix_hint and f.severity.value != "info" else ""))
    return "\n".join(lines)


def refine_lines(tasks: Sequence[Any]) -> list[str]:
    return [t.line() for t in tasks]
