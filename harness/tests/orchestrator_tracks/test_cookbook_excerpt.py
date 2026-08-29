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
from codeverse.tracks.prompting import COOKBOOK_ALWAYS, select_cookbook_excerpt

AURORA = "Aurora borealis over a mountain ridge with a frozen lake, dense stars, green and violet curtains"
RAIN = "rain drops running down a window at night, blurred city lights behind the glass"
EXAMPLE = PROMPTS_DIR / "glsl_shader" / "examples" / "aurora_ridge.frag"


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(cookbook_text=load_text("glsl_shader/cookbook.md"))


def _titles(text: str) -> list[str]:
    return [s.title for s in split_sections(text) if s.level == 2]


def _chapter_bodies(md: str) -> dict[str, str]:
    return {s.title: s.body.rstrip() for s in split_sections(md)}


def test_real_cookbook_parses_into_chapters() -> None:
    secs = [s for s in split_sections(load_text("glsl_shader/cookbook.md")) if s.level == 2]
    assert len(secs) >= 10
    titles = [s.title for s in secs]
    assert any(t.startswith("Light phenomena") for t in titles)
    assert any(t.startswith("Gradient sky") for t in titles)
    assert any(t.startswith("PITFALLS") for t in titles)


def test_aurora_brief_selects_light_and_sky_within_budget() -> None:
    ctx = _ctx()
    text = select_cookbook_excerpt(ctx, AURORA)
    titles = _titles(text)
    assert any(t.startswith("Light phenomena") for t in titles), titles
    assert any(t.startswith("Gradient sky") for t in titles), titles
    assert len(text) <= 9000
    assert "float curtain(vec2 p, float t, float seed, out float k)" in text
    assert "float stars(vec2 p, float density, float keep)" in text
    # never split: every chapter in the excerpt is the whole chapter from the cookbook
    whole = _chapter_bodies(ctx.cookbook_text)
    for s in split_sections(text):
        assert s.body.rstrip() == whole[s.title], s.title
    # deterministic, cookbook order
    assert text == select_cookbook_excerpt(ctx, AURORA)
    order = [t for t in whole if t in titles]
    assert titles == order


def test_rain_brief_selects_rain() -> None:
    text = select_cookbook_excerpt(_ctx(), RAIN)
    titles = _titles(text)
    assert any(t.startswith("Rain") for t in titles), titles
    assert any(t.startswith("Bokeh") for t in titles), titles
    assert len(text) <= 9000


def test_always_chapters_present_for_any_brief() -> None:
    ctx = _ctx()
    for brief in ("", "xyzzy plugh", AURORA, RAIN, "a raymarched temple corridor with fog"):
        text = select_cookbook_excerpt(ctx, brief)
        assert text.startswith("# glsl_shader cookbook"), brief          # the p / uv conventions header
        titles = _titles(text)
        for name in COOKBOOK_ALWAYS:
            assert any(name.lower() in t.lower() for t in titles), (brief, name)
    assert select_cookbook_excerpt(SimpleNamespace(cookbook_text=""), AURORA) == ""


def test_budget_never_cuts_a_chapter() -> None:
    ctx = _ctx()
    small = select_cookbook_excerpt(ctx, AURORA, budget=6000)
    whole = _chapter_bodies(ctx.cookbook_text)
    for s in split_sections(small):
        assert s.body.rstrip() == whole[s.title], s.title
    assert not any(t.startswith("Light phenomena") for t in _titles(small))   # 3.2 k does not fit in 1.6 k of room
    assert any(t.startswith("Gradient sky") for t in _titles(small))          # the 1.3 k chapter that does


def test_every_track_sends_the_whole_cookbook(monkeypatch) -> None:
    """No track delivers the cookbook by byte offset any more.

    It used to be ``ctx.cookbook_text[:6000]`` in base_prompt_context and a 9 000-char
    chapter selection in graphics — 13 % of blender's 46 623 chars, 10 % of
    scene_threejs's 57 631, cut mid-snippet.  The read_cookbook tool was the stated
    escape hatch and went uncalled in all 20 measured sessions, so what the prefix left
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
                          cookbook_text=big, tool_cards="", single_shot=True,
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
