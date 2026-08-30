"""L2 zone layouts: validator complaints, prompt table, parallel calls with one re-ask."""

from __future__ import annotations

from types import SimpleNamespace

from codeverse.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZoneLayout, ZonePlan
from codeverse.tracks.zone_layout import layout_block, layout_zones, validate_layout


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


def test_layout_block_renders_numbers_the_builder_can_follow():
    text = layout_block(_layout(path_points=[(-18.0, 0.0), (-2.0, 4.0)], mid_props=12, ground_cover=400,
                                notes="boats face the quay"))
    assert "6x Bollard around (-12.0, 3.0) spread 4.0 m" in text
    assert "path polyline: (-18.0, 0.0) -> (-2.0, 4.0)" in text
    assert "12 mid props" in text and "400 ground-cover" in text and "boats face the quay" in text
    assert layout_block(None) == ""


class _Model:
    """Scripted planner: first answer per zone from the queue, then valid ones."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def generate(self, req):
        self.calls += 1
        return SimpleNamespace(parsed=self.answers.pop(0), text="", usage=None)


def test_layout_zones_runs_per_zone_and_reasks_once_with_the_complaint():
    plan = _plan()
    good_quay = _layout().model_dump(mode="json")
    bad_quay = _layout(placements=[{"asset": "Kraken", "count": 1, "cluster": (0.0, 0.0), "spread_m": 1.0}]).model_dump(mode="json")
    good_market = {"zone": "Market", "placements": [{"asset": "Stall", "count": 4, "cluster": (10.0, 0.0), "spread_m": 5.0}]}
    # zones fan out with max_workers=1 so the scripted queue stays ordered
    model = _Model([bad_quay, good_quay, good_market])
    out = layout_zones(plan, model, max_workers=1)
    assert set(out) == {"Quay", "Market"} and model.calls == 3
    assert out["Quay"].placements[0].asset == "Bollard"


def test_layout_zones_drops_a_twice_rejected_zone_instead_of_dying():
    plan = _plan()
    bad = {"zone": "Quay", "placements": [{"asset": "Kraken", "count": 1, "cluster": (0.0, 0.0), "spread_m": 1.0}]}
    good_market = {"zone": "Market", "placements": [{"asset": "Stall", "count": 4, "cluster": (10.0, 0.0), "spread_m": 5.0}]}
    out = layout_zones(plan, _Model([bad, bad, good_market]), max_workers=1)
    assert set(out) == {"Market"}
