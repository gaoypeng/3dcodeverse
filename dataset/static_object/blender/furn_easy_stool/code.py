"""ThreeLeggedStool — Blender (bpy) model.

A simple three-legged round wooden stool standing 0.45 m tall, featuring a thick round seat, three splayed turned legs, and lower reinforcing stretchers.
Style: Minimalist Nordic craft style: solid light-toned natural wood, rounded edges, clean splayed geometry with 120-degree radial symmetry.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.225) extents (0.380, 0.380, 0.450)
    -> x in [-0.190, 0.190]  y in [-0.190, 0.190]  z in [0.000, 0.450]
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
  - Seat: circular top sitting surface; bbox center (0.000, 0.000, 0.433) extents (0.340, 0.340, 0.035)  [src/parts/seat.py]
  - Leg x3: supporting splayed leg; bbox center (0.000, -0.138, 0.210) extents (0.032, 0.087, 0.420)  [src/parts/leg.py]
  - Stretcher x3: horizontal rung bracing the legs; bbox center (0.120, 0.069, 0.150) extents (0.245, 0.045, 0.020)  [src/parts/stretcher.py]

ACCEPTANCE:
  - [a1] Overall stool height must measure 0.450 m ± 0.005 m from ground to top of seat
  - [a2] Seat is a solid circular disc approximately 0.34 m in diameter
  - [a3] Three legs arranged radially at 120-degree intervals
  - [a4] All three legs splay outward and contact the ground plane at z=0.000 m
  - [a5] Connecting stretchers link the legs at a lower level for structural stability
  - [a6] Uniform natural light oak wood texture across all parts
  - [dim1] Overall dimensions match the request within 5%: height = 0.450 m
  - [must1] Includes: round seat
  - [must2] Includes: three splayed legs of equal length
  - [must3] Includes: legs touch the ground
"""
import bpy
from mathutils import Vector

from parts.seat import build_seat
from parts.leg import build_leg
from parts.stretcher import build_stretcher


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
    build_seat()
    build_leg()
    build_stretcher()
    _selfcheck()


main()
