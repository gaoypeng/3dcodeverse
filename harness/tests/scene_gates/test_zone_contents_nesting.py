"""A zone that wraps its content in one group has still placed it.

Measured 2026-09-05 on `bench/out/scene_fixed` (floating_islands): three zones were
reported "missing planned contents: FloatingRock, Windmill, SkyPine" while the zone
module built each one and named it exactly that — `windmill.name = 'Windmill'`,
`rock.name = 'FloatingRock_1'` — and added them all to a group called `IslandAssembly`.
The placement table lists direct children of a zone, so the check saw one row named
`IslandAssembly` and called the plan unmet.

All five of arm B's remaining `scene_placement` ERRORs were this family; the other two are
the model naming the object after its behaviour (`PineSway_0..11` for a SnowyPineTree,
`LanternPost1/2` for a HutPorchLantern).  Those stay ERRORs — naming an object after the
plan's content IS part of the contract, because that is how every gate and the judge find
it — but the hint now says so.
"""

from __future__ import annotations

from codeverse.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZonePlan
from codeverse.spatial.scene_placement import contract_findings


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


def test_an_object_named_after_its_behaviour_still_reads_as_missing():
    """`buildSnowyPineTree` was called and the objects were named `PineSway_N`.  The tree is
    there, and the gate still says so — naming it for the plan is what makes it findable."""
    row = {"name": "Trees", "zone": "WindmillIsland", "inner": ["PineSway_0", "PineSway_1"]}
    out = contract_findings(_census(row), _plan(["SnowyPineTree"]))
    assert _missing(out) == ["SnowyPineTree"]
    hint = " ".join(f.fix_hint for f in out if f.data.get("kind") == "missing_content")
    assert "give the object the plan's name" in hint


def test_a_row_with_no_inner_list_behaves_as_before():
    row = {"name": "Windmill", "zone": "WindmillIsland"}
    assert _missing(contract_findings(_census(row), _plan(["Windmill"]))) == []
    assert _missing(contract_findings(_census(row), _plan(["SkyPine"]))) == ["SkyPine"]
