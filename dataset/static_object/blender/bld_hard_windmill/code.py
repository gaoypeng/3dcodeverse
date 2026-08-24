"""DutchWindmill — Blender (bpy) model.

Traditional Dutch octagonal smock windmill with thatched cap, four cross lattice sails, a wrap-around gallery balcony with railing, an arched entrance door, and shuttered windows.
"""
import bpy
from mathutils import Vector

from parts.base_structure import build_base_structure
from parts.smock_body import build_smock_body
from parts.stage_balcony import build_stage_balcony
from parts.windmill_cap import build_windmill_cap
from parts.sail_rotor_hub import build_sail_rotor_hub
from parts.sail_blades import build_sail_blades
from parts.entrance_door import build_entrance_door
from parts.shuttered_windows import build_shuttered_windows


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
    build_base_structure()
    build_smock_body()
    build_stage_balcony()
    build_windmill_cap()
    build_sail_rotor_hub()
    build_sail_blades()
    build_entrance_door()
    build_shuttered_windows()
    _selfcheck()


main()
