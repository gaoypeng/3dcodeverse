"""The GLSL cookbook's aurora example ships where the cookbook says it is (Q9)."""

from __future__ import annotations

from codeverse3d.prompts import PROMPTS_DIR, load_text

EXAMPLE = PROMPTS_DIR / "glsl_shader" / "examples" / "aurora_ridge.frag"


def test_aurora_example_ships_and_composes() -> None:
    from codeverse3d.languages.glsl_shader import compose

    assert EXAMPLE.is_file(), EXAMPLE
    src = EXAMPLE.read_text()
    assert "void mainImage(" in src and "float curtain(" in src
    composed = compose(src)
    assert "mainImage" in composed.source and "u_time" in composed.source
    assert "examples/aurora_ridge.frag" in load_text("glsl_shader/cookbook.md")
