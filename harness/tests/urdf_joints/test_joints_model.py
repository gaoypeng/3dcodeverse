"""load_urdf / fk / pose sampling (offline, primitives + trimesh-written GLBs)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from codeverse3d.spatial.joints_model import (
    UrdfError,
    fk,
    load_urdf,
)
from codeverse3d.spatial.joints_poses import limit_poses, pose_label, pose_samples
from tests.urdf_joints.conftest import write_prims_robot


def test_load_prims_fk_and_pose_samples(tmp_path):
    r = load_urdf(write_prims_robot(tmp_path / "cab.urdf"))
    assert r.root == "body" and r.link_order() == ["body", "door"]
    assert r.joints["hinge"].type == "revolute" and r.joints["hinge"].upper == 1.57
    T = fk(r, {"hinge": math.pi / 2})["door"]
    # axis -z: +90° about -z maps +x → -y
    assert np.allclose(T[:3, 3], [-0.29, -0.2, 0])
    assert np.allclose(T[:3, :3] @ [1, 0, 0], [0, -1, 0], atol=1e-9)
    with pytest.raises(UrdfError):
        fk(r, {"nope": 1.0})
    # pose samples and their labels
    poses = pose_samples(r, n_random=4, seed=1)
    labels = [pose_label(r, q) for q in poses]
    assert labels[:3] == ["rest", "hinge@mid", "hinge@upper"]  # single joint → no random combos
    assert [label for label, _ in limit_poses(r)] == ["rest", "hinge@upper"]
    # continuous joint sampling
    p = tmp_path / "wheel.urdf"
    p.write_text("<robot name='w'><link name='a'/><link name='b'/><joint name='spin' type='continuous'><parent link='a'/><child link='b'/><axis xyz='0 1 0'/></joint>"
                 "<link name='c'/><joint name='s' type='prismatic'><parent link='a'/><child link='c'/><axis xyz='1 0 0'/><limit lower='-0.1' upper='0.2' effort='1' velocity='1'/></joint></robot>")
    r2 = load_urdf(p)
    poses2 = pose_samples(r2, n_random=3, seed=0)
    vals = sorted(q["spin"] for q in poses2 if set(q) == {"spin"})
    assert np.allclose(vals, [-math.pi / 2, math.pi / 2, math.pi])
    assert sum(1 for q in poses2 if len(q) == 2) == 3
    for q in poses2:
        if "s" in q:
            assert -0.1 - 1e-9 <= q["s"] <= 0.2 + 1e-9


def test_load_errors(tmp_path):
    p = tmp_path / "bad.urdf"
    p.write_text("<robot name='x'><link name='a'/><link name='b'/></robot>")
    with pytest.raises(UrdfError, match="exactly one root"):
        load_urdf(p)
    p.write_text("<robot name='x'><link name='a'/><link name='b'/><joint name='j' type='revolute'><parent link='a'/><child link='b'/></joint></robot>")
    with pytest.raises(UrdfError, match="limit"):
        load_urdf(p)
    p.write_text("<robot name='x'><link name='a'/><link name='b'/><joint name='j' type='fixed'><parent link='a'/><child link='zz'/></joint></robot>")
    with pytest.raises(UrdfError, match="unknown"):
        load_urdf(p)
    p.write_text("<robot name='x'><link name='a'><visual><geometry><mesh filename='meshes/nope.glb'/></geometry></visual></link></robot>")
    with pytest.raises(UrdfError, match="not found"):
        load_urdf(p)
