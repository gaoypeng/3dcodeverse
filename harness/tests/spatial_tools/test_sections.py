from __future__ import annotations

from pathlib import Path

import pytest
import trimesh
from PIL import Image

from codeverse.spatial.sections import cross_section, slices_sheet


def test_cross_section_stool(stool_glb: Path, tmp_path: Path) -> None:
    out = tmp_path / "sec.png"
    o = cross_section(stool_glb, "y", 0.5, out)
    assert o.ok and o.images == [str(out)] and out.is_file()
    assert o.numbers["n_loops"] == 4
    assert o.numbers["total_area_m2"] == pytest.approx(4 * 3.14159 * 0.02**2, rel=0.05)
    assert set(o.numbers["parts_cut"]) == {"Leg_0", "Leg_1", "Leg_2", "Leg_3"}
    assert Image.open(out).size == (512, 512)


def test_cross_section_parts_and_absolute(stool_glb: Path, tmp_path: Path) -> None:
    o = cross_section(stool_glb, "y", 0.43, tmp_path / "s.png", parts=["Seat"], absolute=True)
    assert o.ok and o.numbers["parts_cut"] == {"Seat": 1}
    assert o.numbers["total_area_m2"] == pytest.approx(0.16, rel=1e-3)


def test_hollow_ratio(tmp_path: Path) -> None:
    p = tmp_path / "tube.glb"
    trimesh.creation.annulus(r_min=0.04, r_max=0.05, height=0.3).export(str(p))
    o = cross_section(p, "z", 0.5, tmp_path / "t.png")
    assert o.numbers["n_loops"] == 2
    assert o.numbers["hollow_ratio"] == pytest.approx(0.64, abs=0.03)


def test_bad_inputs(stool_glb: Path, tmp_path: Path) -> None:
    assert not cross_section(stool_glb, "w", 0.5, tmp_path / "x.png").ok
    o = cross_section(stool_glb, "y", 0.5, tmp_path / "x.png", parts=["Nope"])
    assert not o.ok and "Nope" in o.text and "Seat" in o.text
    assert not cross_section(tmp_path / "missing.glb", "y", 0.5, tmp_path / "x.png").ok


def test_slices_sheet(stool_glb: Path, tmp_path: Path) -> None:
    out = tmp_path / "slices.png"
    o = slices_sheet(stool_glb, "y", 5, out)
    assert o.ok and out.is_file() and len(o.numbers["slices"]) == 5
    assert all(s["n_loops"] == 4 for s in o.numbers["slices"][:4])
