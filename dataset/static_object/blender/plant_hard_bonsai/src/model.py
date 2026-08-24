"""BonsaiTree — Blender (bpy) model.

Sekijoju (root-over-rock) style bonsai tree standing in a shallow glazed ceramic oval tray with lush moss, featuring an organic rugged rock, winding exposed roots, a tapered gnarled trunk, and three tiered foliage pads. Overall dimensions are approximately 0.38 × 0.30 × 0.48 m.
Style: Japanese traditional bonsai aesthetic (Moyogi/Sekijoju). Tapered twisting trunk, horizontal cloud-pruned foliage pads (Jin/Shari woody accents), unglazed stoneware/ceramic oval pot with small feet.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.240) extents (0.380, 0.300, 0.480)
    -> x in [-0.190, 0.190]  y in [-0.150, 0.150]  z in [0.000, 0.480]
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
  - CeramicTray: shallow oval bonsai pot container; bbox center (0.000, 0.000, 0.020) extents (0.340, 0.240, 0.040)  [src/parts/ceramic_tray.py]
  - SoilAndMoss: potting soil bed and live green moss carpet; bbox center (0.000, 0.000, 0.038) extents (0.320, 0.220, 0.030)  [src/parts/soil_and_moss.py]
  - Rock: rugged mineral crag anchored in the soil; bbox center (-0.020, 0.010, 0.100) extents (0.130, 0.110, 0.140)  [src/parts/rock.py]
  - ExposedRoots: buttress roots wrapping and gripping the rock; bbox center (-0.020, 0.010, 0.110) extents (0.150, 0.130, 0.150)  [src/parts/exposed_roots.py]
  - TwistedTrunk: main structural trunk and primary branches; bbox center (0.010, -0.010, 0.270) extents (0.260, 0.200, 0.240)  [src/parts/twisted_trunk.py]
  - LowerFoliagePad: lowest horizontal branch foliage cloud; bbox center (0.120, -0.060, 0.280) extents (0.140, 0.120, 0.060)  [src/parts/lower_foliage_pad.py]
  - MiddleFoliagePad: middle counter-balancing foliage cloud; bbox center (-0.110, 0.050, 0.350) extents (0.130, 0.110, 0.060)  [src/parts/middle_foliage_pad.py]
  - ApexFoliagePad: top crown foliage canopy dome; bbox center (0.020, -0.010, 0.440) extents (0.150, 0.140, 0.080)  [src/parts/apex_foliage_pad.py]

ACCEPTANCE:
  - [a1] Overall height is between 0.44 m and 0.50 m with base touching z = 0.0 m
  - [a2] Shallow ceramic tray is distinctly oval with rim and elevated feet
  - [a3] Mounded moss carpet covers the tray surface around the rock
  - [a4] Rock is visible with exposed roots tightly gripping and wrapping around its body
  - [a5] Main trunk has noticeable taper from base to top with dynamic twist/curves
  - [a6] At least three clearly separated, cloud-like foliage pads at distinct height levels
  - [must1] Includes: twisted trunk with taper
  - [must2] Includes: at least three distinct foliage pads
  - [must3] Includes: exposed roots gripping a rock
  - [must4] Includes: shallow oval tray
  - [must5] Includes: moss surface
"""
import bpy
from mathutils import Vector

from parts.ceramic_tray import build_ceramic_tray
from parts.soil_and_moss import build_soil_and_moss
from parts.rock import build_rock
from parts.exposed_roots import build_exposed_roots
from parts.twisted_trunk import build_twisted_trunk
from parts.lower_foliage_pad import build_lower_foliage_pad
from parts.middle_foliage_pad import build_middle_foliage_pad
from parts.apex_foliage_pad import build_apex_foliage_pad


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
    build_ceramic_tray()
    build_soil_and_moss()
    build_rock()
    build_exposed_roots()
    build_twisted_trunk()
    build_lower_foliage_pad()
    build_middle_foliage_pad()
    build_apex_foliage_pad()
    _selfcheck()


main()
