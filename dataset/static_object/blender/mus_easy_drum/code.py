"""SnareDrum — Blender (bpy) model.

Standard 14-inch metal concert snare drum equipped with triple-flanged hoops, 8 chrome lugs with tension rods, and two crossed hickory drumsticks resting across the batter head.
Style: Modern concert/rock chrome snare drum with high-gloss polished chrome hardware, coated white top batter head, 8-point radial lug symmetry, and classic tapered wooden sticks.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.098) extents (0.420, 0.420, 0.196)
    -> x in [-0.210, 0.210]  y in [-0.210, 0.210]  z in [0.000, 0.196]
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
  - DrumShell: main acoustic cylindrical resonant body; bbox center (0.000, 0.000, 0.085) extents (0.356, 0.356, 0.140)  [src/parts/drum_shell.py]
  - TopHead: upper batter drumhead membrane; bbox center (0.000, 0.000, 0.155) extents (0.356, 0.356, 0.003)  [src/parts/top_head.py]
  - BottomHead: lower resonant drumhead membrane; bbox center (0.000, 0.000, 0.015) extents (0.356, 0.356, 0.002)  [src/parts/bottom_head.py]
  - TopHoop: top triple-flanged steel counterhoop rim; bbox center (0.000, 0.000, 0.162) extents (0.395, 0.395, 0.024)  [src/parts/top_hoop.py]
  - BottomHoop: bottom steel counterhoop rim with snare gate cutouts; bbox center (0.000, 0.000, 0.010) extents (0.395, 0.395, 0.020)  [src/parts/bottom_hoop.py]
  - Lugs x8: casing brackets mounted to shell to receive tension rods; bbox center (0.000, -0.186, 0.085) extents (0.024, 0.022, 0.060)  [src/parts/lugs.py]
  - TensionRods x8: threaded tuning bolts tensioning the top rim; bbox center (0.000, -0.188, 0.138) extents (0.012, 0.012, 0.052)  [src/parts/tension_rods.py]
  - SnareStrainer: snare throw-off mechanism lever on side of shell; bbox center (-0.185, 0.000, 0.085) extents (0.032, 0.048, 0.068)  [src/parts/snare_strainer.py]
  - DrumStickPrimary: first drumstick resting diagonally across the top drumhead; bbox center (-0.010, -0.005, 0.173) extents (0.380, 0.220, 0.018)  [src/parts/drum_stick_primary.py]
  - DrumStickSecondary: second drumstick resting across the first stick on top of the drum; bbox center (0.015, 0.010, 0.186) extents (0.240, 0.360, 0.022)  [src/parts/drum_stick_secondary.py]

ACCEPTANCE:
  - [a1] Bottom hoop base rests at z = 0.000 m (within +/- 0.002 m)
  - [a2] Drum shell has cylindrical diameter of approx 0.356 m (14 inches)
  - [a3] Top batter drumhead is clearly visible inside the upper rim
  - [a4] Exactly eight evenly-spaced tension rods and lug casings are distributed radially around the shell perimeter
  - [a5] Two drumsticks rest on top of the snare drum in an overlapping arrangement
  - [a6] Distinct material separation between polished chrome metal shell/hoops, white head membrane, and wooden drumsticks
  - [must1] Includes: cylindrical shell
  - [must2] Includes: top head
  - [must3] Includes: at least eight tension rods around the rim
  - [must4] Includes: two sticks
"""
import bpy
from mathutils import Vector

from parts.drum_shell import build_drum_shell
from parts.top_head import build_top_head
from parts.bottom_head import build_bottom_head
from parts.top_hoop import build_top_hoop
from parts.bottom_hoop import build_bottom_hoop
from parts.lugs import build_lugs
from parts.tension_rods import build_tension_rods
from parts.snare_strainer import build_snare_strainer
from parts.drum_stick_primary import build_drum_stick_primary
from parts.drum_stick_secondary import build_drum_stick_secondary


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
    build_drum_shell()
    build_top_head()
    build_bottom_head()
    build_top_hoop()
    build_bottom_hoop()
    build_lugs()
    build_tension_rods()
    build_snare_strainer()
    build_drum_stick_primary()
    build_drum_stick_secondary()
    _selfcheck()


main()
