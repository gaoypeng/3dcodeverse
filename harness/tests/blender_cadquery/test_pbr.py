"""materials.py: the family table and the keyword resolver."""

from __future__ import annotations

import pytest

from codeverse3d.languages.blender._pbr import COARSE_TO_FINE, MATERIALS, family_for, pbr_for


def test_coarse_families_all_resolve():
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
    ("weathered copper roof", "copper_patina"),
    ("BlondeBasswood", "hardwood"),              # -wood suffix
    ("stainless steel drum", "brushed_metal"),
])
def test_family_for_resolves_substrate_then_finish(text, family):
    hit = family_for(text)
    assert hit is not None and hit.family == family, f"{text} -> {hit}"


def test_family_for_prefers_the_first_text_that_resolves():
    """The material's own name outranks the plan's prose."""
    assert family_for("SteamWandChrome", "SteamWand", "chrome wand with a silicone tip").family == "chrome"
    assert family_for("Unnamed", "SteamWand", "silicone tip").family == "rubber"
    assert family_for("", "", "") is None
    assert family_for("Widget42") is None
