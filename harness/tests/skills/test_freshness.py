"""Freshness: every live name a bundle quotes must still exist in the code today.

WHY this file is the reason the library can be trusted in a month.  A skill's whole
value is that its names and numbers are OUR names and numbers; a sentence naming a tool
we renamed, a gate kind we retired or a constant we deleted is not merely useless, it
sends the agent to look for something that is not there and costs a turn to find out.
``test_library`` pins the numbers a bundle DECLARES in ``_claims``; this file pins the
vocabulary it merely mentions, which is the part an author can forget to declare.

The four vocabularies are checked, not guessed at: harness tool names live in the tool
registry, gate kinds in ``skills.registry``, cookbook sections in the language cookbook
the harness inlines into the prompts, and constants in the source.  Anything from a
foreign namespace (bpy, GL, GLSL, three.js) is listed in :data:`FOREIGN` with the library
it belongs to — an allowlist an author has to extend deliberately, so the test keeps
meaning something.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from codeverse.skills import bundle_dirs, iter_skills, skills_dir
from codeverse.skills.registry import ROUTED_SKILLS, ROUTES
from codeverse.spatial.registry import list_tools

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
_ENV = re.compile(r"\bCV3D_[A-Z0-9_]+\b")

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
    "CV3D_SKILLS_UNVERIFIED": "our own switch, named in prose as an env var",
}

LIVE_TOOLS = {t.name for t in list_tools()}




def _module_constants() -> set[str]:
    out: set[str] = set()
    for p in (HARNESS / "codeverse").rglob("*.py"):
        for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*[:=]", p.read_text(errors="ignore"), re.M):
            out.add(m.group(1))
    return out


LIVE_CONSTANTS = _module_constants()


def _live_gate_kinds() -> set[str]:
    from codeverse.skills import registry

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
    for root, exts in ((HARNESS / "codeverse", {".py", ".md", ".j2", ".yaml", ".yml", ".toml"}),
                       (HARNESS / "bench", {".py", ".yaml", ".yml"}),
                       (HARNESS / "runtime_js", {".js", ".mjs", ".glsl", ".json"})):
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            # bench/out is live battery output — 5 781 of the 6 128 files this used to
            # read, 35 MB of 39 MB, and it grows with every run.  The only thing wanted
            # from it is battery NAMES, collected by iterdir() below.
            if (not p.is_file() or p.suffix not in exts or "out" in p.parts
                    or "node_modules" in p.parts or "__pycache__" in p.parts
                    or p.parent.name.startswith("cv3d-") or "_claims" in p.parts
                    or p.parent.parent.name.startswith("cv3d-")):
                continue
            words |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", p.read_text(errors="ignore")))
    out = HARNESS / "bench" / "out"      # battery names a bundle cites as its evidence
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


@pytest.mark.parametrize("doc", DOCS, ids=[str(p.relative_to(skills_dir())) for p in DOCS])
def test_every_harness_name_a_bundle_quotes_is_still_a_live_name(doc: Path):
    """`check_connectivity` must not become a name only the skill remembers.

    Wider than the tool registry on purpose: a rubric criterion a skill tells the agent to
    optimise is as much a live name as a tool, and rubric criteria get renamed.
    """
    text = _prose(doc.read_text())
    named = {t.strip("`").split("(")[0].strip() for t in _INLINE_CODE.findall(text)}
    named = {t for t in named if _HARNESS_SHAPED.match(t)}
    unknown = sorted(named - LIVE_VOCABULARY - FOREIGN_CALLS)
    assert unknown == [], (
        f"{doc.name} quotes {unknown}, which no tool, gate, rubric, criterion, language or "
        f"contract entry point answers to today")


_DEF_RE = re.compile(r"^\s*(?:def|function)\s+([a-z_][a-z0-9_]*)\s*\(", re.M)


def _defined_callables() -> set[str]:
    """Every function the harness itself defines — python source plus the cookbooks'
    fenced snippets (a bundle may tell the agent to call ``radial_array`` by name)."""
    out: set[str] = set()
    for root, pattern in ((HARNESS / "codeverse", "*.py"), (HARNESS / "codeverse" / "prompts", "*.md")):
        for p in root.rglob(pattern):
            if "__pycache__" not in p.parts:
                out |= set(_DEF_RE.findall(p.read_text(errors="ignore")))
    return out


LIVE_CALLABLES = _defined_callables()


@pytest.mark.parametrize("doc", DOCS, ids=[str(p.relative_to(skills_dir())) for p in DOCS])
def test_every_call_a_bundle_instructs_is_a_live_tool_or_known_callable(doc: Path):
    """``read_cookbook(section=...)`` survived its tool's deletion in four bundles.

    A call-shaped instruction in prose must name a live MCP tool (:data:`LIVE_TOOLS`),
    a callable the harness still defines, a function the bundle's own worked examples
    define, or a declared-foreign builtin — never a name only the skill remembers.
    """
    text = doc.read_text()
    bundle = next(d for d in BUNDLES if d == doc.parent or d in doc.parents)
    corpus = "\n".join(q.read_text() for q in sorted(bundle.rglob("*.md")))
    fences = "\n".join(_FENCE.findall(corpus))
    called = set()
    for tok in _INLINE_CODE.findall(_prose(text)):
        m = re.match(r"^([a-z][a-z0-9_]*)\(", tok.strip())
        if m:
            called.add(m.group(1))
    unknown = sorted(n for n in called
                     if n not in LIVE_TOOLS and n not in FOREIGN_CALLS
                     and n not in LIVE_CALLABLES and f"{n}(" not in fences)
    assert unknown == [], (
        f"{doc.name} instructs calling {unknown} — not a live tool, not a harness "
        f"callable, not one of its own example functions, and not declared in "
        f"FOREIGN_CALLS")


@pytest.mark.parametrize("doc", DOCS, ids=[str(p.relative_to(skills_dir())) for p in DOCS])
def test_every_gate_kind_a_bundle_names_is_a_live_kind(doc: Path):
    """A reworded gate that changes a slug must break here, not silently unroute a sheet."""
    slugs = set(_GATE_SLUG.findall(_prose(doc.read_text())))
    unknown = sorted(slugs - LIVE_KINDS)
    assert unknown == [], f"{doc.name} names gate kind(s) registry.py does not define: {unknown}"


@pytest.mark.parametrize("doc", DOCS, ids=[str(p.relative_to(skills_dir())) for p in DOCS])
def test_every_constant_a_bundle_names_in_prose_is_ours_or_declared_foreign(doc: Path):
    toks = set(_CONST.findall(_prose(doc.read_text())))
    unknown = sorted(t for t in toks if t not in LIVE_CONSTANTS and t not in FOREIGN)
    assert unknown == [], (
        f"{doc.name} names {unknown}: either the constant moved, or add it to FOREIGN "
        f"with the namespace that owns it")


@pytest.mark.parametrize("doc", DOCS, ids=[str(p.relative_to(skills_dir())) for p in DOCS])
def test_every_sibling_skill_a_bundle_names_exists(doc: Path):
    named = set(re.findall(r"\bcv3d-[a-z0-9-]+\b", doc.read_text()))
    have = {d.name for d in BUNDLES} | set(ROUTED_SKILLS)
    assert named <= have, f"{doc.name} points at missing skill(s): {sorted(named - have)}"


def _languages_of(skill_name: str) -> set[str]:
    out: set[str] = set()
    for r in ROUTES:
        if r.skill == skill_name:
            out |= set(r.languages)
    return out


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_every_cookbook_section_a_skill_names_exists_in_that_cookbook(s):
    """T4's other half: a skill may point at the cookbook, never invent a heading."""
    from codeverse.prompts import load_text
    from codeverse.prompts.catalog import PROMPT_DIRS
    from codeverse.prompts.sections import split_sections

    # the language-id → prompts/<dir> mapping has ONE home now (prompts/catalog.py);
    # this test used to import the third of its copies
    _PROMPT_DIR = {k.value: v for k, v in PROMPT_DIRS.items()}

    text = "\n".join([s.body] + [p.read_text() for p in sorted(s.dir.rglob("references/*.md"))])
    wanted = set(_COOKBOOK_SECTION.findall(text))
    if not wanted:
        pytest.skip("names no cookbook section")
    langs = _languages_of(s.name) or {"blender"}
    titles: set[str] = set()
    for lang in langs:
        d = _PROMPT_DIR.get(lang, lang)
        try:
            md = load_text(f"{d}/cookbook.md")
        except (FileNotFoundError, OSError):
            continue
        titles |= {sec.title.lower() for sec in split_sections(md)}
    for want in sorted(_squash(w) for w in wanted):
        assert any(want in _squash(t) for t in titles), (
            f"{s.name} sends the agent to read_cookbook(section={want!r}), "
            f"which no cookbook of {sorted(langs)} has")


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_a_constant_a_body_names_is_also_pinned_by_a_claim(s):
    """Naming `PENETRATION_WARN_M` in prose is a promise about its VALUE.

    ``check_claims`` verifies the rows a bundle declares; nothing verified the rows it
    forgot.  A body that names one of our constants is quoting the number beside it, so
    that number needs a claim row — otherwise moving the constant leaves a confident
    sentence with the old value and no test to notice.  Found `DUPLICATE_DIFF` quoted at
    1e-4 in cv3d-glsl-craft with no row behind it.
    """
    from codeverse.skills.targets import load_claims

    pinned = {str(r["python"]).rsplit(":", 1)[-1] for r in load_claims(s.name) if r.get("python")}
    named = {c for c in _CONST.findall(_prose(s.body)) if c in LIVE_CONSTANTS}
    unpinned = sorted(named - pinned)
    assert unpinned == [], (
        f"{s.name} names {unpinned} in prose but pins no claim to it — add a "
        f"[[claim]] row to skills/_claims/{s.name}.toml")


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_the_target_metric_a_bundle_declares_is_still_one_we_read_out(s):
    """The fifth vocabulary: a bundle's own falsifiable claim.

    ``metadata.target_metric`` is the quantity ``bench/skill_targets.py`` prints for this
    bundle.  If the row is renamed or dropped, the frontmatter keeps promising a number
    nothing computes, and the next A/B reads a silent zero as "no effect".
    """
    from codeverse.skills.targets import METRICS, target_for

    metric = s.metadata.get("target_metric", "")
    assert metric, (
        f"{s.name}: metadata.target_metric is missing — every bundle owes one deterministic "
        f"claim (docs/SKILLS_LEDGER.md)")
    assert metric in METRICS, (
        f"{s.name} declares target_metric {metric!r}, which skills/targets.py no longer defines")
    row = target_for(s.name)
    assert row is not None and row.metric == metric
