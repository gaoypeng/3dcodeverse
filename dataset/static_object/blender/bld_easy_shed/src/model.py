"""GardenShed — Blender (bpy) model.

A compact timber garden shed featuring horizontal lap siding, an overhanging pitched gable roof, a front plank door with black hardware, and a framed four-pane window on the side wall. Measures approximately 2.04 x 2.24 x 2.35 m.
Style: Traditional rustic garden shed styling with overlapping horizontal timber weatherboards, felt/shingle textured pitched roof with generous eaves overhang, simple wooden trims, and classic black ironmongery.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 1.175) extents (2.040, 2.240, 2.350)
    -> x in [-1.020, 1.020]  y in [-1.120, 1.120]  z in [0.000, 2.350]
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
  - FloorBase: Foundation platform and floor deck; bbox center (0.000, 0.000, 0.050) extents (1.820, 2.020, 0.100)  [src/parts/floor_base.py]
  - WallStructure: Main rectangular enclosing walls; bbox center (0.000, 0.000, 0.975) extents (1.800, 2.000, 1.750)  [src/parts/wall_structure.py]
  - GableWalls x2: Front and rear triangular gable wall peaks; bbox center (0.000, 0.000, 2.075) extents (1.800, 2.000, 0.450)  [src/parts/gable_walls.py]
  - PitchedRoof x2: Two-panel pitched roof structure with overhangs; bbox center (0.000, 0.000, 2.075) extents (2.040, 2.240, 0.550)  [src/parts/pitched_roof.py]
  - RoofTrim: Fascia boards and bargeboards along eaves and gable edges; bbox center (0.000, 0.000, 2.080) extents (2.040, 2.240, 0.560)  [src/parts/roof_trim.py]
  - FrontDoor: Entrance door on front wall; bbox center (-0.100, -1.005, 0.925) extents (0.780, 0.050, 1.650)  [src/parts/front_door.py]
  - DoorHardware: Hinges and T-handle latch for door; bbox center (-0.100, -1.035, 0.925) extents (0.700, 0.030, 1.400)  [src/parts/door_hardware.py]
  - SideWindowFrame: Window casing and internal mullion crossbars; bbox center (0.905, 0.150, 1.200) extents (0.050, 0.650, 0.650)  [src/parts/side_window_frame.py]
  - WindowGlass: Transparent window glazing panes; bbox center (0.902, 0.150, 1.200) extents (0.008, 0.550, 0.550)  [src/parts/window_glass.py]

ACCEPTANCE:
  - [a1] Overall shed dimensions approximately 2.04 m width (X) x 2.24 m depth (Y) x 2.35 m apex height (Z)
  - [a2] Shed base rests flat on the ground plane at Z = 0.0 m
  - [a3] Four enclosed box walls forming the perimeter of the shed body
  - [a4] Pitched gable roof clearly slopes down from a central ridge with eaves extending past wall boundaries
  - [a5] Full-height vertical plank door clearly visible and properly framed on front (-Y) elevation
  - [a6] Glazed window with 4-pane cross grid frame clearly visible on the side (+X) elevation
  - [a7] Black iron strap hinges and door latch handle attached to the front door
  - [a8] Fascia and bargeboard trim outline the pitched roof perimeter and gable edges
  - [must1] Includes: box walls
  - [must2] Includes: pitched roof overhang
  - [must3] Includes: door on front
  - [must4] Includes: window
"""
import bpy
from mathutils import Vector

from parts.floor_base import build_floor_base
from parts.wall_structure import build_wall_structure
from parts.gable_walls import build_gable_walls
from parts.pitched_roof import build_pitched_roof
from parts.roof_trim import build_roof_trim
from parts.front_door import build_front_door
from parts.door_hardware import build_door_hardware
from parts.side_window_frame import build_side_window_frame
from parts.window_glass import build_window_glass


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
    build_floor_base()
    build_wall_structure()
    build_gable_walls()
    build_pitched_roof()
    build_roof_trim()
    build_front_door()
    build_door_hardware()
    build_side_window_frame()
    build_window_glass()
    _selfcheck()


main()
