"""Every prompt file exists, renders through the loader, and every language ships its two files."""

from __future__ import annotations

import jinja2
import pytest

from codeverse3d.contracts.common import Language
from codeverse3d.prompts import load_text, render
from codeverse3d.prompts.catalog import language_prompt
from tests.prompts.conftest import PROMPT_FILES, PROMPTS_DIR, is_template


@pytest.mark.parametrize("rel", PROMPT_FILES)
def test_prompt_file_contract(rel: str) -> None:
    raw = load_text(rel)
    if is_template(rel):
        jinja2.Environment().parse(raw)   # a template must at least compile
        return
    # quoted verbatim: must be jinja-inert, render() with no context a no-op
    assert render(rel) == raw, f"{rel} contains live jinja syntax; keep prompt .md files static"
    for seq in ("{{", "{%", "{#"):
        assert seq not in raw, f"{rel} contains {seq!r} which breaks jinja rendering"


def test_every_language_ships_a_system_prompt_and_a_contract_and_no_cookbook() -> None:
    """A missing per-language file silently reads as "" through ``language_text`` — so the two files
    every language needs are pinned present, and the cookbook pinned ABSENT for every language (D107,
    owner 2026-10-15: a cookbook that came back would be prompt material a model copies verbatim)."""
    from codeverse3d.prompts.catalog import language_text
    from codeverse3d.tracks.prompting import language_system_prompt

    for lang in Language:
        text = language_system_prompt(lang)
        assert text.strip(), f"{lang.value}: empty system prompt"
        for name in ("system.md", "contract.md"):
            assert (PROMPTS_DIR / language_prompt(lang, name)).is_file(), (lang.value, name)
        assert not (PROMPTS_DIR / language_prompt(lang, "cookbook.md")).is_file(), (lang.value, "ships a cookbook")
        assert language_text(lang, "cookbook.md") == ""
    assert language_prompt(Language.URDF_BLENDER, "contract.md") == "urdf/contract.md"
    for role in ("scope", "repair"):
        assert (PROMPTS_DIR / "system" / f"role_{role}.j2").is_file(), role
