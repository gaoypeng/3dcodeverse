from __future__ import annotations

from pathlib import Path

import pytest
import trimesh
from PIL import Image

from codeverse3d.spatial.sections import SliceManifest, cross_section, judge_slices


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


# ===================================================================== judge slices (D48)
def _two_boxes(path: Path, *, overlap_x: float = 0.05, dy: float = 0.0) -> Path:
    """Box A (0.3³ at origin, resting on y=0) + box B shifted so they overlap by
    ``overlap_x`` along x (and optionally offset ``dy`` up)."""
    sc = trimesh.Scene()
    a = trimesh.creation.box(extents=(0.3, 0.3, 0.3))
    a.apply_translation((0, 0.15, 0))
    b = trimesh.creation.box(extents=(0.3, 0.3, 0.3))
    b.apply_translation((0.3 - overlap_x, 0.15 + dy, 0))
    sc.add_geometry(a, node_name="A", geom_name="A")
    sc.add_geometry(b, node_name="B", geom_name="B")
    sc.export(str(path))
    return path


def test_judge_slices_hatches_only_gate_error_pairs(tmp_path: Path) -> None:
    glb = _two_boxes(tmp_path / "boxes.glb")
    m = judge_slices(glb, [("A", "B")], tmp_path / "err")
    assert [s.name for s in m.rendered()] == ["front_back", "left_right"]
    assert m.error_pairs == [("A", "B")]
    for s in m.rendered():
        assert [(p.a, p.b) for p in s.hatched_pairs] == [("B", "A")] or \
               [(p.a, p.b) for p in s.hatched_pairs] == [("A", "B")]
        assert s.plain_pairs == []
        assert (tmp_path / "err" / s.png).is_file()

    # the SAME geometry with no gate-ERROR pair: plain darkened blend, no hatch (F1)
    plain = judge_slices(glb, [], tmp_path / "plain")
    for s in plain.rendered():
        assert s.hatched_pairs == [] and len(s.plain_pairs) == 1


def test_judge_slices_ignores_hairline_contact_slivers(tmp_path: Path) -> None:
    # 1 mm of in-plane overlap (a weld) is under the ~2·erode thickness: not drawn at all
    glb = _two_boxes(tmp_path / "weld.glb", overlap_x=0.3, dy=0.299)  # stacked, 1 mm y-overlap
    m = judge_slices(glb, [("A", "B")], tmp_path / "out")
    assert m.rendered()
    for s in m.rendered():
        assert s.hatched_pairs == [] and s.plain_pairs == []


def test_judge_slices_drops_a_degenerate_slice(tmp_path: Path) -> None:
    """A portal frame whose centre-x plane cuts only the thin top beam (4 % of the
    part's projected silhouette): F4 drops front_back, keeps left_right."""
    cols = []
    for x in (-0.45, 0.45):
        c = trimesh.creation.box(extents=(0.1, 1.0, 0.1))
        c.apply_translation((x, 0.5, 0))
        cols.append(c)
    beam = trimesh.creation.box(extents=(1.0, 0.04, 0.1))
    beam.apply_translation((0, 0.98, 0))
    sc = trimesh.Scene()
    sc.add_geometry(trimesh.util.concatenate([*cols, beam]), node_name="Frame", geom_name="Frame")
    glb = tmp_path / "portal.glb"
    sc.export(str(glb))

    m = judge_slices(glb, [], tmp_path / "out")
    by_name = {s.name: s for s in m.slices}
    assert not by_name["front_back"].rendered and "degenerate" in by_name["front_back"].reason
    assert by_name["left_right"].rendered
    assert not (tmp_path / "out" / "slice_front_back.png").is_file()


def test_judge_slices_manifest_round_trips_and_bad_glb_degrades(tmp_path: Path) -> None:
    glb = _two_boxes(tmp_path / "boxes.glb")
    out = tmp_path / "out"
    m = judge_slices(glb, [("B", "A"), ("A", "A")], out, planes=("front_back",))  # self-pair ignored
    assert m.error_pairs == [("A", "B")] and [s.name for s in m.slices] == ["front_back"]
    stored = SliceManifest.model_validate_json((out / "manifest.json").read_text())
    assert stored == m

    bad = judge_slices(tmp_path / "missing.glb", [], tmp_path / "bad")
    assert bad.slices == [] and bad.errors and (tmp_path / "bad" / "manifest.json").is_file()
