from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from codeverse3d.spatial.sections import SliceManifest, cross_section, judge_slices


def test_cross_section_position_is_a_fraction_of_the_selected_parts(stool_glb: Path, tmp_path: Path) -> None:
    o = cross_section(stool_glb, "y", 0.5, tmp_path / "s.png", parts=["Seat"])  # the seat's own mid-plane
    assert o.ok and o.numbers["parts_cut"] == {"Seat": 1} and o.numbers["at_m"] == pytest.approx(0.43)
    assert o.numbers["total_area_m2"] == pytest.approx(0.16, rel=1e-3)
    assert "Plane axes: x horizontal, z vertical" in o.text


def test_cross_section_that_cuts_nothing_answers_without_an_image(tmp_path: Path) -> None:
    glb = _two_boxes(tmp_path / "apart.glb", overlap_x=-0.1)  # a 10 cm gap at the bbox middle
    o = cross_section(glb, "x", 0.5, tmp_path / "gap.png")
    assert o.ok and o.numbers["n_loops"] == 0 and o.images == [] and "nothing" in o.text
    assert not (tmp_path / "gap.png").exists()


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


# ===================================================================== D48 pixels, pinned
#: rendered by the judge_slices below from _golden_fixture; regenerate ONLY for an intended
#: change to what the judge sees: ``python -m tests.spatial_tools.test_sections``
_GOLDEN = Path(__file__).parent / "data" / "judge_slices"
_GOLDEN_ERROR_PAIRS = [("Handle", "Body")]
#: the renderer's own inputs: another version of any of them may move pixels by itself
_GOLDEN_LIBS = ("matplotlib", "shapely", "trimesh")


def _golden_fixture(path: Path) -> Path:
    """Every branch the judge slices draw: a gate-ERROR pair (hatch), two plain overlaps
    (darkened blend), an instance group (one legend row "x2"), a hole (the tube), a part
    whose section is a zero-area loop (outline only) and 18 groups on one plane (the
    legend cap)."""
    sc = trimesh.Scene()

    def add(name: str, mesh: trimesh.Trimesh) -> None:
        sc.add_geometry(mesh, node_name=name, geom_name=name)

    def box(name: str, extents: tuple[float, float, float], at: tuple[float, float, float]) -> None:
        m = trimesh.creation.box(extents=extents)
        m.apply_translation(at)
        add(name, m)

    upright = trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0))
    box("Body", (0.6, 0.4, 0.4), (0, 0.35, 0))
    box("Handle", (0.16, 0.08, 0.1), (0, 0.4, 0.19))  # 6 cm into the body: the ERROR pair
    for i, x in enumerate((-0.2, 0.2)):
        leg = trimesh.creation.cylinder(radius=0.04, height=0.3, sections=24)
        leg.apply_transform(upright)
        leg.apply_translation((x, 0.15, 0))
        add(f"Leg_{i}", leg)
    tube = trimesh.creation.annulus(r_min=0.05, r_max=0.08, height=0.2, sections=32)
    tube.apply_transform(upright)
    tube.apply_translation((0, 0.65, 0))
    add("Tube", tube)
    # three coplanar strips closing on themselves: the section is a loop of zero area
    ys = (0.2, 0.35, 0.5)
    verts = np.array([(x, y, -0.3) for x in (-0.2, 0.2) for y in ys])
    faces = [f for i in range(3) for f in ([i, (i + 1) % 3, 3 + (i + 1) % 3], [i, 3 + (i + 1) % 3, 3 + i])]
    add("Fin", trimesh.Trimesh(verts, np.array(faces), process=False))
    for k in range(15):
        box(f"Rivet{chr(65 + k)}", (0.02, 0.04, 0.04), (-0.28 + 0.04 * k, 0.57, -0.03))
    sc.export(str(path))
    return path


def _render_golden_case(out: Path) -> dict:
    """The fixture's judge slices → the manifest as stored (the glb path dropped)."""
    glb = _golden_fixture(out / "fixture.glb")
    return judge_slices(glb, _GOLDEN_ERROR_PAIRS, out).model_dump(mode="json", exclude={"glb"})


def _lib_versions() -> dict[str, str]:
    import importlib

    return {name: importlib.import_module(name).__version__ for name in _GOLDEN_LIBS}


def test_judge_slices_pixels_match_the_golden_images(tmp_path: Path) -> None:
    """What the judge SEES on a gate-ERROR round (D48) is pinned to the pixel: a refactor
    of the slice renderer must leave both PNGs byte-for-byte the same image."""
    meta = json.loads((_GOLDEN / "meta.json").read_text())
    if _lib_versions() != meta["libs"]:
        pytest.skip(f"goldens were rendered with {meta['libs']}, this env has {_lib_versions()}")
    manifest = _render_golden_case(tmp_path)
    assert manifest == json.loads((_GOLDEN / "manifest.json").read_text())
    rendered = [s["png"] for s in manifest["slices"] if s["rendered"]]
    assert rendered == ["slice_front_back.png", "slice_left_right.png"]
    for png in rendered:
        got = np.asarray(Image.open(tmp_path / png).convert("RGBA"))
        want = np.asarray(Image.open(_GOLDEN / png).convert("RGBA"))
        assert got.shape == want.shape, f"{png}: {got.shape} vs golden {want.shape}"
        n_diff = int(np.any(got != want, axis=-1).sum())
        assert n_diff == 0, f"{png}: {n_diff} pixels differ from {_GOLDEN / png} (see {tmp_path / png})"


if __name__ == "__main__":  # regenerate the goldens — an intended change to the judge's pixels only
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        stored = _render_golden_case(Path(d))
        _GOLDEN.mkdir(parents=True, exist_ok=True)
        for s in stored["slices"]:
            if s["rendered"]:
                shutil.copyfile(Path(d) / s["png"], _GOLDEN / s["png"])
    (_GOLDEN / "manifest.json").write_text(json.dumps(stored, indent=1) + "\n")
    (_GOLDEN / "meta.json").write_text(json.dumps({"libs": _lib_versions()}, indent=1) + "\n")
    print(f"wrote {_GOLDEN}")
