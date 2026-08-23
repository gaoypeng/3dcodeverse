"""Skeleton: plan → correct URDF frames + runnable model.py text."""

from __future__ import annotations

import ast

import numpy as np
import pytest

from codeverse.contracts.plan import ArticulatedPlan, BBox, JointPlan, PartPlan
from codeverse.languages.urdf.lint import lint_model_text, lint_urdf_text
from codeverse.languages.urdf.skeleton import (
    compute_urdf_frames,
    render_model_py,
    render_urdf,
    write_skeleton,
)
from codeverse.spatial.joints import fk, load_urdf
from codeverse.workspace import Workspace


def test_frames_chain(cabinet_plan):
    fr = compute_urdf_frames(cabinet_plan)
    assert fr.root == "body" and list(fr.links) == ["body", "door", "handle"]
    assert fr.links["door"].frame_xyz == (-0.29, -0.2, 0.0)
    assert fr.links["handle"].frame_xyz == (0.2, -0.24, 0.4)
    by = {j.name: j for j in fr.joints}
    assert by["hinge"].origin_xyz == (-0.29, -0.2, 0.0)
    # handle joint origin is relative to the DOOR frame (accumulated)
    assert np.allclose(by["handle_mount"].origin_xyz, (0.49, -0.04, 0.4))
    assert by["handle_mount"].type == "fixed" and by["handle_mount"].lower is None


def test_rest_shift():
    plan = ArticulatedPlan(
        object_name="Box", summary="s", overall_bbox=BBox(center=(0, 0, 0.1), extents=(0.2, 0.2, 0.2)), root_link="Base",
        parts=[PartPlan(name="Base", role="r", description="d", bbox=BBox(center=(0, 0, 0.05), extents=(0.2, 0.2, 0.1))),
               PartPlan(name="Lid", role="r", description="d", bbox=BBox(center=(0, 0, 0.11), extents=(0.2, 0.2, 0.02)))],
        joints=[JointPlan(name="lid_hinge", type="revolute", parent="Base", child="Lid", axis=(1, 0, 0), pivot=(0, 0.1, 0.1),
                          lower=0.0, upper=1.5, rest=0.5)],
    )
    fr = compute_urdf_frames(plan)
    j = fr.joints[0]
    assert (j.lower, j.upper) == (-0.5, 1.0) and j.rest == 0.5  # q=0 is the authored rest
    # the rendered files SAY so, next to the shifted limit and the pivot, so the agent who
    # reads the plan table (unshifted lower/upper/rest) and the skeleton sees one rule
    urdf_text = render_urdf(fr)
    assert '<limit lower="-0.5" upper="1" effort="10" velocity="1"/>  <!-- plan lower=0 upper=1.5 rest=0.5' in urdf_text
    assert "shifted by -rest" in urdf_text and "q=0 is the authored pose" in urdf_text
    assert "(plan rest=0.5 → this pose is URDF q=0)" in render_model_py(plan, fr)
    assert lint_urdf_text(urdf_text)[0] == []


def test_no_rest_note_when_rest_is_zero(cabinet_plan):
    fr = compute_urdf_frames(cabinet_plan)
    urdf_text = render_urdf(fr)
    assert all(j.rest == 0.0 for j in fr.joints)
    assert "shifted by -rest" not in urdf_text and "plan rest=" not in render_model_py(cabinet_plan, fr)
    assert '<limit lower="0" upper="1.57" effort="10" velocity="1"/>' in urdf_text


def test_rendered_files_lint_clean_and_load(tmp_path, cabinet_plan):
    fr = compute_urdf_frames(cabinet_plan)
    urdf_text = render_urdf(fr)
    findings, links = lint_urdf_text(urdf_text)
    assert findings == [] and links == ["body", "door", "handle"]
    model_text = render_model_py(cabinet_plan, fr)
    ast.parse(model_text)
    assert lint_model_text(model_text, links) == []
    p = tmp_path / "robot.urdf"
    p.write_text(urdf_text)
    r = load_urdf(p, load_meshes=False)
    T = fk(r, {})
    # visual origin is the inverse of the link frame → meshes land where authored
    for name in fr.links:
        assert np.allclose(T[name] @ r.links[name].visual_origin, np.eye(4), atol=1e-9), name


def test_instances_expand():
    plan = ArticulatedPlan(
        object_name="Cart", summary="s", overall_bbox=BBox(center=(0, 0, 0.3), extents=(1, 1, 0.6)), root_link="Frame",
        parts=[PartPlan(name="Frame", role="r", description="d", bbox=BBox(center=(0, 0, 0.4), extents=(0.8, 0.5, 0.1))),
               PartPlan(name="Wheel", role="r", description="d", bbox=BBox(center=(-0.3, 0, 0.1), extents=(0.2, 0.05, 0.2)), instances=2)],
        joints=[JointPlan(name="axle", type="continuous", parent="Frame", child="Wheel", axis=(0, 1, 0), pivot=(-0.3, 0, 0.1))],
    )
    fr = compute_urdf_frames(plan)
    assert list(fr.links) == ["frame", "wheel_1", "wheel_2"]
    assert [j.name for j in fr.joints] == ["axle_1", "axle_2"]
    assert fr.links["wheel_2"].frame_xyz[0] > fr.links["wheel_1"].frame_xyz[0]
    assert lint_urdf_text(render_urdf(fr))[0] == []


def test_write_skeleton(tmp_path, drawer_plan):
    ws = Workspace(tmp_path / "ws").create()
    paths = write_skeleton(ws, drawer_plan)
    assert [p.name for p in paths] == ["model.py", "robot.urdf"]
    assert "prismatic" in paths[1].read_text() and 'box(\'drawer\'' in paths[0].read_text()


def test_skeleton_rejects_static_plan(tmp_path):
    from codeverse.contracts.plan import StaticPlan
    from codeverse.languages.urdf.runtime import UrdfBlenderRuntime

    sp = StaticPlan(object_name="x", summary="s", overall_bbox=BBox(center=(0, 0, 0), extents=(1, 1, 1)),
                    parts=[PartPlan(name="A", role="r", description="d", bbox=BBox(center=(0, 0, 0), extents=(1, 1, 1)))])
    with pytest.raises(TypeError):
        UrdfBlenderRuntime().skeleton(Workspace(tmp_path / "ws").create(), sp)
