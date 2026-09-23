"""Every track sends the whole cookbook; the GLSL cookbook's aurora example ships (Q9)."""

from __future__ import annotations

from types import SimpleNamespace

from codeverse3d.contracts.common import Language, Track
from codeverse3d.prompts import PROMPTS_DIR, load_text

EXAMPLE = PROMPTS_DIR / "glsl_shader" / "examples" / "aurora_ridge.frag"


def test_every_track_sends_the_whole_cookbook(monkeypatch) -> None:
    from codeverse3d.languages import get_runtime
    from codeverse3d.tracks import graphics as graphics_steps
    from codeverse3d.tracks.prompting import base_prompt_context

    big = "\n\n".join(f"## chapter {i}\n" + "x" * 4000 for i in range(20))   # ~80 000 chars
    ctx = SimpleNamespace(plan=None, spec=SimpleNamespace(prompt="an aurora", constraints=None, must=[], must_not=[],
                                                          dims={}, reference_images=[], reference_notes=""),
                          track=Track.GRAPHICS, language=Language.GLSL_SHADER,
                          contract_text="CONTRACT",
                          cookbook_text=big, tool_cards="", single_shot=True, extra={},
                          runtime=get_runtime(Language.GLSL_SHADER))
    monkeypatch.setattr("codeverse3d.tracks.prompting.constraints_text", lambda spec: "")
    monkeypatch.setattr("codeverse3d.tracks.prompting.reference_note", lambda ctx: "")
    monkeypatch.setattr("codeverse3d.tracks.prompting.acceptance_lines", lambda plan: "")
    assert graphics_steps.graphics_prompt_context(ctx)["cookbook_excerpt"] == big
    assert base_prompt_context(ctx)["cookbook_excerpt"] == big
    assert base_prompt_context(ctx)["contract"] == "CONTRACT"


def test_aurora_example_ships_and_composes() -> None:
    from codeverse3d.languages.glsl_shader import compose

    assert EXAMPLE.is_file(), EXAMPLE
    src = EXAMPLE.read_text()
    assert "void mainImage(" in src and "float curtain(" in src
    composed = compose(src)
    assert "mainImage" in composed.source and "u_time" in composed.source
    assert "examples/aurora_ridge.frag" in load_text("glsl_shader/cookbook.md")
