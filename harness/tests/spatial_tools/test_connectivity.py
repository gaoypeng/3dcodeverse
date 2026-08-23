from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse.contracts.artifacts import Severity
from codeverse.spatial.connectivity import check_connectivity, pair_distance, penetration_depth
from tests.spatial_tools.conftest import FLOAT_GAP_M


def test_floating_leg_found_with_exact_gap(stool_glb: Path) -> None:
    r = check_connectivity(stool_glb)
    assert r.gate == "connectivity" and not r.passed
    errs = r.errors
    assert len(errs) == 1 and errs[0].target == "Leg_3"
    d = errs[0].data
    assert d["nearest"] == "Seat"
    assert d["gap_m"] == pytest.approx(FLOAT_GAP_M, abs=1e-4)
    assert np.allclose(d["gap_vector_m"], (0.0, FLOAT_GAP_M, 0.0), atol=1e-4)
    assert "translate 'Leg_3' by (+0.0000, +0.0050, +0.0000)" in errs[0].fix_hint


def test_solid_stool_passes(solid_stool_glb: Path) -> None:
    r = check_connectivity(solid_stool_glb)
    assert r.passed and not r.errors
    assert any("connected" in f.message for f in r.findings)


def test_penetration_is_flagged(tmp_path: Path) -> None:
    sc = trimesh.Scene()
    a = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    a.apply_translation((0, 0.1, 0))
    b = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    b.apply_translation((0, 0.1 + 0.2 - 0.03, 0))  # 30 mm deep into A
    sc.add_geometry(a, node_name="A", geom_name="A")
    sc.add_geometry(b, node_name="B", geom_name="B")
    p = tmp_path / "pen.glb"
    sc.export(str(p))
    r = check_connectivity(p)
    pen = [f for f in r.findings if "interpenetrate" in f.message]
    assert pen and pen[0].severity == Severity.ERROR
    assert pen[0].data["depth_m"] == pytest.approx(0.03, abs=0.004)


def test_hairline_overlap_is_fine(tmp_path: Path) -> None:
    sc = trimesh.Scene()
    a = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    a.apply_translation((0, 0.1, 0))
    b = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    b.apply_translation((0, 0.2 + 0.05 - 0.001, 0))  # 1 mm weld overlap
    sc.add_geometry(a, node_name="A", geom_name="A")
    sc.add_geometry(b, node_name="B", geom_name="B")
    p = tmp_path / "weld.glb"
    sc.export(str(p))
    r = check_connectivity(p)
    assert r.passed
    assert not [f for f in r.findings if "interpenetrate" in f.message]


def test_tiny_island_warns(tmp_path: Path) -> None:
    body = trimesh.creation.box(extents=(0.3, 0.3, 0.3))
    body.apply_translation((0, 0.15, 0))
    crumb = trimesh.creation.box(extents=(0.004, 0.004, 0.004))
    crumb.apply_translation((0.5, 0.5, 0.5))
    part = trimesh.util.concatenate([body, crumb])
    sc = trimesh.Scene()
    sc.add_geometry(part, node_name="Body", geom_name="Body")
    p = tmp_path / "crumb.glb"
    sc.export(str(p))
    r = check_connectivity(p)
    warns = [f for f in r.findings if f.severity == Severity.WARN]
    assert warns and "tiny disconnected island" in warns[0].message


def test_missing_glb_is_error(tmp_path: Path) -> None:
    r = check_connectivity(tmp_path / "none.glb")
    assert not r.passed and r.errors


def test_pair_distance_sampled_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse.spatial.connectivity as c

    monkeypatch.setattr(c, "_fcl_available", lambda: False)
    a = trimesh.creation.box(extents=(1, 1, 1))
    b = trimesh.creation.box(extents=(1, 1, 1))
    b.apply_translation((1.02, 0, 0))
    pd = pair_distance("a", a, "b", b)
    assert pd.distance == pytest.approx(0.02, abs=1e-6)
    assert pd.gap_vector[0] == pytest.approx(0.02, abs=1e-6)


def test_penetration_depth_zero_when_apart() -> None:
    a = trimesh.creation.box(extents=(1, 1, 1))
    b = trimesh.creation.box(extents=(1, 1, 1))
    b.apply_translation((2, 0, 0))
    assert penetration_depth(a, b) == (0.0, 0.0)
