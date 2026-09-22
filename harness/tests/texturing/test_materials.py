"""materials.py: the family table, the keyword resolver and the plausibility bands."""

from __future__ import annotations

import pytest

from codeverse3d.texturing.materials import (
    BARE_METAL_FAMILIES,
    COARSE_TO_FINE,
    MATERIALS,
    family_for,
    is_framework_default,
    is_warm_metal_hue,
    looks_painted,
    out_of_band,
    pbr_for,
    saturation,
)
from codeverse3d.texturing.plan import FAMILY_DEFAULTS


def test_every_family_is_self_consistent():
    for name, p in MATERIALS.items():
        assert p.family == name
        lo, hi = p.roughness_band
        assert lo <= p.roughness <= hi, f"{name}: canonical roughness outside its own band"
        lo, hi = p.metallic_band
        assert lo <= p.metallic <= hi, f"{name}: canonical metallic outside its own band"
        assert 1.0 <= p.ior <= 2.5
        assert p.note, f"{name}: a row without a note teaches nothing"


def test_coarse_families_of_the_texture_planner_all_resolve():
    """plan.py speaks 11 coarse families; every one must map to a row here, or the
    texture pass and the normaliser would disagree about the same surface."""
    assert set(COARSE_TO_FINE) == set(FAMILY_DEFAULTS)
    for coarse, fine in COARSE_TO_FINE.items():
        assert fine in MATERIALS, coarse
        assert pbr_for(coarse) is MATERIALS[fine]


@pytest.mark.parametrize(("text", "family"), [
    ("CastIronMat", "cast_iron"),
    ("PolishedChrome", "chrome"),
    ("VarnishedBeechBody", "hardwood"),          # substrate beats finish
    ("satin white lacquer", "brushed_metal"),    # finish only
    ("powder-coated steel frame", "painted_metal"),
    ("painted pine shed wall", "painted_wood"),
    ("clear tempered glass", "glass"),
    ("weathered copper roof", "copper_patina"),
    ("BlondeBasswood", "hardwood"),              # -wood suffix
    ("glazed porcelain", "ceramic"),
    ("BlackRubberMat", "rubber"),
    ("stainless steel drum", "brushed_metal"),
])
def test_family_for_resolves_substrate_then_finish(text, family):
    hit = family_for(text)
    assert hit is not None and hit.family == family, f"{text} -> {hit}"


def test_family_for_prefers_the_first_text_that_resolves():
    """The material's own name outranks the plan's prose: a chrome steam wand must
    not become silicone because the plan sentence mentions a silicone tip."""
    assert family_for("SteamWandChrome", "SteamWand", "chrome wand with a silicone tip").family == "chrome"
    assert family_for("Unnamed", "SteamWand", "silicone tip").family == "rubber"
    assert family_for("", "", "") is None
    assert family_for("Widget42") is None


def test_framework_defaults_are_the_untouched_pairs():
    assert is_framework_default(None, None)        # glTF omits 1.0/1.0
    assert is_framework_default(1.0, 1.0)
    assert is_framework_default(0.0, 1.0)          # three MeshStandardMaterial
    assert is_framework_default(0.0, 0.5)          # Blender Principled
    assert not is_framework_default(0.0, 0.45)
    assert not is_framework_default(0.95, 0.15)


def test_out_of_band_names_the_offending_factor():
    chrome = MATERIALS["chrome"]
    assert out_of_band(chrome, 0.95, 0.10) == []
    assert out_of_band(chrome, 0.2, 0.10) == ["metallic"]
    assert out_of_band(chrome, 0.95, 0.8) == ["roughness"]
    assert out_of_band(MATERIALS["hardwood"], 0.9, 0.02) == ["metallic", "roughness"]


def test_saturated_colour_means_paint_but_gold_still_means_metal():
    assert saturation((0.5, 0.5, 0.5)) == 0.0
    assert looks_painted("cast_iron", (0.10, 0.35, 0.18))     # dark green pump = painted iron
    assert looks_painted("machined_steel", (0.85, 0.05, 0.05))
    assert not looks_painted("cast_iron", (0.12, 0.12, 0.13))  # neutral: really is iron
    assert not looks_painted("machined_steel", (0.85, 0.65, 0.28))  # titanium nitride is gold
    assert not looks_painted("hardwood", (0.10, 0.35, 0.18))   # rule only applies to bare metals
    assert is_warm_metal_hue((0.72, 0.40, 0.25)) and not is_warm_metal_hue((0.8, 0.1, 0.1))
    assert set(MATERIALS) >= BARE_METAL_FAMILIES
