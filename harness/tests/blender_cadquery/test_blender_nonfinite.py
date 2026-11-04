"""D114 on the static path (``wrappers/run_bpy.py``, real Blender): a NaN transform or vertex in what the
build would export is the script's error — the glTF exporter would drop a NaN translation silently and
ship the part at the origin —."""

from __future__ import annotations

import pytest

from codeverse3d.languages.blender import BlenderRuntime

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

