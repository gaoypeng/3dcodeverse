"""A zone that wraps its content in one group has still placed it: planned names are looked up under the row."""

from __future__ import annotations

from codeverse3d.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZonePlan
from codeverse3d.spatial.scene_placement import contract_findings


def _plan(contents: list[str]) -> ScenePlan:
    bb = BBox(center=(0, 0, 0), extents=(30, 20, 30))
    return ScenePlan(title="t", summary="s", setting="sky", mood="calm", bounds=bb, environment="clear",
                     zones=[ZonePlan(name="WindmillIsland", description="d", bbox=bb, contents=contents)],
                     assets=[AssetPlan(name=c, description=f"a {c}", kind="threejs", approx_size_m=(1, 1, 1))
                             for c in contents],
                     cameras=[CameraPlan(name="overview", position=(1, 1, 1), look_at=(0, 0, 0), fov=50, purpose="p")],
                     animation=[], effects=[])


def _census(row: dict) -> dict:
    return {"placement": {"assets": [row], "total": 1, "checked": 1}}


WRAPPED = {"name": "IslandAssembly", "zone": "WindmillIsland",
           "inner": ["Windmill", "FloatingRock_1", "FloatingRock_2", "SkyPine_1"]}


def _missing(findings) -> list[str]:
    return [m for f in findings if f.data.get("kind") == "missing_content" for m in f.data.get("missing", [])]


def test_content_one_group_down_is_found():
    out = contract_findings(_census(WRAPPED), _plan(["Windmill", "FloatingRock", "SkyPine"]))
    assert _missing(out) == [], "the zone built and named all three; only the shape differed"


def test_a_subtree_that_carries_the_name_nowhere_still_reads_as_missing():
    """The rule is "somewhere under this row", not "anywhere in the scene"."""
    row = {"name": "Trees", "zone": "WindmillIsland", "inner": ["PineSway_0", "PineSway_1"]}
    out = contract_findings(_census(row), _plan(["SnowyPineTree"]))
    assert _missing(out) == ["SnowyPineTree"]
    hint = " ".join(f.fix_hint for f in out if f.data.get("kind") == "missing_content")
    assert "give the object the plan's name" in hint


def test_an_unavailable_hero_is_not_missing_content():
    """A hero the asset stage could not build was withheld from the zone: its absence is not an ERROR."""
    plan = {"zones": [{"name": "IncenseTerrace", "contents": ["BronzeCenser", "StoneBench"]}], "assets": []}
    census = {"placement": {"assets": [{"name": "StoneBench", "zone": "IncenseTerrace", "inner": []}]}}
    missing = [f for f in contract_findings(census, plan) if f.data.get("kind") == "missing_content"]
    assert missing and "BronzeCenser" in missing[0].message
    assert not [f for f in contract_findings(census, plan, unavailable=["BronzeCenser"]) if f.data.get("kind") == "missing_content"]
