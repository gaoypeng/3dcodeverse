"""PedestalWritingDesk — Blender (bpy) model.

Classic twin-pedestal executive writing desk with a central kneehole, leather inlay top, 6 drawers with brass drop handles, and moulded plinth bases. Measures 1.46 m wide by 0.76 m deep and 0.76 m high.
Style: Traditional English executive/partner desk styling in rich polished mahogany. Features bevelled top overhangs, recessed green leather writing surface with gilt perimeter, panelled drawer fronts, and antiqued brass bail pulls.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.380) extents (1.460, 0.760, 0.760)
    -> x in [-0.730, 0.730]  y in [-0.380, 0.380]  z in [0.000, 0.760]
  * One mesh object per part, named EXACTLY as below (PascalCase); instances Name_0..Name_N-1,
    each TOP-LEVEL (never parented under an Empty — that merges them into ONE measured part).
  * LAYOUT: this file is the ENTRY. Each part lives in src/parts/<snake>.py and exports
    build_<snake>() -> bpy.types.Object; main() below imports and calls them in order.
    Edit geometry in the part files; keep this file to imports + calls + self-check.
  * Materials: Principled BSDF (Base Color / Roughness / Metallic). GLB keeps flat PBR + image
    textures only (procedural node textures are NOT exported) — rely on geometry + flat PBR.
  * Modifiers may stay unapplied (the exporter applies them). Keep < 500k triangles, < 120 s.
  * NEVER: cameras, lights, world, render settings, export/import, file IO, bpy.ops.wm.*.
  * Only bpy / bmesh / mathutils / math / random (seeded). No other imports.

PARTS:
  - DeskTop: main horizontal writing desktop surface; bbox center (0.000, 0.000, 0.742) extents (1.460, 0.760, 0.035)  [src/parts/desk_top.py]
  - LeatherInlay: recessed leather writing pad; bbox center (0.000, 0.000, 0.758) extents (1.320, 0.620, 0.006)  [src/parts/leather_inlay.py]
  - PedestalCabinet x2: structural pedestal carcasses housing the drawers; bbox center (0.480, 0.000, 0.380) extents (0.380, 0.680, 0.690)  [src/parts/pedestal_cabinet.py]
  - PlinthBase x2: bottom moulded foundation plinth under each pedestal; bbox center (0.480, 0.000, 0.030) extents (0.410, 0.710, 0.060)  [src/parts/plinth_base.py]
  - ModestyPanel: rear kneehole privacy panel bridging both pedestals; bbox center (0.000, 0.260, 0.480) extents (0.580, 0.020, 0.490)  [src/parts/modesty_panel.py]
  - DrawerFront x6: visible drawer facade panels; bbox center (0.480, -0.335, 0.410) extents (0.340, 0.022, 0.185)  [src/parts/drawer_front.py]
  - DrawerPull x6: ornate brass bail pull handles; bbox center (0.480, -0.355, 0.410) extents (0.120, 0.025, 0.045)  [src/parts/drawer_pull.py]

ACCEPTANCE:
  - [a1] Overall dimensions match 1.46 m wide, 0.76 m deep, and 0.76 m high (±0.03 m)
  - [a2] Desk sits firmly on the ground plane (z = 0.0 m) via plinth bases
  - [a3] Two distinct pedestal cabinets separated by a central kneehole at least 0.55 m wide
  - [a4] Three distinct drawer fronts visible on each pedestal (total 6 drawers)
  - [a5] Brass pull handles installed on the front face of each drawer
  - [a6] Desk top surface features a central contrasting green leather inlay
  - [a7] Desk top visibly overhangs the pedestal carcasses on front, back, and sides
  - [must1] Includes: two drawer pedestals
  - [must2] Includes: three drawers per pedestal with visible fronts
  - [must3] Includes: knee-hole gap
  - [must4] Includes: brass pull handles
  - [must5] Includes: desk top overhangs
"""
import bpy
from mathutils import Vector

from parts.desk_top import build_desk_top
from parts.leather_inlay import build_leather_inlay
from parts.pedestal_cabinet import build_pedestal_cabinet
from parts.plinth_base import build_plinth_base
from parts.modesty_panel import build_modesty_panel
from parts.drawer_front import build_drawer_front
from parts.drawer_pull import build_drawer_pull


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
    # build every part (order = plan order); parts are placed at world pose by their builders
    build_desk_top()
    build_leather_inlay()
    build_pedestal_cabinet()
    build_plinth_base()
    build_modesty_panel()
    build_drawer_front()
    build_drawer_pull()
    _selfcheck()


main()
