"""Seed the cookbook's verified GLSL recipes into ``src/common.glsl`` BEFORE the session.

Measured 2026-08-26 (refs_v2_graphics, aurora brief, gemini-3.7-flash api-agent, shader_v2
judge): the baseline prompt carried the cookbook's "Light phenomena" chapter — five mentions of
``curtain(`` in ``trajectories/baseline_r00/prompt.md`` — and the agent called none of them
(``grep -c "curtain(" src/shader.frag`` = 0 in both finished runs): round 0 was again a comb
of bars (``comb_artefact``, 0.33), a later round a washed-out blur (0.12).  Showing flash a
recipe is not the same as flash using it, so the generator-side lever is to put the matched
recipes ON DISK: the chapters :func:`select_cookbook_chapters` picks for the brief (minus the
always-on helper / grading / pitfalls chapters), their ``glsl`` code blocks reduced to function
definitions (no usage comments, no ``mainImage``), plus exactly the helpers those functions call
(``hash12`` / ``noise`` / ``fbm`` …), appended to ``src/common.glsl`` under a header the prompt
names.  A resume appends only the names the file does not define yet.  Gate:
``Settings.limits.seed_recipes`` / ``CV3D_SEED_RECIPES`` (:func:`seed_recipes_enabled`).
glsl_shader only — ``opengl_python`` programs own their shader strings, nothing is seeded there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from codeverse.config import seed_recipes_enabled
from codeverse.contracts.common import Language
from codeverse.contracts.plan import GraphicsPlan
from codeverse.languages.glsl_shader.wrap import strip_comments
from codeverse.spatial.cookbook_tool import Section, split_sections
from codeverse.tracks.common import RunContext
from codeverse.tracks.prompting import is_always_chapter, select_cookbook_chapters

COMMON_REL = "src/common.glsl"
HEADER = "// ---- harness-seeded verified recipes (from the GLSL cookbook; call them, do not rewrite them) ----"
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
_FUNC = re.compile(r"^[ \t]*(?:float|int|bool|void|[bi]?vec[234]|mat[234])\s+(\w+)\s*\(")
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
        m = _FUNC.match(raw)
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
    names.update(m.group(1) for line in strip_comments(glsl).splitlines() if (m := _FUNC.match(line)))
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
    """The cookbook recipes a ``common.glsl`` carries under the harness header, in file order (the
    agent's own helpers below the header are not "seeded", so only cookbook names count)."""
    if HEADER not in glsl:
        return []
    return [known[r.name] for r in parse_functions(glsl.split(HEADER, 1)[1]) if r.name in known]


def seed_recipes(ctx: RunContext) -> list[str]:
    """Append the brief's verified recipes (+ their helpers) to ``src/common.glsl``; return the names
    written THIS call.  ``ctx.extra["seeded_recipes"]`` lists every seeded recipe on disk (for the
    prompt block); ``recipes.seeded`` is emitted with both.  No-op unless glsl_shader and enabled."""
    if ctx.language is not Language.GLSL_SHADER or not seed_recipes_enabled():
        return []
    known = cookbook_functions(ctx.cookbook_text or "")
    chapters = recipe_chapters(ctx)
    wanted = [r for s in chapters for r in chapter_functions(s)]
    path: Path = ctx.ws.root / COMMON_REL
    existing = path.read_text(errors="replace") if path.is_file() else ""
    have = defined_names(existing)
    new = [r for r in with_helpers(wanted, known) if r.name not in have]
    if new:
        block = [HEADER, *(f"//   {r.signature} — {r.purpose}" for r in new), "", *(r.text for r in new)]
        lead = "" if not existing else ("" if existing.endswith("\n") else "\n") + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(existing + lead + "\n".join(block) + "\n")
        existing = path.read_text(errors="replace")
    present = seeded_on_disk(existing, known)
    ctx.extra[EXTRA_KEY] = [r.entry() for r in present]
    ctx.events.emit("recipes.seeded", names=[r.name for r in new], present=[r.name for r in present],
                    chapters=[s.title for s in chapters])
    return [r.name for r in new]
