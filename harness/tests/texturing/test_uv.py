"""uv.py: projections, world-scale texel density, seam splitting."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from codeverse3d.texturing.apply import choose_projection, split_by_key, unwrap


def test_choose_projection_rules():
    assert choose_projection(np.array([0.04, 0.45, 0.04]), "auto") == ("cylinder", 1)
    assert choose_projection(np.array([0.44, 0.03, 0.42]), "auto")[0] == "box"  # slab
    assert choose_projection(np.array([0.44, 0.03, 0.08]), "auto")[0] == "box"  # elongated slab, not round
    assert choose_projection(np.array([1, 1, 1]), "planar_y") == ("planar_y", 1)
    assert choose_projection(np.array([1, 1, 1]), "planar_z") == ("planar_z", 2)
    assert choose_projection(np.array([0.1, 2.0, 0.1]), "cylinder") == ("cylinder", 1)


def test_box_projection_world_scale_and_splits():
    m = trimesh.creation.box(extents=(0.6, 0.3, 0.3))
    uw = unwrap(m.vertices, m.faces, np.eye(4), projection="box", tile_size_m=0.3)
    assert uw.projection == "box"
    assert len(uw.faces) == len(m.faces)
    # every box vertex touches 3 axis-groups → 8 vertices × 3 keys = 24
    assert len(uw.vertices) == 24 and uw.n_split == 16
    # the 0.6 m faces span 2 tiles along u, 0.3 m span 1 tile
    spans = []
    for f in uw.faces:
        uv = uw.uv[f]
        spans.append(uv.max(0) - uv.min(0))
    assert max(s.max() for s in spans) == pytest.approx(2.0, abs=1e-6)
    # scaling the world transform ×2 doubles the UV range (texel density is world-space)
    T = np.diag([2.0, 2.0, 2.0, 1.0])
    uw2 = unwrap(m.vertices, m.faces, T, projection="box", tile_size_m=0.3)
    assert (uw2.uv.max(0) - uw2.uv.min(0)) == pytest.approx(2 * (uw.uv.max(0) - uw.uv.min(0)), abs=1e-6)


def test_cylinder_projection_seam_and_caps():
    m = trimesh.creation.cylinder(radius=0.05, height=0.5, sections=32)  # along z
    uw = unwrap(m.vertices, m.faces, np.eye(4), projection="auto", tile_size_m=0.1)
    assert uw.projection == "cylinder" and uw.axis == 2
    assert uw.n_split > 0  # seam + caps split
    # side faces: u spread per face must be small (no face stretched across the seam)
    for f in uw.faces:
        uv = uw.uv[f]
        assert (uv[:, 0].max() - uv[:, 0].min()) < 1.0
    # v covers height / tile = 5 tiles
    assert (uw.uv[:, 1].max() - uw.uv[:, 1].min()) >= 5.0 - 1e-6


def test_split_by_key_preserves_geometry_and_normals():
    m = trimesh.creation.box()
    normals = np.asarray(m.vertex_normals)
    uv_c = np.zeros((len(m.faces), 3, 2))
    key = np.zeros((len(m.faces), 3), dtype=int)
    v, f, n, uv, n_split = split_by_key(m.vertices, m.faces, normals, uv_c, key)
    assert n_split == 0 and len(v) == len(m.vertices) and np.allclose(n, normals)
    assert np.allclose(v[f], m.vertices[m.faces])


def test_unwrap_rejects_bad_input():
    with pytest.raises(ValueError):
        unwrap(np.zeros((3, 3)), np.zeros((0, 3), dtype=int), np.eye(4))
    m = trimesh.creation.box()
    with pytest.raises(ValueError):
        unwrap(m.vertices, m.faces, np.eye(4), tile_size_m=0.0)
