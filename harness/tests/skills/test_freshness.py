"""Keep every harness name, callable, gate, constant and skill cited by a bundle live.

Foreign APIs and example names are explicit allowlists, so deleting or renaming harness
vocabulary fails with the document that still cites it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from codeverse3d.skills import bundle_dirs, iter_skills, skills_dir
from codeverse3d.skills.registry import ROUTED_SKILLS, ROUTES
from codeverse3d.spatial.registry import list_tools

BUNDLES = bundle_dirs()
SKILLS = list(iter_skills()) if BUNDLES else []
pytestmark = pytest.mark.skipif(not BUNDLES, reason=f"no bundles in {skills_dir()} yet")

HARNESS = Path(__file__).resolve().parents[2]

#: every markdown file a bundle ships — SKILL.md and its references are equally quotable
DOCS: list[Path] = sorted(p for d in BUNDLES for p in d.rglob("*.md"))

_FENCE = re.compile(r"^```.*?^```", re.S | re.M)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_CONST = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_GATE_SLUG = re.compile(
    r"\b(?:connectivity|contract|joint_sweep|motion_direction|scene_frames|gl_frames|lint"
    r"|shader|detail_drift|render_console|shader_preflight)/[a-z_]+\b")
#: a snake_case token in backticks is CLAIMED to be a name in this harness.  Which live
#: vocabulary it belongs to does not matter — that it belongs to one of them does.
_HARNESS_SHAPED = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
#: both spellings: the historical tool call and today's prose pointer
_COOKBOOK_SECTION = re.compile(r'(?:read_cookbook\(\s*section\s*=\s*"|cookbook sections? ")([^"]+)"')

#: ALL_CAPS tokens that are NOT ours, with the namespace that owns them.  Extend
#: deliberately: an author who adds a name here is asserting it is somebody else's.
FOREIGN: dict[str, str] = {
    "SIMPLE_DEFORM": "bpy modifier type enum",
    "DEPTH_TEST": "OpenGL capability (GL_DEPTH_TEST)",
    "GL_QUADS": "legacy OpenGL primitive enum",
    "USE_LOGDEPTHBUF": "three.js shader define",
    "USE_FOG": "three.js shader define",
    # variables inside a bundle's own worked examples
    "SEAT_T": "example constant", "SEAT_Z": "example constant",
    "X_0": "example part name", "X_3": "example part name",
    "C3D_SKILLS_UNVERIFIED": "our own switch, named in prose as an env var",
}

LIVE_TOOLS = {t.name for t in list_tools()}


def _module_constants() -> set[str]:
    out: set[str] = set()
    for p in (HARNESS / "codeverse3d").rglob("*.py"):
        for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*[:=]", p.read_text(errors="ignore"), re.M):
            out.add(m.group(1))
    return out


LIVE_CONSTANTS = _module_constants()


def _live_gate_kinds() -> set[str]:
    from codeverse3d.skills import registry

    return {v for k, v in vars(registry).items()
            if k.isupper() and isinstance(v, str) and "/" in v}


LIVE_KINDS = _live_gate_kinds()


def _live_vocabulary() -> set[str]:
    """Every word that appears anywhere in this harness's own source and prompts.

    Deliberately the WHOLE corpus rather than a curated list of tools: a skill quotes
    tool names, gate ids, rubric criteria, ``fix_hint`` field names, uniform names the
    glsl contract defines, lint rule ids and battery names, and every one of those is a
    checkable claim about something that exists.  Curating them by hand would mean the
    author of the next vocabulary has to remember to extend the test; asking "does this
    word occur anywhere in what we ship" needs no maintenance and still fails loudly the
    day a name is renamed out of the codebase.
    """
    words: set[str] = set()
    for root, exts in ((HARNESS / "codeverse3d", {".py", ".md", ".j2", ".yaml", ".yml", ".toml"}),
                       (HARNESS.parent / "eval" / "bench", {".py", ".yaml", ".yml"}),   # battery + prompt ids a bundle cites
                       (HARNESS / "runtime_js", {".js", ".mjs", ".glsl", ".json"})):
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if (not p.is_file() or p.suffix not in exts or "out" in p.parts
                    or "node_modules" in p.parts or "__pycache__" in p.parts
                    or p.parent.name.startswith("c3d-") or "_claims" in p.parts
                    or p.parent.parent.name.startswith("c3d-")):
                continue
            words |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", p.read_text(errors="ignore")))
    out = HARNESS.parent / "eval" / "bench" / "out"
    if out.is_dir():
        words |= {d.name for d in out.iterdir() if d.is_dir()}
    return words


LIVE_VOCABULARY = _live_vocabulary()

#: snake_case names that are NOT ours, with who owns them.  Extend deliberately.
FOREIGN_CALLS = {
    # GLSL / three.js / bpy builtins a bundle quotes while teaching that language
    "texture", "smoothstep", "fract", "mix", "clamp", "dot", "cross", "normalize", "length",
    "abs", "max", "min", "mod", "pow", "sin", "cos", "step", "reflect", "refract",
    "transform_apply", "primitive_cube_add", "ensure_lookup_table", "hide_set", "update",
    "len", "setup", "range", "print", "float", "int",
    "clean",  # cadquery/trimesh mesh op quoted while teaching welding
    # illustrative names inside a bundle's own worked example
    "left_wheel", "right_wheel",
    # batteries whose results are cited as evidence but whose output dir is gitignored
    "graphics_v1_flash", "graphics_v2_flash", "scenes_v1_flash",
}


def _squash(text: str) -> str:
    """One-line, lowercase — a section name wrapped across two markdown lines is one name."""
    return " ".join(text.lower().split())


def _prose(text: str) -> str:
    """The body with fenced code removed — a define inside a shader is not our vocabulary."""
    return _FENCE.sub("", text)


_DEF_RE = re.compile(r"^\s*(?:def|function)\s+([a-z_][a-z0-9_]*)\s*\(", re.M)


def _defined_callables() -> set[str]:
    """Every function the harness itself defines — python source plus the cookbooks'
    fenced snippets (a bundle may tell the agent to call ``radial_array`` by name)."""
    out: set[str] = set()
    for root, pattern in ((HARNESS / "codeverse3d", "*.py"), (HARNESS / "codeverse3d" / "prompts", "*.md")):
        for p in root.rglob(pattern):
            if "__pycache__" not in p.parts:
                out |= set(_DEF_RE.findall(p.read_text(errors="ignore")))
    return out


LIVE_CALLABLES = _defined_callables()


def test_every_bundle_document_only_cites_live_vocabulary():
    """Validate all bundle prose in one corpus pass, with path-specific diagnostics."""
    have = {d.name for d in BUNDLES} | set(ROUTED_SKILLS)
    fences = {
        d: "\n".join(_FENCE.findall("\n".join(p.read_text() for p in d.rglob("*.md"))))
        for d in BUNDLES
    }
    for doc in DOCS:
        label = doc.relative_to(skills_dir())
        raw = doc.read_text()
        prose = _prose(raw)
        named = {t.strip("`").split("(")[0].strip() for t in _INLINE_CODE.findall(prose)}
        unknown = sorted(
            {t for t in named if _HARNESS_SHAPED.match(t)} - LIVE_VOCABULARY - FOREIGN_CALLS
        )
        assert not unknown, f"{label} quotes names absent from the harness: {unknown}"

        called = {
            match.group(1)
            for token in _INLINE_CODE.findall(prose)
            if (match := re.match(r"^([a-z][a-z0-9_]*)\(", token.strip()))
        }
        bundle = next(d for d in BUNDLES if d == doc.parent or d in doc.parents)
        unknown = sorted(
            name
            for name in called
            if name not in LIVE_TOOLS
            and name not in FOREIGN_CALLS
            and name not in LIVE_CALLABLES
            and f"{name}(" not in fences[bundle]
        )
        assert not unknown, f"{label} instructs calling unknown functions: {unknown}"

        unknown = sorted(set(_GATE_SLUG.findall(prose)) - LIVE_KINDS)
        assert not unknown, f"{label} names undefined gate kinds: {unknown}"

        unknown = sorted(set(_CONST.findall(prose)) - LIVE_CONSTANTS - FOREIGN.keys())
        assert not unknown, f"{label} names unknown constants: {unknown}"

        siblings = set(re.findall(r"\bc3d-[a-z0-9-]+\b", raw))
        assert siblings <= have, f"{label} points at missing skills: {sorted(siblings - have)}"


def _languages_of(skill_name: str) -> set[str]:
    out: set[str] = set()
    for r in ROUTES:
        if r.skill == skill_name:
            out |= set(r.languages)
    return out


def _cookbook_sections_named_by(s) -> tuple[str, ...]:
    text = "\n".join([s.body] + [p.read_text() for p in sorted(s.dir.rglob("references/*.md"))])
    return tuple(sorted(set(_COOKBOOK_SECTION.findall(text))))


def test_every_named_cookbook_section_exists():
    """T4's other half: a skill may point at the cookbook, never invent a heading."""
    from codeverse3d.prompts import load_text
    from codeverse3d.prompts.catalog import PROMPT_DIRS
    from codeverse3d.prompts.sections import split_sections

    # the language-id → prompts/<dir> mapping has ONE home now (prompts/catalog.py);
    # this test used to import the third of its copies
    _PROMPT_DIR = {k.value: v for k, v in PROMPT_DIRS.items()}

    for s in SKILLS:
        wanted = _cookbook_sections_named_by(s)
        if not wanted:
            continue
        langs = _languages_of(s.name) or {"blender"}
        titles: set[str] = set()
        for lang in langs:
            d = _PROMPT_DIR.get(lang, lang)
            try:
                md = load_text(f"{d}/cookbook.md")
            except (FileNotFoundError, OSError):
                continue
            titles |= {sec.title.lower() for sec in split_sections(md)}
        for want in map(_squash, wanted):
            assert any(want in _squash(title) for title in titles), (
                f"{s.name} names cookbook section {want!r}, absent from {sorted(langs)}"
            )


def test_every_live_constant_named_by_a_skill_has_a_claim():
    from codeverse3d.addons.skill_targets import load_claims

    for s in SKILLS:
        pinned = {
            str(row["python"]).rsplit(":", 1)[-1]
            for row in load_claims(s.name)
            if row.get("python")
        }
        named = set(_CONST.findall(_prose(s.body))) & LIVE_CONSTANTS
        unpinned = sorted(named - pinned)
        assert not unpinned, f"{s.name} names live constants without claims: {unpinned}"
