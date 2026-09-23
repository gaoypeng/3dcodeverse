"""Every prompt file exists, renders through the loader, is reached by the package, and keeps its promises."""

from __future__ import annotations

import ast
from fnmatch import fnmatch

import jinja2
import pytest

from codeverse3d.contracts.common import TRACK_LANGUAGES, Language, Track
from codeverse3d.prompts import load_text, prompt_hash, render
from codeverse3d.prompts.catalog import language_prompt
from tests.prompts.conftest import DOC_FILES, PROMPT_FILES, PROMPTS_DIR, is_template, read_prompt

#: files nothing in the package loads, each with the reason it still ships
UNREACHED = {
    # the GLSL cookbook tells the agent this example is shipped, but no code copies it into a
    # workspace (review 2026-09-22, owner question Q9: ship it where the cookbook says, or drop it)
    "glsl_shader/examples/aurora_ridge.frag",
}


@pytest.mark.parametrize("rel", PROMPT_FILES)
def test_prompt_file_contract(rel: str) -> None:
    raw = load_text(rel)
    assert prompt_hash(raw)
    if is_template(rel):
        jinja2.Environment().parse(raw)   # a template must at least compile
        return
    # quoted verbatim: must be jinja-inert, render() with no context a no-op
    assert render(rel) == raw, f"{rel} contains live jinja syntax; keep prompt .md files static"
    for seq in ("{{", "{%", "{#"):
        assert seq not in raw, f"{rel} contains {seq!r} which breaks jinja rendering"


def test_every_prompt_file_is_reached_by_the_package() -> None:
    """A prompt file is loaded by a literal path, an f-string path (``system/role_{role}.j2``) or a
    per-language name through ``catalog.language_prompt`` / ``language_text`` — or it is dead."""
    literals: set[str] = set()
    patterns: list[str] = []
    names: set[str] = set()
    for py in (PROMPTS_DIR.parent).rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
            elif isinstance(node, ast.JoinedStr) and isinstance(head := node.values[0], ast.Constant) and "/" in str(head.value):
                patterns.append("".join(v.value if isinstance(v, ast.Constant) else "*" for v in node.values))
            elif (isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("language_prompt", "language_text")
                  and len(node.args) == 2 and isinstance(node.args[1], ast.Constant)):
                names.add(node.args[1].value)
    per_language = {language_prompt(lang, n) for lang in Language for n in names}
    reached = {f for f in PROMPT_FILES if f in literals | per_language or any(fnmatch(f, p) for p in patterns)}
    assert set(PROMPT_FILES) - reached == UNREACHED


@pytest.mark.parametrize("rel", [f for f in DOC_FILES if f.endswith("cookbook.md")])
def test_cookbook_structure(rel: str) -> None:
    text = read_prompt(rel)
    headings = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
    assert len(headings) >= 5, f"{rel}: cookbooks are chaptered with '## ' headings"
    assert len(headings) == len(set(headings)), f"{rel}: duplicate section headings"
    assert "pitfalls" in " | ".join(headings).lower(), f"{rel}: needs a Pitfalls chapter"


@pytest.mark.parametrize(
    # a shader draws in screen space: it has no metres and no 3D frame to state
    "rel", sorted(language_prompt(lang, "contract.md") for t, langs in TRACK_LANGUAGES.items() if t is not Track.GRAPHICS
                  for lang in langs)
)
def test_contract_content(rel: str) -> None:
    text = read_prompt(rel)
    assert "COMPLETE minimal example" in text, f"{rel}: must carry a complete runnable example"
    low = text.lower()
    assert "meter" in low or "metre" in low, f"{rel}: units must be stated"


def test_frames_consistent_with_conventions() -> None:
    """Contracts restate the frame; they must agree with conventions.py (Z-up −Y-front
    for blender/cadquery/urdf, Y-up +Z-front for the three.js languages)."""
    for rel in ("blender/contract.md", "cadquery/contract.md", "urdf/contract.md"):
        text = read_prompt(rel).replace("\u2212", "-")
        assert "Z up" in text or "Z is up" in text, f"{rel}: must state Z up"
        assert "-Y front" in text or "-Y is the front" in text, f"{rel}: must state -Y front"
    for rel in ("threejs/contract.md", "scene_threejs/contract.md"):
        text = read_prompt(rel).replace("\u2212", "-")
        assert "Y is up" in text or "Y-up" in text, f"{rel}: must state Y up"
        assert "+Z front" in text or "+Z is the front" in text, f"{rel}: must state +Z front"


def test_system_prompts_cover_the_laws() -> None:
    hc = read_prompt("system/harness_contract.md")
    for needle in ("artifacts/", "render_sheet", "check_connectivity", "2 mm",
                   "Definition of done", "ground", "seed"):
        assert needle.lower() in hc.lower(), f"harness_contract.md must mention {needle!r}"
    # system/tools_usage.md was a hand-written tool list with no production reader
    # (deleted 2026-08-28): the agent's tool section is GENERATED from the @tool
    # registrations by agents/materialize._tool_section, so "every tool is documented"
    # is true by construction and this assertion was checking a file nobody read.


def test_every_language_ships_a_system_prompt_a_contract_and_a_cookbook() -> None:
    """The generator's system prompt, the authoring contract and the cookbook are per
    LANGUAGE and live in the prompt corpus, found by ``prompts.catalog.language_prompt``.

    The system prompt used to be an f-string in each track class, and the three static-object
    languages shared one sentence with the name swapped in — bpy mesh modelling,
    CadQuery's B-rep workplanes and three.js BufferGeometry, told the same thing.
    A missing file here means a language silently falls back to nothing (``language_text``
    reads a file a language does not ship as "").
    """
    from codeverse3d.contracts.common import Language
    from codeverse3d.prompts.catalog import language_prompt
    from codeverse3d.tracks.prompting import language_system_prompt

    for lang in Language:
        text = language_system_prompt(lang)
        assert text.strip(), f"{lang.value}: empty system prompt"
        for name in ("system.md", "contract.md", "cookbook.md"):
            assert (PROMPTS_DIR / language_prompt(lang, name)).is_file(), (lang.value, name)
    assert language_prompt(Language.URDF_BLENDER, "cookbook.md") == "urdf/cookbook.md"
    for role in ("scope", "repair"):
        assert (PROMPTS_DIR / "system" / f"role_{role}.j2").is_file(), role
