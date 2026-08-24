"""BenchVise — Blender (bpy) model.
Entry point that imports part builders from parts/*.
"""
import bpy
from mathutils import Vector

from parts.mounting_base import build_mounting_base
from parts.fixed_body import build_fixed_body
from parts.anvil_face import build_anvil_face
from parts.fixed_jaw_plate import build_fixed_jaw_plate
from parts.sliding_jaw import build_sliding_jaw
from parts.sliding_jaw_plate import build_sliding_jaw_plate
from parts.guide_beam import build_guide_beam
from parts.lead_screw import build_lead_screw
from parts.tommy_bar_handle import build_tommy_bar_handle


def _selfcheck():
    """What the harness checks first: meshes exist, no auto-suffixed names, stands on z=0."""
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    assert meshes, "no mesh objects built"
    for o in meshes:
        assert "." not in o.name, f"auto-suffixed name {o.name!r}: give every instance its own name"
    z_min = min((o.matrix_world @ Vector(c)).z for o in meshes for c in o.bound_box)
    assert abs(z_min) < 0.002, f"lowest point z={z_min:.4f}: the object must stand on z=0"
    print(f"[selfcheck] {len(meshes)} mesh objects, z_min={z_min:.4f}")


def main():
    build_mounting_base()
    build_fixed_body()
    build_anvil_face()
    build_fixed_jaw_plate()
    build_sliding_jaw()
    build_sliding_jaw_plate()
    build_guide_beam()
    build_lead_screw()
    build_tommy_bar_handle()
    _selfcheck()


main()
