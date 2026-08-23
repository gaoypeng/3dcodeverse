"""Prompt context for the tracks' jinja templates (``prompts/tracks/*.j2``).

Everything a template may reference is produced here from the spec, the plan
and the run context — exact numbers (bbox tables in meters), acceptance lines,
the language frame doc, reference-image notes — plus ``file_for_target_factory``
which maps a refine target (part / zone / asset) to the files that own it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse.contracts.chat import ImagePart
from codeverse.contracts.common import Language
from codeverse.contracts.plan import Plan, StaticPlan
from codeverse.contracts.run import RoundRecord
from codeverse.conventions import LANGUAGE_FRAME, Frame, frame_doc, to_snake
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import SINGLE_SHOT_FORMAT

log = logging.getLogger(__name__)

MAX_SKELETON_CHARS = 14_000

#: the files a scene refine task falls back to when the group has no file ownership
SCENE_FILES: tuple[str, ...] = ("src/scene.js", "src/env.js")


def parts_table(plan: Plan) -> str:
    """Markdown table of parts with exact bboxes (meters, 3 decimals)."""
    parts = getattr(plan, "parts", None)
    if not parts:
        return "(no parts)"
    rows = ["| part | role | bbox centre (x,y,z) m | extents (x,y,z) m | attach_to | inst | material |",
            "|---|---|---|---|---|---|---|"]
    for p in parts:
        c = ", ".join(f"{v:.3f}" for v in p.bbox.center)
        e = ", ".join(f"{v:.3f}" for v in p.bbox.extents)
        rows.append(f"| {p.name} | {p.role} | ({c}) | ({e}) | {p.attach_to or '-'} | {p.instances} | {p.material or '-'} |")
    return "\n".join(rows)


def part_details(plan: Plan) -> str:
    parts = getattr(plan, "parts", None) or []
    return "\n".join(f"- **{p.name}** ({p.symmetry if p.symmetry != 'none' else 'no symmetry'}): {p.description}" for p in parts)


def joints_table(plan: Plan) -> str:
    joints = getattr(plan, "joints", None)
    if not joints:
        return "(no joints)"
    rows = ["| joint | type | parent | child | axis | pivot (world, m) | lower | upper | rest | motion |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for j in joints:
        ax = ", ".join(f"{v:.3f}" for v in j.axis)
        pv = ", ".join(f"{v:.3f}" for v in j.pivot)
        rows.append(f"| {j.name} | {j.type} | {j.parent} | {j.child} | ({ax}) | ({pv}) | {j.lower:.3f} | {j.upper:.3f} | {j.rest:.3f} | {j.motion} |")
    return "\n".join(rows)


def acceptance_lines(plan: Plan | None) -> str:
    items = getattr(plan, "acceptance", None) or []
    if not items:
        return "(none)"
    return "\n".join(f"- [{a.id}] ({a.priority}, {a.how}) {a.text}" for a in items)


def bbox_line(bbox: Any) -> str:
    c = ", ".join(f"{v:.3f}" for v in bbox.center)
    e = ", ".join(f"{v:.3f}" for v in bbox.extents)
    return f"centre ({c}) m, extents ({e}) m"


def glb_to_plan_frame(v: Sequence[float], language: Language, *, extents: bool = False) -> tuple[float, float, float]:
    """Map a GLB-frame (Y-up, +Z front) vector into the language's authoring frame.
    Blender/CadQuery/URDF plans are Z-up with -Y front: glb (x, y, z) → (x, -z, y)."""
    x, y, z = float(v[0]), float(v[1]), float(v[2])
    if LANGUAGE_FRAME[language.value] is Frame.Z_UP_NEG_Y_FRONT:
        return (x, abs(z) if extents else -z, y)
    return (x, y, z)


def constraints_text(spec: Any) -> str:
    c = spec.constraints
    lines = []
    if c.dimensions_m:
        lines.append("Dimensions (m): " + ", ".join(f"{k}={v:.3f}" for k, v in c.dimensions_m.items()))
    if c.max_triangles:
        lines.append(f"Max triangles: {c.max_triangles}")
    if c.style:
        lines.append(f"Style: {c.style}")
    for m in c.must_have:
        lines.append(f"MUST HAVE: {m}")
    for m in c.must_not:
        lines.append(f"MUST NOT: {m}")
    return "\n".join(lines) or "(none)"


def base_prompt_context(ctx: RunContext, **extra: Any) -> dict[str, Any]:
    """Variables every tracks/*.j2 template may use (StrictUndefined → all present)."""
    plan = ctx.plan
    object_name = getattr(plan, "object_name", None) or getattr(plan, "title", None) or "Object"
    d: dict[str, Any] = {
        "track": ctx.track.value,
        "language": ctx.language.value,
        "frame_doc": frame_doc(LANGUAGE_FRAME[ctx.language.value]),
        "contract": ctx.contract_text,
        "cookbook_rel": ctx.cookbook_rel,
        "cookbook_excerpt": ctx.cookbook_text[:6000],
        "tool_cards": ctx.tool_cards,
        "single_shot": ctx.single_shot,
        "output_format": SINGLE_SHOT_FORMAT if ctx.single_shot else AGENT_OUTPUT_RULES,
        "spec_prompt": ctx.spec.prompt,
        "constraints": constraints_text(ctx.spec),
        "object_name": object_name,
        "plan_summary": getattr(plan, "summary", "") if plan else "",
        "style_notes": getattr(plan, "style_notes", "") if plan else "",
        "overall_bbox": bbox_line(plan.overall_bbox) if isinstance(plan, StaticPlan) else "",
        "parts_table": parts_table(plan) if plan else "",
        "part_details": part_details(plan) if plan else "",
        "joints_table": joints_table(plan) if plan else "",
        "acceptance": acceptance_lines(plan),
        "entry_files": ", ".join(getattr(ctx.runtime, "entry_globs", ()) or ()),
        "n_parts": len(getattr(plan, "parts", []) or []) if plan else 0,
        "root_link": getattr(plan, "root_link", "") if plan else "",
        "reference_note": reference_note(ctx),
    }
    d.update(extra)
    return d


def reference_images(ctx: RunContext, limit: int = 3) -> list[ImagePart]:
    """The spec's reference images as inline image parts (single-shot prompts)."""
    out: list[ImagePart] = []
    for r in list(ctx.spec.references)[:limit]:
        if Path(r.path).is_file():
            out.append(ImagePart(path=r.path, label=f"reference ({r.role}){': ' + r.note if r.note else ''}"))
    return out


def reference_note(ctx: RunContext) -> str:
    """Prompt paragraph telling the generator how to use the reference images (empty when none)."""
    refs = [r for r in ctx.spec.references if Path(r.path).is_file()]
    if not refs:
        return ""
    lines = [f"REFERENCE IMAGES ({len(refs)}): match their silhouette, proportions and visible details — they "
             "outrank the text when the two disagree.  A harness measures the front-view outline IoU against the "
             "target reference; aim for IoU ≥ 0.6."]
    for i, r in enumerate(refs, 1):
        lines.append(f"- reference {i} ({r.role}): `{r.path}`" + (f" — {r.note}" if r.note else ""))
    if ctx.single_shot:
        lines.append("The images are attached to this message.")
    else:
        tgt = next((r.path for r in refs if r.role == "target"), refs[0].path)
        lines.append(f"Use the `compare_silhouette` tool (render_png=<your front render>, reference_png=`{tgt}`) "
                     "after building to check the outline, and `render_views` to look at your model.")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- files + round digests
def expected_files(ctx: RunContext) -> list[str]:
    """Files the generator is expected to produce for this language + plan."""
    lang = ctx.language
    parts = getattr(ctx.plan, "parts", None) or []
    if lang is Language.THREEJS:
        return ["src/object.js"] + [f"src/parts/{to_snake(p.name)}.js" for p in parts]
    if lang is Language.URDF_BLENDER:
        return ["src/model.py", "src/robot.urdf"]
    files = ["src/model.py"]
    custom = getattr(ctx.runtime, "file_for_part", None)  # blender: src/parts/<snake>.py per part
    if callable(custom):
        for p in parts:
            try:
                rel = custom(p.name)
            except Exception as e:  # noqa: BLE001
                log.warning("runtime.file_for_part failed for %s: %s", p.name, e)
                continue
            if rel and str(rel) not in files:
                files.append(str(rel))
    return files


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
    gate_lines = [f"- {f.as_line(with_gate=True)}" for g in last.gates for f in g.errors][:12]
    return "\n".join(lines + gate_lines)


AGENT_OUTPUT_RULES = """HOW TO FINISH (agent mode): edit files under src/ only (and public/ for compiled assets).
Before you finish you MUST run the `build` tool and fix every error it reports; then run `measure`
(objects) or `scene_probe` (scenes) once and compare the numbers with the plan.  Do not write
reports, READMEs or notes — only the code files.  Stop when the build is clean."""


def file_for_target_factory(ctx: RunContext):
    """Return ``target → [files]`` for the current language, or ``None`` when the
    language is whole-object (one file).  Prefers ``runtime.file_for_part``."""
    rt = ctx.runtime
    plan = ctx.plan
    custom = getattr(rt, "file_for_part", None)
    part_names = {to_snake(p.name): p.name for p in (getattr(plan, "parts", None) or [])}
    lang = ctx.language

    if lang is Language.THREEJS or callable(custom):
        entry = "src/object.js" if lang is Language.THREEJS else "src/model.py"

        def _per_part(target: str) -> list[str]:
            key = to_snake(target)
            if key in part_names:
                if callable(custom):
                    try:
                        out = custom(part_names[key])
                        if out:
                            return [str(out)] if isinstance(out, (str, Path)) else [str(p) for p in out]
                    except Exception as e:  # noqa: BLE001
                        log.warning("runtime.file_for_part failed for %s: %s", target, e)
                return [f"src/parts/{key}.js"] if lang is Language.THREEJS else [entry]
            if key in ("overall", "assembly", "object", ""):
                whole = getattr(rt, "file_for_target", None)  # blender: 'overall' → src/model.py
                if callable(whole):
                    try:
                        out = whole(target)
                        if out:
                            return [str(out)] if isinstance(out, (str, Path)) else [str(p) for p in out]
                    except Exception as e:  # noqa: BLE001
                        log.warning("runtime.file_for_target failed for %s: %s", target, e)
                return [entry]
            return []
        return _per_part

    if lang is Language.SCENE_THREEJS:
        zones = {to_snake(z.name) for z in (getattr(plan, "zones", None) or [])}
        assets = {to_snake(a.name) for a in (getattr(plan, "assets", None) or [])}
        cameras = {to_snake(c.name) for c in (getattr(plan, "cameras", None) or [])}

        def _scene(target: str) -> list[str]:
            key = to_snake(target)
            if key in zones:
                return [f"src/zones/{key}.js"]
            if key in assets:
                return [f"src/assets/{key}.js"]
            if key in cameras or key in ("camera", "cameras", "composition"):
                return ["src/scene.js"]
            if key in ("env", "environment", "lighting", "sky", "fog", "ground", "water", "light"):
                return ["src/env.js"]
            return []
        return _scene
    return None
