"""ToyCar — Blender (bpy) model.

Chunky toddler-style toy car with a smoothly rounded one-piece body and four wide protruding wheels resting firmly on the ground. Measures approximately 0.14 x 0.21 x 0.11 m.
Style: Chunky Scandinavian wooden/plastic toddler toy aesthetic with exaggerated soft fillets (r=8-15 mm), playful bulbous cabin proportions, oversized thick wheels, and contrasting smooth materials.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.055) extents (0.136, 0.210, 0.110)
    -> x in [-0.068, 0.068]  y in [-0.105, 0.105]  z in [0.000, 0.110]
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
  - CarBody: main unified vehicle body and cabin; bbox center (0.000, 0.000, 0.062) extents (0.088, 0.204, 0.096)  [src/parts/car_body.py]
  - FrontWheel x2: front rolling wheel; bbox center (0.054, -0.065, 0.035) extents (0.024, 0.070, 0.070)  [src/parts/front_wheel.py]
  - RearWheel x2: rear rolling wheel; bbox center (0.054, 0.065, 0.035) extents (0.024, 0.070, 0.070)  [src/parts/rear_wheel.py]
  - FrontAxle: transverse front wheel axle; bbox center (0.000, -0.065, 0.035) extents (0.114, 0.008, 0.008)  [src/parts/front_axle.py]
  - RearAxle: transverse rear wheel axle; bbox center (0.000, 0.065, 0.035) extents (0.114, 0.008, 0.008)  [src/parts/rear_axle.py]
  - Headlight x2: front decorative headlights; bbox center (0.026, -0.100, 0.045) extents (0.018, 0.006, 0.018)  [src/parts/headlight.py]
  - Taillight x2: rear decorative taillights; bbox center (0.026, 0.100, 0.045) extents (0.014, 0.006, 0.014)  [src/parts/taillight.py]

ACCEPTANCE:
  - [a1] Overall bounding box is approximately 0.136 x 0.210 x 0.110 m (±0.01 m)
  - [a2] All four wheels touch the ground plane at z = 0.000 m (±0.001 m)
  - [a3] Wheel outer faces extend beyond the car body sides by at least 0.020 m on each side
  - [a4] Car body is a cohesive, rounded one-piece shape with smooth corners
  - [a5] Axles pass continuously through the lower body section into wheel hubs
  - [a6] Headlights and taillights are symmetrically placed on the front and rear faces
  - [must1] Includes: one-piece rounded body
  - [must2] Includes: four wheels touching the ground
  - [must3] Includes: wheels protrude sideways
"""
import bpy
from mathutils import Vector

from parts.car_body import build_car_body
from parts.front_wheel import build_front_wheel
from parts.rear_wheel import build_rear_wheel
from parts.front_axle import build_front_axle
from parts.rear_axle import build_rear_axle
from parts.headlight import build_headlight
from parts.taillight import build_taillight


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
    build_car_body()
    build_front_wheel()
    build_rear_wheel()
    build_front_axle()
    build_rear_axle()
    build_headlight()
    build_taillight()
    _selfcheck()


main()
