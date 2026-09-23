"""L2 zone layouts: validator complaints, prompt table, parallel calls with one re-ask."""

from __future__ import annotations

from codeverse3d.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZoneLayout, ZonePlan
from codeverse3d.tracks.zone_layout import layout_block, layout_zones, validate_layout
from tests.orchestrator_tracks.fakes import FakeChatModel


def _plan() -> ScenePlan:
    return ScenePlan(
        title="t", summary="s", setting="harbour, dusk", mood="calm",
        bounds=BBox(center=(0, 0, 0), extents=(60, 10, 60)), environment="sky 0x223250",
        zones=[ZonePlan(name="Quay", description="12 mid props", bbox=BBox(center=(-10, 0, 0), extents=(20, 8, 20)),
                        contents=["Bollard", "FishingBoat"]),
               ZonePlan(name="Market", description="stalls", bbox=BBox(center=(10, 0, 0), extents=(20, 8, 20)),
                        contents=["Stall"])],
        assets=[AssetPlan(name="Bollard", kind="threejs", description="d", approx_size_m=(0.3, 0.8, 0.3), instances_hint=6),
                AssetPlan(name="FishingBoat", kind="threejs", description="d", approx_size_m=(6, 2.5, 2), instances_hint=2),
                AssetPlan(name="Stall", kind="threejs", description="d", approx_size_m=(3, 2.5, 2), instances_hint=4)],
        cameras=[CameraPlan(name="overview", position=(1, 1, 1), look_at=(0, 0, 0), fov=50, purpose="p")])


def _layout(**over) -> ZoneLayout:
    base = {"zone": "Quay",
            "placements": [{"asset": "Bollard", "count": 6, "cluster": (-12.0, 3.0), "spread_m": 4.0},
                           {"asset": "FishingBoat", "count": 2, "cluster": (-8.0, -5.0), "spread_m": 2.0}]}
    base.update(over)
    return ZoneLayout.model_validate(base)


def test_validator_accepts_a_buildable_layout_and_names_every_problem():
    plan = _plan()
    assert validate_layout(_layout(), plan.zones[0], plan) == ""
    bad = _layout(placements=[{"asset": "Kraken", "count": 1, "cluster": (-12.0, 3.0), "spread_m": 1.0},
                              {"asset": "Bollard", "count": 99, "cluster": (55.0, 3.0), "spread_m": 1.0}])
    complaint = validate_layout(bad, plan.zones[0], plan)
    assert "unknown asset 'Kraken'" in complaint
    assert "outside zone Quay bbox" in complaint
    assert "count 99" in complaint
    assert "no placement for planned contents: FishingBoat" in complaint


def test_an_aerial_camera_does_not_reject_the_placements_beneath_it():
    """A lens above the cluster top is clear; a lens at stall height above it is still rejected."""
    plan = _plan()
    plan.cameras = [CameraPlan(name="aerial", position=(10, 8, 0), look_at=(10, 0, 0), fov=50, purpose="p")]
    under = _layout(placements=[{"asset": "Stall", "count": 4, "cluster": (10.0, 0.0), "spread_m": 3.0}],
                    zone="Market")
    assert validate_layout(under, plan.zones[1], plan) == ""
    plan.cameras = [CameraPlan(name="low", position=(10, 2.0, 0), look_at=(10, 0, 0), fov=50, purpose="p")]
    complaint = validate_layout(under, plan.zones[1], plan)
    assert "camera low" in complaint, "a lens at stall height above the cluster is still inside its reach"


def test_validator_rejects_two_large_assets_on_one_spot_but_spares_adjacency():
    """Narrow rule: small footprints and declared support relations pass untouched."""
    plan = _plan()
    stacked = _layout(placements=[
        {"asset": "FishingBoat", "count": 1, "cluster": (-12.0, 3.0), "spread_m": 0.0},
        {"asset": "Stall", "count": 1, "cluster": (-12.2, 3.1), "spread_m": 0.0}])
    # Stall is not Quay content, but the stacking rule should still name the pair
    complaint = validate_layout(stacked, plan.zones[0], plan)
    assert "share one spot" in complaint and "FishingBoat" in complaint and "Stall" in complaint
    # small footprint: Bollard (0.3 m) right next to the boat is legitimate adjacency
    adjacent = _layout(placements=[
        {"asset": "FishingBoat", "count": 1, "cluster": (-12.0, 3.0), "spread_m": 0.0},
        {"asset": "Bollard", "count": 6, "cluster": (-12.1, 3.2), "spread_m": 0.5}])
    assert "share one spot" not in validate_layout(adjacent, plan.zones[0], plan)
    # support relation: a Stall standing ON the boat (declared) passes
    supported = _layout(placements=[
        {"asset": "FishingBoat", "count": 1, "cluster": (-12.0, 3.0), "spread_m": 0.0},
        {"asset": "Stall", "count": 1, "cluster": (-12.1, 3.1), "spread_m": 0.0, "support": "FishingBoat"}])
    assert "share one spot" not in validate_layout(supported, plan.zones[0], plan)


def test_layout_block_renders_numbers_the_builder_can_follow():
    text = layout_block(_layout(path_points=[(-18.0, 0.0), (-2.0, 4.0)], mid_props=12, ground_cover=400,
                                notes="boats face the quay"))
    assert "6x Bollard around (-12.0, 3.0) spread 4.0 m" in text
    assert "path polyline: (-18.0, 0.0) -> (-2.0, 4.0)" in text
    assert "12 mid props" in text and "400 ground-cover" in text and "boats face the quay" in text
    assert layout_block(None) == ""


def test_layout_zones_drops_a_twice_rejected_zone_and_buys_nothing_past_the_ceiling():
    plan = _plan()
    bad = {"zone": "Quay", "placements": [{"asset": "Kraken", "count": 1, "cluster": (0.0, 0.0), "spread_m": 1.0}]}
    good_market = {"zone": "Market", "placements": [{"asset": "Stall", "count": 4, "cluster": (10.0, 0.0), "spread_m": 5.0}]}
    out = layout_zones(plan, FakeChatModel([bad, bad, good_market]), max_workers=1)
    assert set(out) == {"Market"}
    # a run past its ceiling buys no layout calls
    import time

    from codeverse3d.contracts.common import Budget
    from codeverse3d.orchestrator import BudgetGuard

    model = FakeChatModel()   # any call finds no reply and raises
    guard = BudgetGuard(Budget(max_minutes=30), start_time=time.time() - 45 * 60)
    out = layout_zones(plan, model, budget=guard, max_workers=1)
    assert out == {} and len(model.requests) == 0
