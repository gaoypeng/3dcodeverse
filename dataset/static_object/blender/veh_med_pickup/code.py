"""PickupTruck — Blender (bpy) model.

Classic two-door single-cab pickup truck with an open rear cargo bed, rugged all-terrain wheels, flared wheel arches, and prominent front fascia. Dimensions are 2.10 m wide (mirror to mirror) by 4.80 m long by 1.76 m high.
Style: Utilitarian American single-cab pickup with boxy proportions, slightly sloped windshield, pronounced flared wheel arches, horizontal front grille slats, and an open corrugated-style utility bed.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.880) extents (2.100, 4.800, 1.760)
    -> x in [-1.050, 1.050]  y in [-2.400, 2.400]  z in [0.000, 1.760]
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
  - ChassisAndLowerBody: main chassis frame, front hood, and integrated flared wheel arches; bbox center (0.000, -0.050, 0.650) extents (1.880, 4.650, 0.720)  [src/parts/chassis_and_lower_body.py]
  - Cab: two-door driver cabin with tinted glass windows and roof; bbox center (0.000, -0.250, 1.340) extents (1.780, 1.500, 0.780)  [src/parts/cab.py]
  - CargoBed: open rear cargo box with side walls, bulkhead, and tailgate; bbox center (0.000, 1.320, 0.960) extents (1.840, 1.850, 0.580)  [src/parts/cargo_bed.py]
  - FrontWheel x2: steerable front wheel assemblies with treaded rubber tires and alloy rims; bbox center (0.860, -1.450, 0.380) extents (0.260, 0.760, 0.760)  [src/parts/front_wheel.py]
  - RearWheel x2: rear drive wheel assemblies with treaded rubber tires and alloy rims; bbox center (0.860, 1.450, 0.380) extents (0.260, 0.760, 0.760)  [src/parts/rear_wheel.py]
  - FrontGrilleAndBumper: front radiator grille, badge, and heavy-duty front bumper; bbox center (0.000, -2.340, 0.620) extents (1.820, 0.160, 0.520)  [src/parts/front_grille_and_bumper.py]
  - Headlights x2: front headlight and turn signal clusters; bbox center (0.720, -2.320, 0.740) extents (0.280, 0.080, 0.180)  [src/parts/headlights.py]
  - RearBumperAndTaillights: rear step bumper and vertical taillight clusters; bbox center (0.000, 2.340, 0.680) extents (1.840, 0.160, 0.540)  [src/parts/rear_bumper_and_taillights.py]
  - SideMirror x2: exterior side rearview mirrors; bbox center (0.980, -0.720, 1.220) extents (0.240, 0.160, 0.140)  [src/parts/side_mirror.py]

ACCEPTANCE:
  - [a1] Overall vehicle bounding box is approximately 2.10 m (width) x 4.80 m (length) x 1.76 m (height) with bottom tires touching z = 0.0 m ± 0.01 m
  - [a2] Two-door enclosed cab features transparent/tinted windshield, side windows, and rear window
  - [a3] Open rear cargo bed has distinct sidewalls, front bulkhead, and rear tailgate creating a recessed loading volume
  - [a4] Four wheels with treaded tires and central alloy hubs are placed in arched wheel wells
  - [a5] Front fascia contains a centered horizontal grille, dual headlights, and front bumper
  - [a6] Side rearview mirrors are mounted symmetrically on both left and right cab doors
  - [a7] All major subcomponents (cab, bed, chassis, wheels, mirrors) overlap by at least 2 mm without floating gaps
  - [must1] Includes: cab with windows
  - [must2] Includes: open rear bed
  - [must3] Includes: four wheels with hubs
  - [must4] Includes: front grille and headlights
  - [must5] Includes: side mirrors
"""
import bpy
from mathutils import Vector

from parts.chassis_and_lower_body import build_chassis_and_lower_body
from parts.cab import build_cab
from parts.cargo_bed import build_cargo_bed
from parts.front_wheel import build_front_wheel
from parts.rear_wheel import build_rear_wheel
from parts.front_grille_and_bumper import build_front_grille_and_bumper
from parts.headlights import build_headlights
from parts.rear_bumper_and_taillights import build_rear_bumper_and_taillights
from parts.side_mirror import build_side_mirror


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
    build_chassis_and_lower_body()
    build_cab()
    build_cargo_bed()
    build_front_wheel()
    build_rear_wheel()
    build_front_grille_and_bumper()
    build_headlights()
    build_rear_bumper_and_taillights()
    build_side_mirror()
    _selfcheck()


main()
