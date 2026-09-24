"""BlenderRuntime: offline guards + integration tests on the real binary."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from codeverse3d.languages.blender import (
    BlenderNotFoundError,
    BlenderRuntime,
    blender_env,
)


def test_missing_binary_raises(tmp_ws) -> None:
    rt = BlenderRuntime(blender="/nonexistent/blender")
    (tmp_ws.src / "model.py").write_text("import bpy\n")
    with pytest.raises(BlenderNotFoundError):
        rt.build(tmp_ws)


def test_blender_env_scrubs_secrets_and_host_python(monkeypatch) -> None:
    """model.py runs inside blender: no credentials, no host PYTHONPATH."""
    monkeypatch.setenv("PYTHONPATH", "/tmp/x")
    monkeypatch.setenv("GEMINI_API_KEYS", "k1,k2")
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "t")
    env = blender_env()
    assert "PYTHONPATH" not in env and env["PYTHONNOUSERSITE"] == "1"
    assert "GEMINI_API_KEYS" not in env and "SOME_SERVICE_TOKEN" not in env and "PATH" in env


# --------------------------------------------------------------------------- real Blender
@pytest.mark.blender
def test_live_skeleton_builds_and_exports_canonical_glb(tmp_ws, table_plan, blender_bin) -> None:
    trimesh = pytest.importorskip("trimesh")
    rt = BlenderRuntime(blender=blender_bin)
    paths = rt.skeleton(tmp_ws, table_plan)
    rels = [p.relative_to(tmp_ws.root).as_posix() for p in paths]
    assert rels == ["src/model.py", "src/parts/table_top.py", "src/parts/leg.py", "src/parts/shelf.py"]
    assert rt.expected_files(table_plan) == rels  # the layout IS the skeleton
    lint = rt.lint(tmp_ws)
    assert lint.passed, [(f.target, f.message) for f in lint.errors]
    t0 = time.monotonic()
    r = rt.build(tmp_ws, timeout_s=120)
    dt = time.monotonic() - t0
    assert r.ok, (r.error_type, r.error_message, r.stderr_tail)
    assert dt < 60
    assert "[selfcheck]" in r.stdout_tail
    assert Path(r.extra_paths["stl"]).stat().st_size > 0
    scene = trimesh.load(r.glb_path)
    names = set(scene.graph.nodes_geometry)
    assert (tmp_ws.src / "parts" / "leg.py").is_file()  # multi-file skeleton
    assert names == {"TableTop", "Shelf", "Leg_0", "Leg_1", "Leg_2", "Leg_3"}
    assert "Legs" not in scene.graph.nodes  # instances are TOP-LEVEL: no parent Empty node
    lo, hi = scene.bounds
    assert abs(lo[1]) < 1e-4 and abs(hi[1] - 0.6) < 1e-3  # Y-up, ground contact, height 0.6
    assert abs(lo[0] + 0.25) < 1e-3 and abs(hi[2] - 0.25) < 1e-3
    leg0 = scene.geometry[scene.graph["Leg_0"][1]]
    assert len(leg0.faces) > 12  # bevel modifier applied on export
    assert leg0.visual.material.baseColorFactor[0] > leg0.visual.material.baseColorFactor[2]  # oak-ish
    c = r.census
    assert c["tri_count"] == sum(o["tri_count"] for o in c["objects"] if o["type"] == "MESH")
    assert c["n_mesh_objects"] == 6 and c["frame"] == "z_up_neg_y_front" and not c["cameras"]
    assert abs(c["scene_bbox_min"][2]) < 1e-4 and abs(c["scene_bbox_max"][2] - 0.6) < 1e-3


@pytest.mark.blender
def test_live_timeout_and_memory_cap(tmp_ws, blender_bin) -> None:
    rt = BlenderRuntime(blender=blender_bin)
    (tmp_ws.src / "model.py").write_text("import bpy, time\nbpy.ops.mesh.primitive_cube_add(size=1)\nwhile True:\n    time.sleep(0.1)\n")
    t0 = time.monotonic()
    r = rt.build(tmp_ws, timeout_s=2)
    assert not r.ok and r.error_type == "BuildTimeout" and time.monotonic() - t0 < 20
    (tmp_ws.src / "model.py").write_text("import bpy\nimport numpy as np\nbpy.ops.mesh.primitive_cube_add(size=1)\nbig = np.ones((3_000_000_000,))\n")
    r = rt.build(tmp_ws, timeout_s=60)
    assert not r.ok and r.error_type == "MemoryError" and r.error_line == 4


@pytest.mark.blender
def test_live_selfcheck_skips_hidden_cutters(tmp_ws, blender_bin) -> None:
    """Audit 2026-09-24 N6a: the skeleton's self-check measured a hidden boolean cutter
    (`Cutter.001`, reaching z = -0.5) and failed a build whose GLB stands exactly on z = 0."""
    from codeverse3d.languages.blender import selfcheck_source

    (tmp_ws.src / "model.py").write_text(
        "import bpy\nfrom mathutils import Vector\n" + selfcheck_source() +
        "bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5)); body = bpy.context.object; body.name = 'Body'\n"
        "bpy.ops.mesh.primitive_cylinder_add(radius=0.2, depth=2, location=(0, 0, 0.5)); cut = bpy.context.object\n"
        "cut.name = 'Cutter.001'; body.modifiers.new('Hole', 'BOOLEAN').object = cut\n"
        "cut.hide_set(True); cut.hide_render = True\n_selfcheck()\n")
    r = BlenderRuntime(blender=blender_bin).build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    assert "[selfcheck] 1 mesh objects, z_min=0.0000" in r.stdout_tail
