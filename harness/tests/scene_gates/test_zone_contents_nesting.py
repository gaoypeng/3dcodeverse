"""A zone that wraps its content in one group has still placed it.

Measured 2026-09-05 on `bench/out/scene_fixed` (floating_islands): three zones were
reported "missing planned contents: FloatingRock, Windmill, SkyPine" while the zone
module built each one and named it exactly that — `windmill.name = 'Windmill'`,
`rock.name = 'FloatingRock_1'` — and added them all to a group called `IslandAssembly`.
The placement table lists direct children of a zone, so the check saw one row named
`IslandAssembly` and called the plan unmet.

**All five** of arm B's remaining `scene_placement` ERRORs were this family, including the
two that looked like the model renaming things.  `PineSway_0` and `LanternPost1` are the
names the ZONE gave the placed objects, but each contains the asset module's own root one
level further down — `inner` reads `['SnowyPineTree', 'Trunk', 'Boughs', …]` and
`['HutPorchLantern']`.  Re-probing all three recorded workspaces takes the contract from
5 ERRORs to **0**.

The last test below still pins the rule for a subtree that genuinely does not carry the
planned name; that shape did not occur in this corpus.
"""

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


def test_content_that_really_is_absent_is_still_reported():
    out = contract_findings(_census(WRAPPED), _plan(["Windmill", "RopeBridge"]))
    assert _missing(out) == ["RopeBridge"]


def test_a_subtree_that_carries_the_name_nowhere_still_reads_as_missing():
    """The rule is "somewhere under this row", not "anywhere in the scene": a zone whose
    whole subtree never mentions the planned content is still missing it."""
    row = {"name": "Trees", "zone": "WindmillIsland", "inner": ["PineSway_0", "PineSway_1"]}
    out = contract_findings(_census(row), _plan(["SnowyPineTree"]))
    assert _missing(out) == ["SnowyPineTree"]
    hint = " ".join(f.fix_hint for f in out if f.data.get("kind") == "missing_content")
    assert "give the object the plan's name" in hint


def test_a_row_with_no_inner_list_behaves_as_before():
    row = {"name": "Windmill", "zone": "WindmillIsland"}
    assert _missing(contract_findings(_census(row), _plan(["Windmill"]))) == []
    assert _missing(contract_findings(_census(row), _plan(["SkyPine"]))) == ["SkyPine"]


def test_an_unavailable_hero_is_not_missing_content():
    """A hero the asset stage could not build was withheld from the zone ("NOT AVAILABLE —
    do not reference"); the contract check must not ERROR every round on its absence."""
    from codeverse3d.spatial.scene_placement import contract_findings

    plan = {"zones": [{"name": "IncenseTerrace", "contents": ["BronzeCenser", "StoneBench"]}], "assets": []}
    census = {"placement": {"assets": [{"name": "StoneBench", "zone": "IncenseTerrace", "inner": []}]}}
    missing = [f for f in contract_findings(census, plan) if f.data.get("kind") == "missing_content"]
    assert missing and "BronzeCenser" in missing[0].message
    assert not [f for f in contract_findings(census, plan, unavailable=["BronzeCenser"]) if f.data.get("kind") == "missing_content"]


def test_a_zone_whose_only_content_was_never_built_is_not_empty():
    """zone_empty follows the same rule as missing_content: a zone told its only planned hero
    is NOT AVAILABLE placed nothing because it was told to."""
    from codeverse3d.spatial.scene_placement import contract_findings

    plan = {"zones": [{"name": "IncenseTerrace", "contents": ["BronzeCenser"]},
                      {"name": "Courtyard", "contents": ["StoneBench"]}], "assets": []}
    census = {"placement": {"assets": [{"name": "StoneBench", "zone": "Courtyard", "inner": []}]}}
    empty = [f.target for f in contract_findings(census, plan) if f.data.get("kind") == "zone_empty"]
    assert empty == ["IncenseTerrace"]
    assert not [f for f in contract_findings(census, plan, unavailable=["BronzeCenser"]) if f.data.get("kind") == "zone_empty"]
