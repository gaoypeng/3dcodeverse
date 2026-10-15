"""The judge numbers ``generate_graphics.j2`` states are the rubric's (the gate thresholds it states are
pinned in ``prompts/_claims.toml``): change ``shader_v2.yaml`` and this fails until the prompt agrees."""

from __future__ import annotations

from codeverse3d.judges.rubrics import load_rubric
from codeverse3d.prompts import load_text


def test_generate_graphics_states_the_rubrics_own_numbers() -> None:
    text = load_text("tracks/generate_graphics.j2")
    rubric = load_rubric("shader_v2")
    caps = {c.id: c.cap for c in rubric.caps}
    penalties = {d.id: d.penalty for d in rubric.defects}
    depth = next(c for c in rubric.criteria if c.id == "depth_and_detail")
    assert f"`static`, cap {caps['static_frames']:g}" in text
    assert f"`flicker`, cap {caps['flicker']:g}" in text
    assert f"`stretched_aspect`, −{penalties['stretched_aspect']:.2f}" in text
    assert f"`flat_fill` defect (−{penalties['flat_fill']:.2f})" in text
    assert f"depth_and_detail ({depth.weight:.2f})" in text
    # the anchor the prompt names is the one that describes "one field fills the frame"
    assert "0.1 anchor" in text and "field" in depth.anchors["0.1"]
