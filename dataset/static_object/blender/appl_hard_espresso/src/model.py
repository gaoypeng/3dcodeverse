"""EspressoMachine — Blender (bpy) model."""
import bpy
from mathutils import Vector
from parts.base_feet import build_base_feet
from parts.main_chassis import build_main_chassis
from parts.water_tank import build_water_tank
from parts.cup_warmer_rail import build_cup_warmer_rail
from parts.drip_tray import build_drip_tray
from parts.drip_grate import build_drip_grate
from parts.group_head import build_group_head
from parts.portafilter import build_portafilter
from parts.steam_wand import build_steam_wand
from parts.steam_knob import build_steam_knob
from parts.pressure_gauge import build_pressure_gauge
from parts.control_switches import build_control_switches


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
    # build every part in plan order
    build_base_feet()
    build_main_chassis()
    build_water_tank()
    build_cup_warmer_rail()
    build_drip_tray()
    build_drip_grate()
    build_group_head()
    build_portafilter()
    build_steam_wand()
    build_steam_knob()
    build_pressure_gauge()
    build_control_switches()
    _selfcheck()


main()
