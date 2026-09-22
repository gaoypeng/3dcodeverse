"""apply.py: textured GLB keeps names/geometry, shares images, skips parts, reloads."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from codeverse3d.spatial.measure import measure_glb
from codeverse3d.texturing.apply import apply_textures, node_part_lookup
from codeverse3d.texturing.generate import generate_textures
from codeverse3d.texturing.plan import default_plan
from tests.texturing.conftest import FakeImageModel


def test_node_part_lookup_instances_and_links(chair_plan):
    tp = default_plan(chair_plan)
    lk = node_part_lookup(["Seat", "Leg_0", "Leg_3", "Knob", "Back__1", "Mystery"], tp.by_part())
    assert lk["Leg_0"].part == "Leg" and lk["Leg_3"].part == "Leg" and lk["Back__1"].part == "Back"
    assert lk["Mystery"] is None


def test_apply_textures_end_to_end(tmp_path: Path, chair_glb: Path, chair_plan):
    tp = default_plan(chair_plan)
    ts = generate_textures(tp, tmp_path / "tex", FakeImageModel(), size=64, cache_dir=tmp_path / "c")
    out = tmp_path / "object_textured.glb"
    rep = apply_textures(chair_glb, tp, ts.paths(), out)
    assert out.is_file() and rep.warnings == []
    assert sorted(rep.parts_textured) == ["Back", "Leg_0", "Leg_1", "Leg_2", "Leg_3", "Seat"]
    assert rep.parts_skipped == ["Knob"] and rep.parts_unmatched == []
    assert rep.projections["Leg_0"] == "cylinder" and rep.projections["Seat"] == "box"
    assert rep.n_materials == 2
    # names + geometry preserved; measurement unchanged
    a, b = measure_glb(chair_glb), measure_glb(out)
    assert {p.name for p in a.parts} == {p.name for p in b.parts}
    assert np.allclose(a.extents, b.extents, atol=1e-6) and a.tri_count == b.tri_count
    # skipped part keeps its original (untextured) visual
    s = trimesh.load(out, force="scene", process=False)
    knob = s.geometry["Knob"]
    assert getattr(getattr(knob.visual, "material", None), "baseColorTexture", None) is None
    # UVs present and per-vertex on textured parts
    seat = s.geometry["Seat"]
    assert seat.visual.uv is not None and len(seat.visual.uv) == len(seat.vertices)


def test_apply_with_missing_texture_file_warns(tmp_path: Path, chair_glb: Path, chair_plan):
    tp = default_plan(chair_plan)
    rep = apply_textures(chair_glb, tp, {tp.texture_ids()[0]: tmp_path / "missing.png"}, tmp_path / "o.glb")
    assert any("missing" in w for w in rep.warnings)
    assert rep.parts_textured == []
