"""MidCenturyDiningChair — Blender (bpy) model.

A mid-century modern solid teak dining chair with tapered splayed legs, a contoured wooden seat slab, and a gently curved slatted backrest. Overall dimensions are approximately 0.48 × 0.52 × 0.84 m.
Style: Mid-century Scandinavian design: organic curves, warm teak timber, round tapered legs splayed slightly outward, continuous back posts, and thin vertical back slats.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.015, 0.420) extents (0.480, 0.520, 0.840)
    -> x in [-0.240, 0.240]  y in [-0.245, 0.275]  z in [0.000, 0.840]
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
  - Seat: horizontal contoured seat slab; bbox center (0.000, -0.010, 0.436) extents (0.450, 0.440, 0.028)  [src/parts/seat.py]
  - FrontLeg x2: front load-bearing tapered leg; bbox center (0.185, -0.180, 0.215) extents (0.045, 0.045, 0.430)  [src/parts/front_leg.py]
  - BackLegPost x2: continuous rear leg extending into backrest upright post; bbox center (0.180, 0.170, 0.420) extents (0.045, 0.070, 0.840)  [src/parts/back_leg_post.py]
  - TopRail: curved crest rail spanning back posts; bbox center (0.000, 0.210, 0.815) extents (0.420, 0.060, 0.045)  [src/parts/top_rail.py]
  - BottomBackRail: lower cross rail supporting vertical slats; bbox center (0.000, 0.170, 0.485) extents (0.380, 0.040, 0.025)  [src/parts/bottom_back_rail.py]
  - BackSlat x4: vertical backrest support slat; bbox center (0.050, 0.190, 0.650) extents (0.020, 0.015, 0.310)  [src/parts/back_slat.py]
  - SideStretcher x2: structural side rung connecting front and rear legs; bbox center (0.180, -0.005, 0.180) extents (0.020, 0.350, 0.020)  [src/parts/side_stretcher.py]

ACCEPTANCE:
  - [a1] Total height is between 0.82 m and 0.86 m, and seat top height is at 0.45 m ± 0.02 m above ground.
  - [a2] All four legs contact the ground plane at z = 0 with no gaps or floating elements.
  - [a3] Rear legs are continuous upright posts rising from floor level up to the backrest crest rail.
  - [a4] Backrest contains at least 3 vertical slats framed between top and bottom horizontal rails.
  - [a5] Front and back legs clearly exhibit round conical taper towards the floor.
  - [a6] Wood grain / oiled teak material is consistently applied across all frame and seat members.
  - [must1] Includes: four tapered legs
  - [must2] Includes: seat slab
  - [must3] Includes: backrest with at least three vertical slats
  - [must4] Includes: back legs continue into back posts
"""
import bpy
from mathutils import Vector

from parts.seat import build_seat
from parts.front_leg import build_front_leg
from parts.back_leg_post import build_back_leg_post
from parts.top_rail import build_top_rail
from parts.bottom_back_rail import build_bottom_back_rail
from parts.back_slat import build_back_slat
from parts.side_stretcher import build_side_stretcher


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
    build_front_leg()
    build_back_leg_post()
    build_top_rail()
    build_bottom_back_rail()
    build_back_slat()
    build_side_stretcher()
    _selfcheck()


main()
