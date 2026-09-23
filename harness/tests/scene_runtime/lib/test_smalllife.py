"""smalllife.js — regression: a camera inside a swarm put a butterfly across a quarter of the frame."""

from __future__ import annotations

from tests.scene_runtime.lib._probe import LIB_DIR


def test_an_insect_at_the_lens_fades_instead_of_becoming_the_subject():
    """The close ones fade in the shader, measured from the instance centre, so
    every swarm gets it."""
    src = (LIB_DIR / "smalllife.js").read_text(encoding="utf-8")
    # The fade is measured from the INSTANCE centre in world space, not from
    # the mesh origin: every insect in one swarm shares that.
    assert "distance(cameraPosition, cW)" in src
    assert "vNear = smoothstep(0.35, 1.8" in src
    # And it must actually reach the alpha, not just be computed.
    assert "a *= vNear;" in src
