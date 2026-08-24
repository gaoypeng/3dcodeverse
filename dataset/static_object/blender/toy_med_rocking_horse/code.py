"""RockingHorse — Blender (bpy) model.

A classic handcrafted wooden rocking horse with twin curved base rockers, sculpted torso, splayed legs, handle grips, decorative mane, and a contoured saddle seat. Dimensions: 0.34 m wide, 0.85 m long, 0.65 m tall.
Style: Traditional Scandinavian/artisan woodwork aesthetic. Features smooth birch/pine wood tones with rounded edges, visible pegged dowel joints, arched rockers with upturned tips, and a contrasting rich brown saddle.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.325) extents (0.340, 0.850, 0.650)
    -> x in [-0.170, 0.170]  y in [-0.425, 0.425]  z in [0.000, 0.650]
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
  - Rocker x2: curved ground rail providing rocking motion; bbox center (0.130, 0.000, 0.050) extents (0.030, 0.850, 0.100)  [src/parts/rocker.py]
  - FootCrossbar x2: structural stretcher and footrest dowel; bbox center (0.000, -0.220, 0.070) extents (0.260, 0.026, 0.026)  [src/parts/foot_crossbar.py]
  - FrontLeg x2: forward supporting leg; bbox center (0.090, -0.160, 0.220) extents (0.035, 0.070, 0.280)  [src/parts/front_leg.py]
  - RearLeg x2: rear supporting leg; bbox center (0.090, 0.160, 0.220) extents (0.035, 0.070, 0.280)  [src/parts/rear_leg.py]
  - HorseBody: main torso and barrel of the horse; bbox center (0.000, -0.010, 0.375) extents (0.140, 0.400, 0.140)  [src/parts/horse_body.py]
  - HorseNeckHead: sculpted neck, head, muzzle, and ears; bbox center (0.000, -0.220, 0.520) extents (0.090, 0.240, 0.260)  [src/parts/horse_neck_head.py]
  - HorseMane: decorative mane along the neck crest; bbox center (0.000, -0.160, 0.540) extents (0.024, 0.160, 0.200)  [src/parts/horse_mane.py]
  - HandleGrip: hand grip dowel for the rider; bbox center (0.000, -0.240, 0.530) extents (0.240, 0.032, 0.032)  [src/parts/handle_grip.py]
  - Saddle: contoured seat for the rider; bbox center (0.000, 0.020, 0.455) extents (0.160, 0.200, 0.040)  [src/parts/saddle.py]
  - HorseTail: rear tail feature; bbox center (0.000, 0.240, 0.350) extents (0.040, 0.140, 0.150)  [src/parts/horse_tail.py]

ACCEPTANCE:
  - [a1] Overall height is 0.65 m ± 0.03 m and overall length is 0.85 m ± 0.03 m
  - [a2] Rockers make contact with the floor at z = 0 with symmetric upward curvature at front and back
  - [a3] Four distinct splayed legs firmly connect the body to the twin rockers
  - [a4] Transverse handle dowel extends symmetrically to the left and right through the head/neck
  - [a5] Saddle is centered on top of the horse body with smooth ergonomic seating profile
  - [a6] Stylized mane is visible along the dorsal crest of the neck
  - [must1] Includes: horse body with head and four legs
  - [must2] Includes: two curved rockers
  - [must3] Includes: saddle
  - [must4] Includes: handle grips
  - [must5] Includes: mane
"""
import bpy
from mathutils import Vector

from parts.rocker import build_rocker
from parts.foot_crossbar import build_foot_crossbar
from parts.front_leg import build_front_leg
from parts.rear_leg import build_rear_leg
from parts.horse_body import build_horse_body
from parts.horse_neck_head import build_horse_neck_head
from parts.horse_mane import build_horse_mane
from parts.handle_grip import build_handle_grip
from parts.saddle import build_saddle
from parts.horse_tail import build_horse_tail


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
    build_rocker()
    build_foot_crossbar()
    build_front_leg()
    build_rear_leg()
    build_horse_body()
    build_horse_neck_head()
    build_horse_mane()
    build_handle_grip()
    build_saddle()
    build_horse_tail()
    _selfcheck()


main()
