"""The objective complexity vector: axes behave, the index orders artifacts, and
the whole thing is deterministic and additive to ``measure_glb``."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse.spatial.complexity import (
    COMPLEXITY_WEIGHTS,
    ComplexityVector,
    band_of,
    complexity_of_glb,
    complexity_of_parts,
)
from codeverse.spatial.measure import measure_glb, part_meshes


def _export(path: Path, parts: dict[str, trimesh.Trimesh]) -> Path:
    sc = trimesh.Scene()
    for name, mesh in parts.items():
        sc.add_geometry(mesh, node_name=name, geom_name=name)
    sc.export(str(path))
    return path


def primitive_scene(path: Path) -> Path:
    """Three unmodified boxes — the box-stack floor of the scale."""
    parts = {}
    for i, (w, h, y) in enumerate(((0.4, 0.05, 0.42), (0.4, 0.4, 0.2), (0.35, 0.05, 0.02))):
        box = trimesh.creation.box(extents=(w, h, 0.4))
        box.apply_translation((0, y, 0))
        parts[f"Slab_{i}"] = box
    return _export(path, parts)


def detailed_scene(path: Path) -> Path:
    """The same three slabs plus repeated hardware, a hollow tube and fine bevel
    strips: more parts, more small sharp edges, repeated families, hollowness."""
    parts = {}
    for i, (w, h, y) in enumerate(((0.4, 0.05, 0.42), (0.4, 0.4, 0.2), (0.35, 0.05, 0.02))):
        box = trimesh.creation.box(extents=(w, h, 0.4))
        box.apply_translation((0, y, 0))
        parts[f"Slab_{i}"] = box
    for i, (x, z) in enumerate(((-0.15, -0.15), (0.15, -0.15), (-0.15, 0.15), (0.15, 0.15))):
        knob = trimesh.creation.icosphere(subdivisions=2, radius=0.012)
        knob.apply_translation((x, 0.47, z))
        parts[f"Knob_{i}"] = knob
    tube = trimesh.creation.annulus(r_min=0.028, r_max=0.035, height=0.3)
    tube.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, (1, 0, 0)))
    tube.apply_translation((0, 0.25, 0.22))
    parts["Tube"] = tube
    for i in range(6):
        strip = trimesh.creation.box(extents=(0.4, 0.004, 0.004))
        strip.apply_translation((0, 0.40 + 0.006 * i, -0.2))
        parts[f"Bead_{i}"] = strip
    return _export(path, parts)


def test_vector_shape_and_weights(stool_glb: Path) -> None:
    vec = complexity_of_glb(stool_glb)
    assert set(COMPLEXITY_WEIGHTS) == set(vec.components) == set(vec.axes)
    assert sum(COMPLEXITY_WEIGHTS.values()) == pytest.approx(1.0)
    assert vec.part_count == 5 and vec.tri_count > 0
    assert 0.0 < vec.index < 1.0
    assert all(0.0 <= v <= 1.0 for v in vec.components.values())
    assert vec.band == band_of(vec.index)
    assert vec.index == pytest.approx(
        sum(COMPLEXITY_WEIGHTS[a] * v for a, v in vec.components.items()), abs=5e-4
    )


def test_detail_beats_primitives(tmp_path: Path) -> None:
    plain = complexity_of_glb(primitive_scene(tmp_path / "plain.glb"))
    rich = complexity_of_glb(detailed_scene(tmp_path / "rich.glb"))
    assert rich.index > plain.index + 0.15
    assert rich.feature_density > plain.feature_density
    assert rich.part_count > plain.part_count
    assert rich.symmetry_groups >= 2 > plain.symmetry_groups  # knobs and beads repeat
    assert rich.hollowness > plain.hollowness


def test_hollowness_separates_tube_from_solid(tmp_path: Path) -> None:
    solid = trimesh.creation.cylinder(radius=0.05, height=0.3, sections=32)
    tube = trimesh.creation.annulus(r_min=0.04, r_max=0.05, height=0.3)
    assert complexity_of_parts({"Solid": solid}).hollowness < 0.05
    assert complexity_of_parts({"Tube": tube}).hollowness > 0.5


def test_silhouette_frame_beats_block(tmp_path: Path) -> None:
    block = complexity_of_parts({"Block": trimesh.creation.box(extents=(0.4, 0.4, 0.4))})
    bars = {}
    for i in range(4):
        bar = trimesh.creation.box(extents=(0.4, 0.02, 0.02))
        angle = i * math.pi / 2
        bar.apply_transform(trimesh.transformations.rotation_matrix(angle, (1, 0, 0)))
        bar.apply_translation((0, 0.19 * math.cos(angle), 0.19 * math.sin(angle)))
        bars[f"Bar_{i}"] = bar
    frame = complexity_of_parts(bars)
    assert block.silhouette < 2.0  # a solid box projects to a square: crack P²/4πA ≈ 1.3
    assert frame.silhouette > 2 * block.silhouette


def test_deterministic(tmp_path: Path) -> None:
    import codeverse.spatial.measure as measure

    glb = detailed_scene(tmp_path / "rich.glb")
    first = complexity_of_glb(glb)
    measure._lru.clear()
    assert complexity_of_glb(glb).model_dump() == first.model_dump()


def test_degenerate_input_never_raises() -> None:
    empty = complexity_of_parts({})
    assert empty.index == 0.0 and empty.extra["error"] == "no geometry"
    none_only = complexity_of_parts({"Ghost": None})
    assert none_only.part_count == 0


def test_measure_glb_carries_the_vector(stool_glb: Path) -> None:
    block = measure_glb(stool_glb).extra["complexity"]
    vec = ComplexityVector.model_validate(block)
    assert vec.part_count == 5
    assert vec.tri_count == measure_glb(stool_glb).tri_count
    assert vec.version >= 1


def test_symmetry_groups_count_geometric_families(solid_stool_glb: Path) -> None:
    parts = part_meshes(trimesh.load(str(solid_stool_glb), force="scene", process=False))
    vec = complexity_of_parts(parts)
    assert vec.symmetry_groups == 1  # the four legs are one family; the seat is alone
    assert vec.extra["repeated_parts"] == 4


def test_bands_are_ordered() -> None:
    labels = [band_of(x) for x in (0.0, 0.3, 0.45, 0.6, 0.9)]
    assert labels == ["trivial", "simple", "moderate", "complex", "intricate"]
    assert band_of(1.0) == "intricate"


def test_assembly_depth_sees_subassemblies(tmp_path: Path) -> None:
    sc = trimesh.Scene()
    box = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    sc.add_geometry(box, node_name="Base", geom_name="Base")
    child = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    child.apply_translation((0, 0.15, 0))
    sc.add_geometry(child, node_name="Top", geom_name="Top", parent_node_name="Base")
    path = tmp_path / "nested.glb"
    sc.export(str(path))
    assert complexity_of_glb(path).assembly_depth >= 2


def test_normalisers_saturate_and_floor() -> None:
    tiny = complexity_of_parts({"Dot": trimesh.creation.box(extents=(0.01, 0.01, 0.01))})
    assert tiny.components["part_count"] == 0.0  # one part is the floor of the scale
    assert tiny.index < 0.25 and tiny.band == "trivial"
    assert np.isfinite(tiny.index)
