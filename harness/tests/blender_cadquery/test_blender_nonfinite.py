"""D114 on the static path (``wrappers/run_bpy.py``, real Blender): a NaN transform or vertex in what the
build would export is the script's error — the glTF exporter would drop a NaN translation silently and
ship the part at the origin — and ``--blend`` packs the images the script made in memory, so the
reopened ``object.blend`` is not black."""

from __future__ import annotations

import pytest

from codeverse3d.languages.blender import BlenderRuntime, blender_env
from codeverse3d.proc import run_subprocess

pytestmark = pytest.mark.blender

PART = '''import bpy

me = bpy.data.meshes.new("Seat")
me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [], [(0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)])
seat = bpy.data.objects.new("Seat", me)
bpy.context.collection.objects.link(seat)
'''


def test_a_nan_location_fails_the_build_and_exports_nothing(tmp_ws, blender_bin) -> None:
    (tmp_ws.src / "model.py").write_text(PART + 'seat.location.y = float("nan")\n')
    r = BlenderRuntime(blender=blender_bin).build(tmp_ws, timeout_s=120)
    assert not r.ok and not r.harness_failure and r.error_type == "NonFiniteTransform", (r.error_type, r.error_message)
    assert "'Seat': location=(0, nan, 0)" in r.error_message
    assert not (tmp_ws.artifacts / "object.glb").exists()


def test_a_nan_vertex_fails_the_build(tmp_ws, blender_bin) -> None:
    (tmp_ws.src / "model.py").write_text(PART + 'me.vertices[3].co.z = float("nan")\n')
    r = BlenderRuntime(blender=blender_bin).build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "NonFiniteGeometry", (r.error_type, r.error_message)
    assert "'Seat': 1 of 4 evaluated vertices" in r.error_message


def test_the_blend_keeps_a_texture_made_in_memory(tmp_ws, blender_bin) -> None:
    (tmp_ws.src / "model.py").write_text(PART + '''
img = bpy.data.images.new("Weave", 8, 8)
img.pixels = [0.8, 0.3, 0.1, 1.0] * 64
hdr = bpy.data.images.new("Sheen", 4, 4, float_buffer=True)
hdr.pixels = [2.0, 0.5, 0.25, 1.0] * 16
mat = bpy.data.materials.new("Fabric")
for im in (img, hdr):
    mat.node_tree.nodes.new("ShaderNodeTexImage").image = im
me.materials.append(mat)
''')
    r = BlenderRuntime(blender=blender_bin).build(tmp_ws, timeout_s=120, stl=False, blend=True)
    assert r.ok, (r.error_type, r.error_message)
    blend = tmp_ws.artifacts / "object.blend"
    code = (f"import bpy\nbpy.ops.wm.open_mainfile(filepath={str(blend)!r})\n"
            "for n in ('Weave', 'Sheen'):\n"
            "    i = bpy.data.images[n]\n"
            "    print('PX', n, i.packed_file is not None, ','.join(f'{v:.3f}' for v in i.pixels[:4]))\n")
    proc = run_subprocess([blender_bin, "-b", "--factory-startup", "--python-expr", code], cwd=tmp_ws.root,
                          env=blender_env(), timeout_s=120)
    rows = {p[1]: (p[2], [float(v) for v in p[3].split(",")]) for p in
            (ln.split() for ln in proc.stdout.splitlines() if ln.startswith("PX "))}
    assert rows["Weave"][0] == "True" and rows["Weave"][1] == pytest.approx([0.8, 0.3, 0.1, 1.0], abs=1 / 255)
    assert rows["Sheen"][0] == "True" and rows["Sheen"][1] == pytest.approx([2.0, 0.5, 0.25, 1.0], abs=1e-3)   # EXR: HDR kept
