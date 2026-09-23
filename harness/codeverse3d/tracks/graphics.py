"""GraphicsTrack: plan (GraphicsPlan) → skeleton → baseline → refine rounds → finalise.

Languages: glsl_shader (Shadertoy-style fragment shader) · opengl_python (raw
moderngl program).  Reuses ``BaseTrack`` (lifecycle) and ``run_round`` (steps)
unchanged; the track-specific pieces are the recipe seeding, the prompt context
(no 3D frame) and the render step (the sampled frames + contact sheet as the RenderSet
the ``shader_v2`` judge sees); the ``gl_frames`` gate is the build's own
(``BuildResult.gates``).  Planning is ``tracks/planner.py``'s, like every track's
(template, worked example, T = 0.5, acceptance: all dispatched on the track there).
Refinement is always one whole-program task.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateReport,
    Measurement,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.common import HARNESS_OWNED_SRC, TRACK_INFO, Language, Track
from codeverse3d.contracts.plan import GraphicsPlan, Plan
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.languages._gl_common import METRICS_NAME, SHEET_NAME, read_metrics
from codeverse3d.languages.glsl_shader import COMMON_GLSL, FUNC_DEF, strip_comments
from codeverse3d.orchestrator import TaskGroup
from codeverse3d.prompts import render
from codeverse3d.prompts.sections import Section, split_sections
from codeverse3d.tracks.common import RunContext
from codeverse3d.tracks.generation import GenerationTask
from codeverse3d.tracks.lifecycle import BaseTrack, StageRunner
from codeverse3d.tracks.prompting import (
    base_prompt_context,
    is_always_chapter,
    judge_digest,
    judged_sheet,
    reference_images,
    refine_inline_files,
    select_cookbook_chapters,
    skeleton_files,
)
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


# ===================================================================== recipe seeding
#: the harness-owned recipe file (``contracts.common.HARNESS_OWNED_SRC``; ``AgentJob.read_only``)
RECIPES_REL = HARNESS_OWNED_SRC[Language.GLSL_SHADER][0]
#: the agent's helper file — never written by this module, except :func:`trim_skeleton_common`
COMMON_REL = "src/common.glsl"
HEADER = ("// harness-owned: verified cookbook recipes matched to this brief — READ-ONLY (the harness pastes this "
          "above src/common.glsl); call these functions from shader.frag")
RESUME_HEADER = "// ---- appended on resume (recipes this file did not define yet) ----"
TRIM_NOTE = "// {names}: provided by src/recipes.glsl (harness-owned, pasted above this file) — call them, do not redefine them"
#: ``ctx.extra`` key → the prompt block (``[{name, signature, purpose}, …]``, every seeded recipe on disk)
EXTRA_KEY = "seeded_recipes"
#: chapters whose code is a TEMPLATE to adapt, not a library to call: the raymarching ``map()`` is the
#: agent's own scene, and a seeded ``map`` / ``calcNormal`` would collide with the one it must write
NOT_SEEDED: tuple[str, ...] = ("Raymarching",)

#: what each recipe is for — one line in the header comment and in the prompt block
PURPOSES: dict[str, str] = {
    "curtain": "ONE organic aurora / drapery / flame-sheet ribbon (folded lower edge, rays in bundles, gaps; "
               "k = 0 at the lower edge .. 1 at the top) — never a comb of bars",
    "auroraCol": "green-low → violet-crown aurora colour for the k that curtain() writes",
    "aurora": "the whole aurora: three curtains at different depths, nearest brightest "
              "(then col += au * 1.7 + au * au * 0.5 for bloom)",
    "stars": "dense sub-pixel star field on a grid (density 160 / 70, keep 0.2–0.3); add two layers",
    "bokehSoft": "soft gaussian city-light discs ADDED on a dark ground, three depth layers, pulsing",
    "dropsLayer": "rain on glass: (drop mask, trail) per grid cell scrolling down; refract the background with .x",
    "skyGrad": "dusk sky gradient by p.y", "sun": "sun disc with a soft halo",
    "waterHeight": "water surface height (waves + noise) for reflections / normals",
    "warped": "domain-warped fbm: billowy clouds, smoke, marble",
    "hash11": "1D hash 0..1", "hash12": "2D → float hash 0..1", "hash22": "2D → vec2 hash", "hash33": "3D → vec3 hash",
    "noise": "smooth 2D value noise 0..1", "noise3": "3D value noise (volumes, time as z)",
    "fbm": "6-octave rotated fbm", "ridged": "ridged fbm (mountains, veins)",
    "palette": "IQ cosine palette", "tonemapACES": "ACES tonemap", "gamma": "gamma 2.2",
    "sdCircle": "2D circle SDF", "sdBox": "2D box SDF", "sdSegment": "2D segment SDF", "sdStar": "2D star SDF",
    "fill": "1-px anti-aliased fill of an SDF", "stroke": "anti-aliased outline of an SDF",
    "glow": "exponential glow around an SDF", "rot2": "2D rotation matrix",
}

_FENCE = re.compile(r"```glsl[ \t]*\n(.*?)^```", re.S | re.M)
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_KEYWORDS = frozenset({"for", "if", "while", "switch", "return"})
_DEFINE = re.compile(r"^[ \t]*#\s*define\s+(\w+)", re.M)
#: top-level comment lines that are usage examples (``// usage: …``, ``// sunset: palette(…)``, a
#: statement ending in ``;``), not documentation of the function that follows
_USAGE = re.compile(r"^//\s*(?:usage|e\.g\.)\b|^//\s*[\w .-]+?:\s+\w+\(|;\s*(?://.*)?$", re.I)


@dataclass(frozen=True)
class Recipe:
    name: str
    signature: str
    text: str  # doc comment lines + the complete definition
    calls: frozenset[str]
    chapter: str

    @property
    def purpose(self) -> str:
        return PURPOSES.get(self.name, f'from the cookbook chapter "{self.chapter}"')

    def entry(self) -> dict[str, str]:
        return {"name": self.name, "signature": self.signature, "purpose": self.purpose}


def parse_functions(code: str, chapter: str = "") -> list[Recipe]:
    """Top-level function definitions in a GLSL text (``mainImage`` / ``main`` excluded), each with
    the documentation comment lines directly above it; usage comments and bare statements dropped."""
    lines = code.splitlines()
    out: list[Recipe] = []
    pending: list[str] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if s.startswith("//"):
            pending = [] if _USAGE.search(s) else pending + [raw]
            i += 1
            continue
        m = FUNC_DEF.match(raw)
        if not s or not m:  # blank, #define, or a bare statement snippet
            pending, i = [], i + 1
            continue
        depth, opened, j = 0, False, i
        while j < len(lines):
            code_only = strip_comments(lines[j])
            depth += code_only.count("{") - code_only.count("}")
            opened = opened or "{" in code_only
            j += 1
            if opened and depth <= 0:
                break
        body = lines[i:j]
        head = strip_comments(body[0])
        signature = (head[: head.index(")") + 1] if ")" in head else head).strip()
        text = "\n".join(pending + body)
        if m.group(1) not in ("mainImage", "main"):
            calls = frozenset(_CALL.findall(strip_comments(text))) - _KEYWORDS - {m.group(1)}
            out.append(Recipe(m.group(1), signature, text, calls, chapter))
        pending, i = [], j
    return out


def chapter_functions(section: Section) -> list[Recipe]:
    return [r for block in _FENCE.findall(section.body) for r in parse_functions(block, section.title)]


def cookbook_functions(md: str) -> dict[str, Recipe]:
    """Every function the cookbook defines, by name, in cookbook order (first definition wins)."""
    out: dict[str, Recipe] = {}
    for sec in split_sections(md):
        for r in chapter_functions(sec):
            out.setdefault(r.name, r)
    return out


def defined_names(glsl: str) -> set[str]:
    """Function and ``#define`` names a GLSL file already defines (what a seed must not repeat)."""
    names = set(_DEFINE.findall(glsl))
    names.update(m.group(1) for line in strip_comments(glsl).splitlines() if (m := FUNC_DEF.match(line)))
    return names


def with_helpers(wanted: list[Recipe], known: dict[str, Recipe]) -> list[Recipe]:
    """``wanted`` plus every cookbook function they call (transitively), callees before callers."""
    order: list[Recipe] = []
    seen: set[str] = set()

    def visit(r: Recipe) -> None:
        if r.name in seen:
            return
        seen.add(r.name)
        for dep in (n for n in known if n in r.calls):
            visit(known[dep])
        order.append(r)

    for r in wanted:
        visit(known.get(r.name, r))
    return order


def graphics_brief(ctx: RunContext) -> str:
    """The text the cookbook selection (prompt excerpt AND seeded recipes) is matched against."""
    plan = ctx.plan if isinstance(ctx.plan, GraphicsPlan) else None
    return ctx.spec.prompt + " " + " ".join(plan.key_visuals if plan else [])


def recipe_chapters(ctx: RunContext) -> list[Section]:
    """The brief's cookbook chapters minus the always-on ones and the templates (``NOT_SEEDED``)."""
    return [s for s in select_cookbook_chapters(ctx, graphics_brief(ctx))
            if s.level >= 2 and not is_always_chapter(s.title) and not is_always_chapter(s.title, NOT_SEEDED)]


def seeded_on_disk(glsl: str, known: dict[str, Recipe]) -> list[Recipe]:
    """The cookbook recipes a ``src/recipes.glsl`` carries, in file order (a file without the harness
    header is not a seed; only cookbook names count)."""
    if HEADER not in glsl:
        return []
    return [known[r.name] for r in parse_functions(glsl) if r.name in known]


def is_skeleton_common(text: str) -> bool:
    """Is this ``src/common.glsl`` still the harness skeleton — untouched, or only ever trimmed by
    :func:`trim_skeleton_common`?  (Every function it defines is the skeleton's own, verbatim.)"""
    if text == COMMON_GLSL:
        return True
    skeleton = {r.name: r.text for r in parse_functions(COMMON_GLSL)}
    return (text.startswith(COMMON_GLSL.splitlines()[0])
            and defined_names(text) <= defined_names(COMMON_GLSL)
            and all(skeleton.get(r.name) == r.text for r in parse_functions(text)))


def trim_skeleton_common(path: Path, provided: set[str]) -> list[str]:
    """Drop from the SKELETON's ``src/common.glsl`` the helpers ``src/recipes.glsl`` now provides — it is
    pasted first, so a second ``hash12`` in common.glsl is a redefinition and the first build would fail
    before the agent wrote a line.  Acts only while the file is still the harness's own skeleton text
    (the agent has not seen it yet); once the agent owns common.glsl a duplicate is its own to remove,
    and the lint names it.  Returns the names dropped."""
    if not path.is_file():
        return []
    text = path.read_text(errors="replace")
    if not is_skeleton_common(text):
        return []
    dropped = [r for r in parse_functions(text) if r.name in provided]
    if not dropped:
        return []
    for r in dropped:
        text = text.replace(r.text + "\n", "", 1)
    text = re.sub(r"\n{3,}", "\n\n", text)
    first, rest = text.split("\n", 1)
    note = TRIM_NOTE.format(names=", ".join(r.name for r in dropped))
    path.write_text(f"{first}\n{note}\n{rest}")
    return [r.name for r in dropped]


def seed_recipes(ctx: RunContext) -> list[str]:
    """Write the brief's verified recipes (+ their helpers) to the harness-owned ``src/recipes.glsl``
    (a resume appends only names the file does not define yet); return the names written THIS call.
    ``ctx.extra["seeded_recipes"]`` lists every seeded recipe on disk (for the prompt block);
    ``recipes.seeded`` is emitted with both.  No-op unless glsl_shader and enabled."""
    if ctx.language is not Language.GLSL_SHADER or not get_settings().limits.seed_recipes:
        return []
    known = cookbook_functions(ctx.cookbook_text or "")
    chapters = recipe_chapters(ctx)
    wanted = [r for s in chapters for r in chapter_functions(s)]
    path: Path = ctx.ws.root / RECIPES_REL
    existing = path.read_text(errors="replace") if path.is_file() else ""
    have = defined_names(existing)
    new = [r for r in with_helpers(wanted, known) if r.name not in have]
    trimmed: list[str] = []
    if new:
        head = [HEADER] if not existing else [RESUME_HEADER]
        block = [*head, *(f"//   {r.signature} — {r.purpose}" for r in new), "", *(r.text for r in new)]
        lead = "" if not existing else ("" if existing.endswith("\n") else "\n") + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(existing + lead + "\n".join(block) + "\n")
        existing = path.read_text(errors="replace")
        trimmed = trim_skeleton_common(ctx.ws.root / COMMON_REL, defined_names(existing))
    present = seeded_on_disk(existing, known)
    ctx.extra[EXTRA_KEY] = [r.entry() for r in present]
    ctx.events.emit("recipes.seeded", file=RECIPES_REL, names=[r.name for r in new], present=[r.name for r in present],
                    chapters=[s.title for s in chapters], trimmed=trimmed)
    return [r.name for r in new]


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
        # the recipes seed_recipes() put in the harness-owned src/recipes.glsl before the session ([] = no block)
        "seeded_recipes": list(ctx.extra.get(EXTRA_KEY) or []),
        "title": plan.title if plan else "Untitled effect",
        "style": plan.style if plan else "", "resolution": f"{res[0]}x{res[1]}", "duration": f"{plan.duration_s:g}" if plan else "8",
        "passes_table": passes_table(plan), "motion": plan.motion if plan else "", "key_visuals": list(plan.key_visuals) if plan else [],
        "uniforms": ", ".join(plan.uniforms) if plan and plan.uniforms else "u_time, u_resolution",
        "expected_files": ctx.runtime.expected_files(ctx.plan), "judge_times": "0, 1, 2.5, 4, 6 s", **extra})


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
    (``renders/rNN/metrics.json``), else the canonical file — the LAST build's, so for a round
    rendered before rounds kept a copy it is quoted only when no later round exists."""
    where = ws.renders_dir(round_index) if round_index is not None else None
    if where is not None and not (where / METRICS_NAME).is_file():
        if (ws.root / "rounds" / f"r{round_index + 1:02d}.json").is_file():
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

    def make_pipeline(self) -> GraphicsPipeline:
        return GraphicsPipeline()

    # ------------------------------------------------------------------ prepare
    def prepare(self, ctx: RunContext, runner: StageRunner) -> None:
        """Skeleton, then the brief's verified cookbook recipes into the harness-owned, read-only
        src/recipes.glsl (seed_recipes: measured, flash does not call a recipe it is only shown — and a
        recipe seeded into its own common.glsl was overwritten by the end of the run, so the file is one
        the agent cannot write: AgentJob.read_only)."""
        super().prepare(ctx, runner)
        if seed_recipes(ctx):
            ctx.ws.commit("recipes")

    # ------------------------------------------------------------------ baseline
    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        files = ctx.runtime.expected_files(ctx.plan)
        prompt = render(self.generate_template, **graphics_prompt_context(
            ctx, skeleton_files=skeleton_files(ctx) if ctx.single_shot else {}, previous_error=""))
        ctx.record_prompt("generate", prompt)
        return [GenerationTask(label="baseline", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=0,
                               kind="baseline", temperature=0.6, thinking="medium", owns_entry=True,
                               images=reference_images(ctx))]

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
                              kind="refine", temperature=0.5, thinking="medium", owns_entry=True,
                              images=reference_images(ctx) + judged_sheet(last))
