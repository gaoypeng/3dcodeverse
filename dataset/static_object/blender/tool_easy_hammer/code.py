"""ClawHammer — Blender (bpy) model.

Classic 16 oz curved claw hammer with a turned hickory wood handle and a forged steel head. Total dimensions are approximately 0.035 × 0.165 × 0.330 m.
Style: Traditional American pattern carpenter's claw hammer: cylindrical turned hardwood handle with ergonomic butt flare, rectangular forged steel eye, round striking face with chamfered rim, and a curved two-prong split claw for pulling nails.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.005, 0.165) extents (0.035, 0.165, 0.330)
    -> x in [-0.018, 0.018]  y in [-0.077, 0.088]  z in [0.000, 0.330]
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
  - WoodenHandle: cylindrical ergonomic wooden grip and shaft; bbox center (0.000, 0.000, 0.155) extents (0.032, 0.034, 0.310)  [src/parts/wooden_handle.py]
  - HammerEye: central mounting block of the steel head; bbox center (0.000, 0.000, 0.305) extents (0.032, 0.036, 0.045)  [src/parts/hammer_eye.py]
  - StrikingNeck: tapered neck connecting eye to striking face; bbox center (0.000, -0.040, 0.305) extents (0.030, 0.045, 0.030)  [src/parts/striking_neck.py]
  - StrikingFace: flat front impact face for nail driving; bbox center (0.000, -0.065, 0.305) extents (0.032, 0.012, 0.032)  [src/parts/striking_face.py]
  - ClawBase: swept transition block from eye to rear claw; bbox center (0.000, 0.032, 0.300) extents (0.030, 0.035, 0.040)  [src/parts/claw_base.py]
  - ClawProng x2: curved tapered fork prong for pulling nails; bbox center (0.010, 0.065, 0.280) extents (0.012, 0.045, 0.045)  [src/parts/claw_prong.py]
  - TopWedge: fixation wedge locking handle tenon into eye; bbox center (0.000, 0.000, 0.328) extents (0.016, 0.008, 0.004)  [src/parts/top_wedge.py]

ACCEPTANCE:
  - [a1] Total vertical height is 0.330 m ± 0.015 m, standing with handle butt touching ground plane z=0
  - [a2] Handle is cylindrical/turned wood extending continuously from z=0 up into the steel head
  - [a3] Striking face is located at the front (-Y), circular with a flat planar impact surface
  - [a4] Rear of the head (+Y) features a two-pronged curved forked claw separated by a central V-notch
  - [a5] Head parts (Eye, Neck, Face, ClawBase, ClawProngs) are metallic steel and form a continuous intersecting assembly
  - [a6] Striking face to claw tip distance along Y is between 0.150 m and 0.175 m
  - [must1] Includes: cylindrical handle
  - [must2] Includes: steel head with a flat face
  - [must3] Includes: forked claw on the back
"""
import bpy
from mathutils import Vector

from parts.wooden_handle import build_wooden_handle
from parts.hammer_eye import build_hammer_eye
from parts.striking_neck import build_striking_neck
from parts.striking_face import build_striking_face
from parts.claw_base import build_claw_base
from parts.claw_prong import build_claw_prong
from parts.top_wedge import build_top_wedge


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
    build_wooden_handle()
    build_hammer_eye()
    build_striking_neck()
    build_striking_face()
    build_claw_base()
    build_claw_prong()
    build_top_wedge()
    _selfcheck()


main()
