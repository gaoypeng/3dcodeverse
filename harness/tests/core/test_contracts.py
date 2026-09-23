"""Contract + convention tests (the spine)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from codeverse3d.contracts.common import Budget
from codeverse3d.contracts.plan import (
    ArticulatedPlan,
    AssetPlan,
    BBox,
    CameraPlan,
    JointPlan,
    PartPlan,
    ScenePlan,
    StaticPlan,
    ZonePlan,
)
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import (
    OBJECT_CLAY_VIEWS,
    OBJECT_VIEWS,
    OBJECT_VIEWS_QUICK,
    slugify,
)


def _box(cx=0.0, cy=0.0, cz=0.5, ex=1.0, ey=1.0, ez=1.0) -> BBox:
    return BBox(center=(cx, cy, cz), extents=(ex, ey, ez))


def test_a_prompt_slugifies_to_a_run_name():
    assert slugify("A mid-century wooden dining chair!") == "a_mid_century_wooden_dining_chair"


def test_view_presets_unique():
    names = [v.name for v in OBJECT_VIEWS]
    assert len(names) == len(set(names)) == 14
    # the 14-view rig (D47): quick subset by name, poles present, underside reachable
    assert {v.name for v in OBJECT_VIEWS_QUICK} <= set(names) and len(OBJECT_VIEWS_QUICK) == 4
    assert "bottom" in names and "top" in names
    assert min(v.elevation_deg for v in OBJECT_VIEWS) == -90.0
    assert max(v.elevation_deg for v in OBJECT_VIEWS) == 90.0
    # the clay rig's 'top' (el 88) shares the rig's name at another camera: never union by name
    assert len(OBJECT_CLAY_VIEWS) == 4
    clay_top = next(v for v in OBJECT_CLAY_VIEWS if v.name == "top")
    assert clay_top.elevation_deg == 88.0


def test_static_plan_validates_attach_and_duplicates():
    with pytest.raises(ValidationError):
        StaticPlan(object_name="o", summary="s", overall_bbox=_box(),
                   parts=[PartPlan(name="Seat", role="r", description="d", bbox=_box()),
                          PartPlan(name="seat", role="r", description="d", bbox=_box())])
    with pytest.raises(ValidationError):
        StaticPlan(object_name="o", summary="s", overall_bbox=_box(),
                   parts=[PartPlan(name="Seat", role="r", description="d", bbox=_box(), attach_to="Ghost")])


def test_articulated_plan_requires_single_root_tree_and_sane_joints():
    parts = [PartPlan(name="Base", role="r", description="d", bbox=_box()),
             PartPlan(name="Door", role="r", description="d", bbox=_box(0.4, -0.5)),
             PartPlan(name="Handle", role="r", description="d", bbox=_box(0.45, -0.55, 0.5, 0.05, 0.05, 0.1))]
    good = ArticulatedPlan(object_name="cab", summary="s", overall_bbox=_box(), parts=parts, root_link="Base",
                           joints=[JointPlan(name="hinge", type="revolute", parent="Base", child="Door", axis=(0, 0, 2),
                                             pivot=(-0.5, -0.5, 0.5), lower=0, upper=1.5, rest=0.3),
                                   JointPlan(name="h2", type="fixed", parent="Door", child="Handle", axis=(0, 0, 1),
                                             pivot=(0, 0, 0))])
    assert math.isclose(sum(a * a for a in good.joints[0].axis), 1.0, abs_tol=1e-6)  # axis normalised
    with pytest.raises(ValidationError):  # Handle not connected
        ArticulatedPlan(object_name="cab", summary="s", overall_bbox=_box(), parts=parts, root_link="Base",
                        joints=[good.joints[0]])
    with pytest.raises(ValidationError):  # rest outside limits
        JointPlan(name="j", type="revolute", parent="A", child="B", axis=(0, 0, 1), pivot=(0, 0, 0), lower=0, upper=1, rest=2)


def test_scene_plan_zone_contents_must_be_assets():
    z = ZonePlan(name="Pond", description="d", bbox=_box(), contents=["Lantern"])
    cam = CameraPlan(name="c", position=(0, 1.6, 5), look_at=(0, 0, 0))
    with pytest.raises(ValidationError):
        ScenePlan(title="t", summary="s", setting="x", bounds=_box(), environment="e", zones=[z], cameras=[cam])
    ok = ScenePlan(title="t", summary="s", setting="x", bounds=_box(), environment="e", zones=[z], cameras=[cam],
                   assets=[AssetPlan(name="Lantern", kind="threejs", description="d", approx_size_m=(0.3, 0.3, 1.2))])
    assert ok.zones[0].contents == ["Lantern"]


def test_camera_plan_name_must_be_filename_safe():
    """Camera names become render filenames (render_scene.mjs) — reject path tricks."""
    ok = CameraPlan(name="pier_low-2", position=(0, 1.6, 5), look_at=(0, 0, 0))
    assert ok.name == "pier_low-2"
    for bad in ("x/../y", "/absolute", "a\\b", "a b", "a.png", "", "x" * 200):
        with pytest.raises(ValidationError):
            CameraPlan(name=bad, position=(0, 1.6, 5), look_at=(0, 0, 0))


def test_budget_is_strict_but_recorded_specs_migrate_the_retired_cost_key():
    with pytest.raises(ValidationError):
        Budget(max_usd=1.0)
    with pytest.raises(ValidationError):
        Budget(max_minuts=5)
    import json

    data = {"id": "x", "track": "static_object", "language": "blender", "prompt": "p",
            "budget": {"max_rounds": 2, "max_minutes": 30.0, "max_usd": 2.5}}
    s = Spec.model_validate(data)
    assert s.budget.max_rounds == 2 and not hasattr(s.budget, "max_usd")
    assert Spec.model_validate_json(json.dumps(data)).budget.max_minutes == 30.0
    assert data["budget"]["max_usd"] == 2.5, "migration must not mutate the caller's dict"
