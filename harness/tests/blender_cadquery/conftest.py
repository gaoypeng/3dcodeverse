"""Fixtures for the blender / cadquery language packages."""

from __future__ import annotations

import shutil

import pytest

from codeverse3d.config import get_settings
from codeverse3d.contracts.plan import AcceptanceItem, BBox, PartPlan, StaticPlan


@pytest.fixture
def table_plan() -> StaticPlan:
    """3-part plan (one part with 4 instances) used by skeleton/build tests."""
    return StaticPlan(
        object_name="SideTable",
        summary="A small oak side table with four legs and a lower shelf.",
        overall_bbox=BBox(center=(0, 0, 0.3), extents=(0.5, 0.5, 0.6)),
        style_notes="mid-century",
        parts=[
            PartPlan(name="TableTop", role="top surface", description="rounded-corner slab",
                     bbox=BBox(center=(0, 0, 0.58), extents=(0.5, 0.5, 0.04)), material="oak wood"),
            PartPlan(name="Leg", role="support", description="tapered square leg",
                     bbox=BBox(center=(0.21, 0.21, 0.28), extents=(0.04, 0.04, 0.56)), material="oak",
                     attach_to="TableTop", symmetry="mirror_x", instances=4),
            PartPlan(name="Shelf", role="lower shelf", description="thin slab",
                     bbox=BBox(center=(0, 0, 0.15), extents=(0.42, 0.42, 0.02)), material="painted steel",
                     attach_to="Leg"),
        ],
        acceptance=[AcceptanceItem(id="a1", text="overall height 0.60 m", how="measure")],
    )


@pytest.fixture
def blender_bin() -> str:
    b = get_settings().resolve_blender()
    if not b or not shutil.which(b) and not b.startswith("/"):
        pytest.skip("no Blender binary")
    return b


def has_cadquery() -> bool:
    try:
        import cadquery  # noqa: F401
    except ImportError:
        return False
    return True
