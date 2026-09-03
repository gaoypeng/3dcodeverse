"""``select_cookbook_excerpt``: the graphics prompt carries the cookbook chapters its brief calls for.

Measured 2026-08-26: ``cookbook_text[:7000]`` of an 11,298-char cookbook cut everything after
the raymarching template (sky / stars, rain, bokeh, feedback, PITFALLS) out of every graphics
prompt, and flash drew what it was handed — an aurora as a comb of bars, a star field of twenty
sparkles.  The selector hands over whole chapters, ranked by the brief, inside a budget.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from codeverse.contracts.common import Language, Track
from codeverse.prompts import PROMPTS_DIR, load_text
from codeverse.prompts.sections import split_sections

EXAMPLE = PROMPTS_DIR / "glsl_shader" / "examples" / "aurora_ridge.frag"


def test_real_cookbook_parses_into_chapters() -> None:
    secs = [s for s in split_sections(load_text("glsl_shader/cookbook.md")) if s.level == 2]
    assert len(secs) >= 10
    titles = [s.title for s in secs]
    assert any(t.startswith("Light phenomena") for t in titles)
    assert any(t.startswith("Gradient sky") for t in titles)
    assert any(t.startswith("PITFALLS") for t in titles)


def test_every_track_sends_the_whole_cookbook(monkeypatch) -> None:
    """No track delivers the cookbook by byte offset any more.

    It used to be ``ctx.cookbook_text[:6000]`` in base_prompt_context and a 9 000-char
    chapter selection in graphics — 13 % of blender's 46 623 chars, 10 % of
    scene_threejs's 57 631, cut mid-snippet.  On-demand cookbook lookup was the stated
    escape hatch and went unused in all 20 measured sessions, so what the prefix left
    out simply never reached the model.  Chapter SELECTION is still right where the
    stage knows which chapters it needs (scene's env/zone recipes, graphics' recipe
    seeding); it is not right as a way to shrink the reference itself.
    """
    from codeverse.tracks import graphics as graphics_steps
    from codeverse.tracks.prompting import base_prompt_context

    big = "\n\n".join(f"## chapter {i}\n" + "x" * 4000 for i in range(20))   # ~80 000 chars
    ctx = SimpleNamespace(plan=None, spec=SimpleNamespace(prompt="an aurora", constraints=None, must=[], must_not=[],
                                                          dims={}, reference_images=[], reference_notes=""),
                          track=Track.GRAPHICS, language=Language.GLSL_SHADER,
                          contract_text="CONTRACT", cookbook_rel="glsl_shader/cookbook.md",
                          cookbook_text=big, tool_cards="", single_shot=True, agent_kind="single-shot", extra={},
                          runtime=SimpleNamespace(entry_globs=()))
    monkeypatch.setattr(graphics_steps, "constraints_text", lambda spec: "")
    monkeypatch.setattr(graphics_steps, "reference_note", lambda ctx: "")
    monkeypatch.setattr(graphics_steps, "acceptance_lines", lambda plan: "")
    assert graphics_steps.graphics_prompt_context(ctx)["cookbook_excerpt"] == big

    monkeypatch.setattr("codeverse.tracks.prompting.constraints_text", lambda spec: "")
    monkeypatch.setattr("codeverse.tracks.prompting.reference_note", lambda ctx: "")
    monkeypatch.setattr("codeverse.tracks.prompting.acceptance_lines", lambda plan: "")
    assert base_prompt_context(ctx)["cookbook_excerpt"] == big
    assert base_prompt_context(ctx)["contract"] == "CONTRACT"


def test_aurora_example_ships_and_composes() -> None:
    from codeverse.languages.glsl_shader import compose

    assert EXAMPLE.is_file(), EXAMPLE
    src = EXAMPLE.read_text()
    assert "void mainImage(" in src and "float curtain(" in src
    composed = compose(src)
    assert "mainImage" in composed.source and "u_time" in composed.source
    assert "examples/aurora_ridge.frag" in load_text("glsl_shader/cookbook.md")
    assert Path(EXAMPLE).suffix == ".frag"
