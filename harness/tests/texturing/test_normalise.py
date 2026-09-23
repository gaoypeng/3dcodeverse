"""normalise.py: which materials the post-build normaliser touches, and what it writes."""

from __future__ import annotations

from pathlib import Path

import trimesh
from PIL import Image

from codeverse3d.spatial.measure import load_scene, measure_glb
from codeverse3d.texturing.apply import classify, normalise_materials


def _pbr(name: str, metallic, roughness, colour=(120, 120, 120, 255), **kw):
    return trimesh.visual.material.PBRMaterial(
        name=name, baseColorFactor=list(colour), metallicFactor=metallic, roughnessFactor=roughness, **kw)


def _glb(tmp_path: Path, rows) -> Path:
    """rows: (node, material name, metallic, roughness, colour)."""
    scene = trimesh.Scene()
    for i, (node, mat_name, m, r, colour) in enumerate(rows):
        box = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
        box.apply_translation((i * 0.2, 0, 0))
        box.visual = trimesh.visual.TextureVisuals(
            uv=[[0.0, 0.0]] * len(box.vertices), material=_pbr(mat_name, m, r, colour))
        scene.add_geometry(box, node_name=node, geom_name=node)
    out = tmp_path / "in.glb"
    scene.export(out)
    return out


# --------------------------------------------------------------------------- classify


def test_plan_prose_may_only_fix_a_never_configured_default():
    """The plan's prose may spot an untouched default, never overrule typed numbers."""
    assert classify("lid__0", "SpaceGray", "glass display panel in an aluminium lid", 0.88, 0.30) is None
    v = classify("lid__0", "SpaceGray", "glass display panel", 0.0, 1.0)
    assert v is not None and v.reason == "untouched" and v.family == "glass"


def test_a_saturated_colour_turns_bare_iron_into_paint():
    v = classify("PumpBody", "DarkGreenCastIron", "", 0.15, 0.65, (0.10, 0.35, 0.18))
    assert v is None or v.family in ("painted_metal", "painted_wood")
    grey = classify("CastBase", "CastIronDark", "", 0.20, 0.65, (0.12, 0.12, 0.13))
    assert grey is not None and grey.family == "cast_iron" and grey.metallic == 0.5


# --------------------------------------------------------------------------- the pass
def test_normalise_materials_rewrites_only_the_factors(tmp_path: Path):
    glb = _glb(tmp_path, [
        ("Seat", "OakSeat", 0.0, 0.5, (150, 100, 60, 255)),          # untouched default -> hardwood
        ("Rail", "ChromeRail", 0.20, 0.10, (210, 215, 220, 255)),    # impossible metallic -> clamp
        ("Bit", "PolishedSteelBit", 0.98, 0.15, (215, 215, 224, 255)),  # fine
        ("Widget", "Mat.001", 0.30, 0.40, (100, 100, 100, 255)),     # unknown
    ])
    out = tmp_path / "out.glb"
    rep = normalise_materials(glb, out, plan=None)
    assert out.is_file() and rep.warnings == []
    changed = {c.node: c for c in rep.changes}
    assert set(changed) == {"Seat", "Rail"}
    assert changed["Seat"].family == "hardwood" and changed["Seat"].roughness == (0.5, 0.45)
    assert changed["Rail"].family == "chrome" and changed["Rail"].metallic == (0.2, 0.7)
    assert rep.n_materials == 4 and rep.n_unchanged == 2
    assert "hardwood" in rep.table() and "chrome" in rep.table()
    # nothing but the two floats moved
    a, b = measure_glb(glb), measure_glb(out)
    assert {p.name for p in a.parts} == {p.name for p in b.parts} and a.tri_count == b.tri_count
    after = {g: m.baseColorFactor.tolist() for g, mesh in load_scene(out).geometry.items()
             for m in [mesh.visual.material]}
    before = {g: m.baseColorFactor.tolist() for g, mesh in load_scene(glb).geometry.items()
              for m in [mesh.visual.material]}
    assert after == before, "base colour is the agent's decision and must survive"


def test_nothing_to_do_writes_nothing(tmp_path: Path):
    glb = _glb(tmp_path, [("Bit", "PolishedSteelBit", 0.98, 0.15, (215, 215, 224, 255))])
    out = tmp_path / "out.glb"
    rep = normalise_materials(glb, out)
    assert not rep.changed() and rep.glb_out == "" and not out.exists()
    assert rep.table() == "no material needed normalising"


def test_a_textured_material_is_never_second_guessed(tmp_path: Path):
    glb = _glb(tmp_path, [("Seat", "OakSeat", 0.0, 0.5, (150, 100, 60, 255))])
    scene = load_scene(glb)
    scene.geometry["Seat"].visual.material.baseColorTexture = Image.new("RGB", (8, 8), (120, 90, 40))
    textured = tmp_path / "textured.glb"
    scene.export(textured)
    rep = normalise_materials(textured, tmp_path / "out.glb")
    assert not rep.changed() and rep.n_unchanged == 1
