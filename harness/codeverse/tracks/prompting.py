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
from typing import TYPE_CHECKING, Any

from codeverse.config import fewer_turns_enabled
from codeverse.contracts.chat import ImagePart
from codeverse.contracts.common import HARNESS_OWNED_SRC, Language, Track
from codeverse.contracts.plan import Plan, StaticPlan
from codeverse.contracts.run import RoundRecord
from codeverse.conventions import LANGUAGE_FRAME, Frame, frame_doc, to_snake
from codeverse.prompts import render
from codeverse.prompts.catalog import prompt_dir_for
from codeverse.tracks.common import RunContext
from codeverse.tracks.depth import DepthBudget, PartScope, depth_budget, interfaces_text
from codeverse.tracks.generation import SINGLE_SHOT_FORMAT

if TYPE_CHECKING:
    from codeverse.prompts.sections import Section

log = logging.getLogger(__name__)

MAX_SKELETON_CHARS = 14_000

#: the files a scene refine task falls back to when the group has no file ownership
SCENE_FILES: tuple[str, ...] = ("src/scene.js", "src/env.js")


def parts_table(plan: Plan) -> str:
    """Markdown table of parts with exact bboxes (meters, 3 decimals)."""
    return parts_table_for(getattr(plan, "parts", None) or ())


def parts_table_for(parts: Sequence[Any]) -> str:
    """``parts_table`` for an explicit part subset (one scoped session's parts)."""
    if not parts:
        return "(no parts)"
    rows = [
        "| part | role | bbox centre (x,y,z) m | extents (x,y,z) m | attach_to | inst | material |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in parts:
        c = ", ".join(f"{v:.3f}" for v in p.bbox.center)
        e = ", ".join(f"{v:.3f}" for v in p.bbox.extents)
        rows.append(
            f"| {p.name} | {p.role} | ({c}) | ({e}) | {p.attach_to or '-'} | {p.instances} | {p.material or '-'} |"
        )
    return "\n".join(rows)


def part_details(plan: Plan) -> str:
    return part_details_for(getattr(plan, "parts", None) or [])


def part_details_for(parts: Sequence[Any]) -> str:
    """Per-part construction notes, including the sub-parts an ASSEMBLY part is made of.

    Sub-parts (``PartPlan.children``) are a planning device — the parent is still ONE
    named export node — but they are where the depth lives, so the builder must see
    them with their own boxes, not just a sentence."""
    out: list[str] = []
    for p in parts:
        sym = p.symmetry if getattr(p, "symmetry", "none") != "none" else "no symmetry"
        line = f"- **{p.name}** ({sym}): {p.description}"
        hint = getattr(p, "detail_hint", "") or ""
        if hint:
            line += f"  _{hint}_"
        out.append(line)
        for c in getattr(p, "children", None) or ():
            cc = ", ".join(f"{v:.3f}" for v in c.bbox.center)
            ce = ", ".join(f"{v:.3f}" for v in c.bbox.extents)
            inst = f" ×{c.instances}" if getattr(c, "instances", 1) > 1 else ""
            mat = f" [{c.material}]" if getattr(c, "material", "") else ""
            out.append(
                f"    - sub-part **{c.name}**{inst}{mat} — centre ({cc}) extents ({ce}) m: {c.description}"
            )
    return "\n".join(out)


def joints_table(plan: Plan) -> str:
    joints = getattr(plan, "joints", None)
    if not joints:
        return "(no joints)"
    rows = [
        "| joint | type | parent | child | axis | pivot (world, m) | lower | upper | rest | motion |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for j in joints:
        ax = ", ".join(f"{v:.3f}" for v in j.axis)
        pv = ", ".join(f"{v:.3f}" for v in j.pivot)
        rows.append(
            f"| {j.name} | {j.type} | {j.parent} | {j.child} | ({ax}) | ({pv}) | {j.lower:.3f} | {j.upper:.3f} | {j.rest:.3f} | {j.motion} |"
        )
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


def glb_to_plan_frame(
    v: Sequence[float], language: Language, *, extents: bool = False
) -> tuple[float, float, float]:
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
        lines.append(
            "Dimensions (m): " + ", ".join(f"{k}={v:.3f}" for k, v in c.dimensions_m.items())
        )
    if c.max_triangles:
        lines.append(f"Max triangles: {c.max_triangles}")
    if c.style:
        lines.append(f"Style: {c.style}")
    for m in c.must_have:
        lines.append(f"MUST HAVE: {m}")
    for m in c.must_not:
        lines.append(f"MUST NOT: {m}")
    return "\n".join(lines) or "(none)"


def cookbook_sections(ctx: RunContext, names: Sequence[str], *, max_chars: int = 24_000) -> str:
    """The named cookbook chapters, inlined verbatim, for a prompt to carry.

    Measured on the scenes_v1 battery: every zone and env session was told to call
    ``read_cookbook(section=...)`` for its chapters and NOT ONE of the 20 sessions did —
    flash writes the file immediately.  A chapter the model never reads teaches nothing,
    so the chapters that decide the score (ground, horizon, vegetation, rocks, dressing,
    layering, motion) travel inside the prompt instead; the tool stays for everything else.
    """
    from codeverse.prompts.sections import find_section, split_sections

    md = ctx.cookbook_text or ""
    if not md.strip():
        return ""
    sections = split_sections(md)
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        sec = find_section(sections, name)
        if sec is None or sec.title in seen:
            continue
        seen.add(sec.title)
        out.append(sec.body.rstrip())
    text = "\n\n".join(out)
    if len(text) > max_chars:
        text = (
            text[:max_chars].rstrip() + "\n\n…[clipped — the whole cookbook is at .3dcv/cookbook.md]"
        )
    return text


#: brief words → cookbook chapter titles (case-insensitive substrings of a ``## `` heading) they call for
COOKBOOK_SYNONYMS: dict[str, tuple[str, ...]] = {
    "aurora": ("Light phenomena",), "curtain": ("Light phenomena",), "curtains": ("Light phenomena",),
    "northern": ("Light phenomena",), "glow": ("Light phenomena",),
    "star": ("Gradient sky", "Light phenomena"), "stars": ("Gradient sky", "Light phenomena"),
    "night": ("Gradient sky", "Light phenomena"), "space": ("Gradient sky", "Light phenomena"),
    "galaxy": ("Gradient sky", "Light phenomena"), "nebula": ("Gradient sky", "Light phenomena"),
    "bokeh": ("Bokeh",), "city": ("Bokeh",), "neon": ("Bokeh",), "lights": ("Bokeh",),
    "rain": ("Rain",), "drops": ("Rain",), "glass": ("Rain",),
    "cloud": ("Domain warping",), "clouds": ("Domain warping",), "smoke": ("Domain warping",),
    "marble": ("Domain warping",),
    "tunnel": ("Raymarching",), "temple": ("Raymarching",), "corridor": ("Raymarching",), "3d": ("Raymarching",),
    "trail": ("Feedback",), "trails": ("Feedback",), "feedback": ("Feedback",),
}
COOKBOOK_ALWAYS: tuple[str, ...] = ("Hash / noise / fbm", "Palettes, tonemapping, grading", "PITFALLS")
_STOP = frozenset(("the", "and", "with", "for", "from", "into", "over", "that", "this", "are", "its", "one", "two",
                   "not", "but", "then", "than", "out", "each", "all", "any", "per", "via", "use", "like", "look",
                   "looks", "very", "some", "more", "most"))


def _words(text: str) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if (len(w) >= 3 or w in COOKBOOK_SYNONYMS) and w not in _STOP}


def is_always_chapter(title: str, always: Sequence[str] = COOKBOOK_ALWAYS) -> bool:
    """Is ``title`` one of the chapters every graphics prompt carries (helpers / grading / pitfalls)?"""
    low = title.lower()
    return any(name.lower() in low for name in always)


def select_cookbook_chapters(ctx: RunContext, brief: str, *, budget: int = 9000,
                             always: Sequence[str] = COOKBOOK_ALWAYS) -> list[Section]:
    """The cookbook chapters (cookbook order) a brief calls for.

    Measured 2026-08-26: the graphics prompt carried ``cookbook_text[:7000]`` of an 11,298-char
    cookbook, so everything after the raymarching template (sky / stars, rain, bokeh, feedback,
    PITFALLS) never reached the agent unless it called ``read_cookbook`` — and flash draws
    what it was handed (an aurora as a comb of bars, twenty sparkles for a star field).
    Here the header + ``always`` chapters go in first (4.4 k chars), then chapters ranked by
    keyword overlap between the brief (+ plan key visuals) and the chapter heading / body, with
    ``COOKBOOK_SYNONYMS`` as the strong signal, until ``budget`` is spent.  A chapter is added
    whole or not at all; the output keeps cookbook order.  The default budget is the measured
    need of a night-sky brief: always-set 4.4 k + Light phenomena 3.2 k + Gradient sky 1.3 k.
    ``tracks/graphics.py:seed_recipes`` seeds the SAME selection's code into the harness-owned ``src/recipes.glsl``.
    """
    from codeverse.prompts.sections import Section, find_section, split_sections

    md = ctx.cookbook_text or ""
    if not md.strip():
        return []
    chapters: list[Section] = []
    for s in split_sections(md):        # fold ### sub-headings into their ## chapter
        if s.level >= 3 and chapters:
            chapters[-1] = Section(chapters[-1].level, chapters[-1].title, chapters[-1].body + s.body)
        else:
            chapters.append(s)
    chosen: set[int] = {i for i, s in enumerate(chapters) if s.level < 2}    # title / conventions header
    for name in always:
        sec = find_section(chapters, name)
        if sec is not None:
            chosen.add(chapters.index(sec))
    brief_words = _words(brief)
    wanted = {t.lower() for w in brief_words for t in COOKBOOK_SYNONYMS.get(w, ())}
    scored: list[tuple[int, int]] = []
    for i, s in enumerate(chapters):
        if i in chosen:
            continue
        title = s.title.lower()
        score = 4 * sum(1 for t in wanted if t in title)
        score += 3 * len(brief_words & _words(s.title)) + len(brief_words & _words(s.body))
        if score > 0:
            scored.append((-score, i))
    used = sum(len(chapters[i].body.rstrip()) + 2 for i in chosen)
    for _, i in sorted(scored):
        size = len(chapters[i].body.rstrip()) + 2
        if used + size <= budget:
            chosen.add(i)
            used += size
    return [chapters[i] for i in sorted(chosen)]


def language_system_prompt(language: Language, *, role: str = "", tools: bool = True, **vars: Any) -> str:
    """The generator's system prompt: ``prompts/<dir>/system.md``, or a role template.

    These were f-strings inside each track class until 2026-08-28 — two sentences each,
    and for the three static-object languages literally the SAME two sentences with the
    language's name substituted, though bpy mesh modelling, CadQuery's B-rep workplanes
    and three.js BufferGeometry share almost nothing but the word "3D".  Per language, in
    the prompt corpus, so the seven can diverge and be edited without touching code.
    """
    d = prompt_dir_for(language)
    # rendered, not read raw: a system prompt that tells a SINGLE-SHOT session to call
    # gl_probe is instructing something it has no tools to do, and the self-check loop is
    # the whole point of the graphics prompt.  `tools` lets the file say so itself.
    # (The v0 two-sentence arm of the system-prompt A/B was retired 2026-08-29: a
    # three-way null, docs/EVAL.md.)
    base = render(f"{d}/system.md", tools=tools).strip()
    if not role:
        return base
    # roles COMPOSE with the language base rather than replacing it.  Replacing was the
    # shape inherited from the f-strings, and it left every fatal language-specific fact —
    # the self-check loop, the sampled-time contract — absent from exactly the stages that
    # violate it: the repair pass and the detail pass.
    return base + "\n\n" + render(f"system/role_{role}.j2", language=language.value, **vars).strip()


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
        # the whole cookbook.  It used to be ctx.cookbook_text[:6000] — a blind byte
        # prefix that delivered 13 % of blender's and 10 % of scene_threejs's, cutting
        # mid-snippet, and the read_cookbook tool that was supposed to fetch the rest
        # went uncalled in all 20 measured sessions.  Prompt material the harness wrote
        # for the model is not summarised by byte offset.
        "cookbook_excerpt": ctx.cookbook_text,
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
        "detail_budget": detail_budget_text(ctx),
        "root_link": getattr(plan, "root_link", "") if plan else "",
        "reference_note": reference_note(ctx),
        # a string, "" when off: templates render it with one {% if %} and stay byte-identical
        # for the control arm
        "turn_discipline": TURN_DISCIPLINE if (fewer_turns_enabled() and not ctx.single_shot) else "",
    }
    d.update(extra)
    return d


# ----------------------------------------------------------------------------- depth / scoping
def budget_for(ctx: RunContext) -> DepthBudget:
    """This run's complexity-aware triangle + build-time budget (cached on ctx.extra)."""
    got = ctx.extra.get("depth_budget")
    if isinstance(got, DepthBudget):
        return got
    timeout = int(getattr(getattr(ctx.settings, "limits", None), "build_timeout_s", 300) or 300)
    b = depth_budget(ctx.plan, build_timeout_s=timeout)
    ctx.extra["depth_budget"] = b
    return b


def detail_budget_text(ctx: RunContext) -> str:
    """The limits block every generate/refine/detail prompt shows, sized from the plan."""
    if ctx.plan is None or not (getattr(ctx.plan, "parts", None) or ()):
        return ""
    return budget_for(ctx).as_prompt()


def scope_context(ctx: RunContext, scope: PartScope, **extra: Any) -> dict[str, Any]:
    """Template context for ONE scoped part session: only its parts, plus the exact
    numbers of the neighbours it must weld to but may not write."""
    d = base_prompt_context(ctx, **extra)
    d.update(
        {
            "scope_label": scope.label,
            "scope_names": ", ".join(scope.names),
            "parts_table": parts_table_for(scope.parts),
            "part_details": part_details_for(scope.parts),
            "interfaces": interfaces_text(ctx.plan, scope),
            "n_scope_parts": len(scope.parts),
        }
    )
    d.update(extra)
    return d


def reference_images(ctx: RunContext, limit: int = 3) -> list[ImagePart]:
    """The spec's reference images as inline image parts (single-shot prompts)."""
    out: list[ImagePart] = []
    for r in list(ctx.spec.references)[:limit]:
        if Path(r.path).is_file():
            out.append(
                ImagePart(
                    path=r.path, label=f"reference ({r.role}){': ' + r.note if r.note else ''}"
                )
            )
    return out


def judged_sheet(last: Any) -> list[ImagePart]:
    """The contact sheet the judge scored last round, as ONE inline image for the refine session.

    "What the judge saw" reached the refine agent as a paragraph of text; the picture it
    was written about did not.  A session told "the horn intersects the case" then had to
    re-render to find out which view showed it.  One image is cheap next to a session of
    30 tool turns, and it is the same file the score came from, so the agent and the judge
    are finally looking at the same thing.
    """
    sheet = getattr(getattr(last, "renders", None), "contact_sheet", None)
    if not sheet or not Path(sheet).is_file():
        return []
    return [
        ImagePart(
            path=str(sheet),
            label=f"the contact sheet the judge scored (round {getattr(last, 'index', '?')})",
        )
    ]


def _likeness_note(ctx: RunContext, refs: list[Any]) -> str:
    """Graphics / scene: the photos say what the REAL thing looks like, not what to compose.

    The object-track note asks for a silhouette match and an IoU tool; a shader has no
    silhouette.  What an aurora / a harbour / a nebula needs from a photo is its physics —
    dominant colour, how the structure folds and thins, where the light sits, how much of
    the frame stays dark — and the judge sees the same photos beside the frames.
    """
    lines = [
        f"REFERENCE PHOTOS ({len(refs)}) of the REAL thing come with this task.  They are not a composition to copy; "
        "they show what the brief's subject actually looks like: its dominant colour and where the secondary "
        "colours sit, how its structure folds / layers / thins out, where the brightness concentrates and how "
        "much of the frame stays dark, its texture at fine scale.  Match THAT — it outranks the brief's "
        "adjectives when the two disagree, and the judge scores your frames beside the same photos.  A row of "
        "evenly spaced bars is not a curtain; a flat band is not a glow; cartoon saturation is not a night sky.  "
        "Take the physics from the photo, not the postcard: foreground, framing and landscape stay as the brief says."
    ]
    for i, r in enumerate(refs, 1):
        lines.append(f"- reference {i}: `{r.path}`" + (f" — {r.note}" if r.note else ""))
    lines.append("The photos are attached to this message." if ctx.single_shot else
                 "Their paths are listed under 'Images for this task' at the end of this message: open them with "
                 "your image/file-reading tool, and look again before every `gl_frames` / `scene_views` comparison.")
    return "\n".join(lines)


def reference_note(ctx: RunContext) -> str:
    """Prompt paragraph telling the generator how to use the reference images (empty when none)."""
    refs = [r for r in ctx.spec.references if Path(r.path).is_file()]
    if not refs:
        return ""
    if ctx.track in (Track.GRAPHICS, Track.SCENE):
        return _likeness_note(ctx, refs)
    lines = [
        f"REFERENCE IMAGES ({len(refs)}): match their silhouette, proportions and visible details — they "
        "outrank the text when the two disagree.  A harness measures the front-view outline IoU against the "
        "target reference; aim for IoU ≥ 0.6."
    ]
    for i, r in enumerate(refs, 1):
        lines.append(f"- reference {i} ({r.role}): `{r.path}`" + (f" — {r.note}" if r.note else ""))
    if ctx.single_shot:
        lines.append("The images are attached to this message.")
    else:
        tgt = next((r.path for r in refs if r.role == "target"), refs[0].path)
        lines.append(
            f"Use the `compare_silhouette` tool (render_png=<your front render>, reference_png=`{tgt}`) "
            "after building to check the outline, and `render_views` to look at your model."
        )
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
    return current_files(
        ctx,
        [str(p.relative_to(ctx.ws.root)) for p in sorted(ctx.ws.src.rglob("*")) if p.is_file()],
        max_chars,
    )


def refine_inline_files(ctx: RunContext, rels: Sequence[str], *, scoped: bool) -> dict[str, str]:
    """The files a refine task may edit, inlined for the prompt — or ``{}``.

    Single-shot always gets them (it has no read tool).  An agent session gets them only
    under ``fewer_turns`` and only when the task is scoped to ≤ ``INLINE_MAX_FILES`` files
    totalling ≤ ``INLINE_MAX_CHARS`` — measured: a refine session spends 5.2 of its 24
    turns on read_file, mostly on the files the task just named.  Larger sets are NOT
    truncated into the prompt (a half file is worse than a read): they stay on disk.
    """
    if ctx.single_shot:
        return current_files(ctx, rels)
    if not (fewer_turns_enabled() and scoped) or not rels or len(rels) > INLINE_MAX_FILES:
        return {}
    paths = [ctx.ws.root / r for r in rels]
    if not all(p.is_file() for p in paths):
        return {}
    if sum(p.stat().st_size for p in paths) > INLINE_MAX_CHARS:
        return {}
    files = current_files(ctx, rels, max_chars=INLINE_MAX_CHARS + 1)
    return files if sum(len(t) for t in files.values()) <= INLINE_MAX_CHARS else {}


def current_files(
    ctx: RunContext, rels: Sequence[str], max_chars: int = MAX_SKELETON_CHARS
) -> dict[str, str]:
    """``{rel: text}`` for the files that exist, trimmed to ``max_chars`` in total.  The
    language's harness-owned files (``src/recipes.glsl``) are never inlined: the
    single-shot prompt showed one as an editable skeleton file while every write to it
    is refused (``generate_graphics.j2`` pastes its signatures separately)."""
    owned = set(HARNESS_OWNED_SRC.get(ctx.language, ()))
    out: dict[str, str] = {}
    total = 0
    for rel in rels:
        p = ctx.ws.root / rel
        if not p.is_file() or rel in owned:
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
    lines = [
        f"Previous score {j.overall:.2f} ({'passed' if j.passed else 'not passed'}). {j.summary}".strip()
    ]
    for k, v in sorted(j.scores.items(), key=lambda kv: kv[1])[:6]:
        lines.append(f"- {k}: {v:.2f}")
    for i in j.issues[:max_issues]:
        lines.append(
            f"- [{i.severity}/{i.kind}] {i.target}: {i.detail}"
            + (f" (seen in {i.evidence})" if i.evidence else "")
        )
    return "\n".join(lines)


def measurement_vs_plan(
    last: RoundRecord, plan: Plan | None, language: Language = Language.THREEJS
) -> str:
    """Exact numbers (in the plan's frame): measured overall/part bboxes vs planned ones."""
    m = last.measurement
    if m is None or plan is None or not hasattr(plan, "overall_bbox"):
        return ""
    pe = plan.overall_bbox.extents
    me = glb_to_plan_frame(m.extents, language, extents=True)
    lines = [
        f"Measured overall extents {me[0]:.3f}×{me[1]:.3f}×{me[2]:.3f} m vs plan "
        f"{pe[0]:.3f}×{pe[1]:.3f}×{pe[2]:.3f} m; ground gap {m.ground_gap_m:+.3f} m; footprint offset {m.footprint_offset_m:.3f} m; "
        f"{m.tri_count} tris, {m.n_meshes} meshes, {m.n_islands} islands."
    ]
    planned = {to_snake(p.name): p for p in getattr(plan, "parts", [])}
    for pm in m.parts[:24]:
        p = planned.get(to_snake(pm.name))
        if p is None:
            continue
        ext = glb_to_plan_frame(
            [b - a for a, b in zip(pm.bbox_min, pm.bbox_max, strict=True)], language, extents=True
        )
        cen = glb_to_plan_frame(
            [(a + b) / 2 for a, b in zip(pm.bbox_min, pm.bbox_max, strict=True)], language
        )
        lines.append(
            f"- {p.name}: measured centre ({cen[0]:.3f}, {cen[1]:.3f}, {cen[2]:.3f}) extents ({ext[0]:.3f}, {ext[1]:.3f}, {ext[2]:.3f})"
            f" | plan centre ({p.bbox.center[0]:.3f}, {p.bbox.center[1]:.3f}, {p.bbox.center[2]:.3f}) extents "
            f"({p.bbox.extents[0]:.3f}, {p.bbox.extents[1]:.3f}, {p.bbox.extents[2]:.3f})"
        )
    gate_lines = [f"- {f.as_line(with_gate=True)}" for g in last.gates for f in g.errors][:12]
    return "\n".join(lines + gate_lines)


#: fewer_turns (docs/COST.md §29): the baseline session hit the 60-turn cap in every measured
#: run — 22.5 write_file + 8 build + 7.5 read_file + 4.6 check_connectivity + 4.1 check_contract
#: per session, each a 4 s round trip re-sending a 35–70 k context.  The cap itself is NOT the
#: lever (§17: a 28-turn cap cost 0.205 of a score point); the block asks for fewer, fuller turns.
TURN_DISCIPLINE = """## Turn discipline (every tool call is a full round trip — spend as few as you can)
- FIRST reply: write EVERY file listed under "Files you must produce" as multiple `write_file`
  calls in that same reply, then call `build` once.  Do not write one file per turn.
- `build` already runs check_connectivity and check_contract: read its CONNECTIVITY / CONTRACT
  sections, fix what they list, build again.  Do not call those two tools separately.
- Do not read a file back after writing or editing it: the write result reports its line count and
  syntax verdict.
- Call `render_sheet` once before you finish; `measure` at most once."""

#: the refine prompt inlines the files a scoped task edits when they are few and small, so the
#: session's first turn is the edit, not a read_file (fewer_turns, docs/COST.md §29)
INLINE_MAX_FILES = 3
INLINE_MAX_CHARS = 12_000

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
                            return (
                                [str(out)]
                                if isinstance(out, (str, Path))
                                else [str(p) for p in out]
                            )
                    except Exception as e:  # noqa: BLE001
                        log.warning("runtime.file_for_part failed for %s: %s", target, e)
                return [f"src/parts/{key}.js"] if lang is Language.THREEJS else [entry]
            if key in ("overall", "assembly", "object", ""):
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
            # "Zone/Asset" — the scene_placement gate names the asset but the fix lives in the
            # zone's file; keeping the asset in the target keeps one refine task per asset
            if "/" in target:
                zone_key = to_snake(target.split("/", 1)[0])
                if zone_key in zones:
                    return [f"src/zones/{zone_key}.js"]
            return []

        return _scene
    return None
