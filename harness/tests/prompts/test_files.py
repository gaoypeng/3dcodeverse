"""Every prompt file exists, renders through the loader, and keeps its promises."""

from __future__ import annotations

import pytest

from codeverse3d.prompts import load_text, prompt_hash, render
from tests.prompts.conftest import PROMPT_FILES, PROMPTS_DIR, read_prompt


@pytest.mark.parametrize("rel", PROMPT_FILES)
def test_prompt_file_contract(rel: str) -> None:
    p = PROMPTS_DIR / rel
    assert p.is_file(), f"missing prompt file {rel}"
    raw = load_text(rel)
    assert prompt_hash(raw)
    # markdown prompts must be jinja-inert: render() with no context must be a no-op
    rendered = render(rel)
    assert rendered == raw, f"{rel} contains live jinja syntax; keep prompt .md files static"
    for seq in ("{{", "{%", "{#"):
        assert seq not in raw, f"{rel} contains {seq!r} which breaks jinja rendering"


@pytest.mark.parametrize(
    "rel",
    [f for f in PROMPT_FILES if f.endswith("cookbook.md")],
)
def test_cookbook_structure(rel: str) -> None:
    text = read_prompt(rel)
    headings = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
    assert len(headings) >= 5, f"{rel}: cookbooks are chaptered with '## ' headings"
    assert len(headings) == len(set(headings)), f"{rel}: duplicate section headings"
    joined = " | ".join(headings)
    assert "Pitfalls" in joined, f"{rel}: needs a Pitfalls chapter"


@pytest.mark.parametrize(
    "rel", [f for f in PROMPT_FILES if f.endswith("contract.md") and not f.startswith("system/")]
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


def test_every_language_ships_a_system_prompt() -> None:
    """The generator's system prompt is per LANGUAGE and lives in the prompt corpus.

    It used to be an f-string in each track class, and the three static-object
    languages shared one sentence with the name swapped in — bpy mesh modelling,
    CadQuery's B-rep workplanes and three.js BufferGeometry, told the same thing.
    A missing file here means a language silently falls back to nothing.
    """
    from codeverse3d.contracts.common import Language
    from codeverse3d.prompts.catalog import prompt_dir_for
    from codeverse3d.tracks.prompting import language_system_prompt

    for lang in Language:
        text = language_system_prompt(lang)
        assert text.strip(), f"{lang.value}: empty system prompt"
        assert (PROMPTS_DIR / prompt_dir_for(lang) / "system.md").is_file(), lang.value
    for role in ("scope", "repair"):
        assert (PROMPTS_DIR / "system" / f"role_{role}.j2").is_file(), role
