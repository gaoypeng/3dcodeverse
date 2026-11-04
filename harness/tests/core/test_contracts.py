"""Contract + convention tests (the spine)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from codeverse3d.contracts.plan import (
    ArticulatedPlan,
    BBox,
    CameraPlan,
    JointPlan,
    PartPlan,
)


def _box(cx=0.0, cy=0.0, cz=0.5, ex=1.0, ey=1.0, ez=1.0) -> BBox:
    return BBox(center=(cx, cy, cz), extents=(ex, ey, ez))


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


def test_camera_plan_name_must_be_filename_safe():
    """Camera names become render filenames (render_scene.mjs) — reject path tricks."""
    ok = CameraPlan(name="pier_low-2", position=(0, 1.6, 5), look_at=(0, 0, 0))
    assert ok.name == "pier_low-2"
    for bad in ("x/../y", "/absolute", "a\\b", "a b", "a.png", "", "x" * 200):
        with pytest.raises(ValidationError):
            CameraPlan(name=bad, position=(0, 1.6, 5), look_at=(0, 0, 0))
    # the scene host's other rules (audit 2026-09-24 N10): a plan camera it would flag fails here
    for bad in ({"fov": 170}, {"fov": 2}, {"look_at": (0, 1.6, 5)}):
        with pytest.raises(ValidationError):
            CameraPlan(**{"name": "cam", "position": (0, 1.6, 5), "look_at": (0, 0, 0), **bad})
