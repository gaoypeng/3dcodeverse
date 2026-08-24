"""PottedPalm — Blender (bpy) model.

Small indoor potted areca/parlor palm in a ceramic pot, standing 0.64 m tall with a textured fibrous trunk and eight graceful, downward-arching pinnate fronds.
Style: Botanically accurate parlor/areca palm with a warm matte terracotta pot, textured brown ringed trunk, green crown core, and 8 radial pinnate fronds with downward-arching rachises and slender lanceolate leaflets.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.320) extents (0.650, 0.650, 0.640)
    -> x in [-0.325, 0.325]  y in [-0.325, 0.325]  z in [0.000, 0.640]
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
  - Pot: main ceramic planter holding the plant; bbox center (0.000, 0.000, 0.100) extents (0.220, 0.220, 0.200)  [src/parts/pot.py]
  - Soil: potting soil surface; bbox center (0.000, 0.000, 0.185) extents (0.195, 0.195, 0.020)  [src/parts/soil.py]
  - Trunk: short fibrous palm trunk; bbox center (0.000, 0.000, 0.260) extents (0.060, 0.060, 0.160)  [src/parts/trunk.py]
  - CrownShaft: green emergence point of frond stems; bbox center (0.000, 0.000, 0.360) extents (0.050, 0.050, 0.060)  [src/parts/crown_shaft.py]
  - FrondStem x8: central rachis spine for each frond; bbox center (0.180, 0.000, 0.480) extents (0.320, 0.040, 0.260)  [src/parts/frond_stem.py]
  - FrondLeaflets x8: pinnate leaflets along each frond rachis; bbox center (0.200, 0.000, 0.470) extents (0.350, 0.180, 0.280)  [src/parts/frond_leaflets.py]

ACCEPTANCE:
  - [a1] Overall height is between 0.60 m and 0.68 m, pot sitting firmly on z=0
  - [a2] Planter pot is present with visible soil fill inside the upper opening
  - [a3] Palm trunk has visible horizontal rings / fibrous texture
  - [a4] Exactly eight distinct fronds radiate from the central crown
  - [a5] Fronds arch outward and visibly curve downward at their outer ends
  - [a6] Each frond contains numerous individual slender leaflets along its stem
  - [must1] Includes: textured trunk
  - [must2] Includes: at least eight fronds
  - [must3] Includes: fronds arch downward
  - [must4] Includes: leaflets along each frond
  - [must5] Includes: pot
"""
import bpy
from mathutils import Vector

from parts.pot import build_pot
from parts.soil import build_soil
from parts.trunk import build_trunk
from parts.crown_shaft import build_crown_shaft
from parts.frond_stem import build_frond_stem
from parts.frond_leaflets import build_frond_leaflets


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
    build_pot()
    build_soil()
    build_trunk()
    build_crown_shaft()
    build_frond_stem()
    build_frond_leaflets()
    _selfcheck()


main()
