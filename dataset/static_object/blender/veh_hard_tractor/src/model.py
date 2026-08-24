"""FarmTractor — Blender (bpy) model.

Classic open-cab agricultural farm tractor measuring approximately 1.90 x 3.10 x 2.35 m. Features massive treaded rear drive tyres, smaller ribbed front steering wheels, sculpted engine hood, vertical exhaust stack, operator station with steering wheel and seat, and an overhead safety roll bar.
Style: Classic utility tractor aesthetic with bold agricultural red body panels, cream-painted steel rims, rugged black rubber tires, and exposed mechanical details like the vertical exhaust stack and tubular steel roll bar.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, -0.050, 1.175) extents (1.900, 3.100, 2.350)
    -> x in [-0.950, 0.950]  y in [-1.600, 1.500]  z in [0.000, 2.350]
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
  - Chassis: structural backbone and drivetrain housing; bbox center (0.000, 0.000, 0.550) extents (0.650, 2.200, 0.450)  [src/parts/chassis.py]
  - EngineHood: engine enclosure and front nose; bbox center (0.000, -0.750, 1.050) extents (0.600, 1.300, 0.650)  [src/parts/engine_hood.py]
  - RearWheel x2: primary rear drive wheels with heavy lug treads; bbox center (0.780, 0.700, 0.700) extents (0.340, 1.400, 1.400)  [src/parts/rear_wheel.py]
  - FrontWheel x2: front steering wheels with directional tread; bbox center (0.620, -1.050, 0.350) extents (0.220, 0.700, 0.700)  [src/parts/front_wheel.py]
  - RearFender x2: rear wheel mudguards; bbox center (0.720, 0.650, 1.250) extents (0.380, 1.200, 0.500)  [src/parts/rear_fender.py]
  - OperatorSeat: driver seating; bbox center (0.000, 0.450, 1.150) extents (0.480, 0.450, 0.450)  [src/parts/operator_seat.py]
  - SteeringAssembly: steering column and wheel; bbox center (0.000, 0.050, 1.250) extents (0.420, 0.350, 0.450)  [src/parts/steering_assembly.py]
  - ExhaustStack: vertical engine exhaust pipe; bbox center (0.260, -0.600, 1.650) extents (0.120, 0.120, 0.950)  [src/parts/exhaust_stack.py]
  - RollBar: roll-over protection structure (ROPS); bbox center (0.000, 0.680, 1.700) extents (1.050, 0.150, 1.300)  [src/parts/roll_bar.py]

ACCEPTANCE:
  - [a1] Rear wheel diameter (~1.40m) is approximately double front wheel diameter (~0.70m)
  - [a2] Lowest vertices of all four tires touch the ground plane at z = 0.00m ± 0.01m
  - [a3] Rear tires show distinct agricultural deep chevron/herringbone tread pattern
  - [a4] Vertical exhaust stack rises prominently above the right side of the engine hood
  - [a5] Roll bar (ROPS) forms an arch spanning over and behind the driver seat
  - [a6] Operator station contains both the driver seat and angled steering wheel column
  - [a7] Overall vehicle height reaches 2.35m ± 0.05m at the peak of the roll bar
  - [must1] Includes: rear wheels much larger than front wheels
  - [must2] Includes: tyre tread detail
  - [must3] Includes: exhaust stack
  - [must4] Includes: seat and steering wheel
  - [must5] Includes: roll bar
  - [must6] Includes: engine hood
"""
import bpy
from mathutils import Vector

from parts.chassis import build_chassis
from parts.engine_hood import build_engine_hood
from parts.rear_wheel import build_rear_wheel
from parts.front_wheel import build_front_wheel
from parts.rear_fender import build_rear_fender
from parts.operator_seat import build_operator_seat
from parts.steering_assembly import build_steering_assembly
from parts.exhaust_stack import build_exhaust_stack
from parts.roll_bar import build_roll_bar


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
    build_chassis()
    build_engine_hood()
    build_rear_wheel()
    build_front_wheel()
    build_rear_fender()
    build_operator_seat()
    build_steering_assembly()
    build_exhaust_stack()
    build_roll_bar()
    _selfcheck()


main()
