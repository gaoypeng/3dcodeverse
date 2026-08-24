"""PottedBarrelCactus — Blender (bpy) model.

A potted golden barrel cactus in a classic terracotta pot with saucer and soil surface, measuring approximately 0.21 x 0.21 x 0.365 m.
Style: Classic botanical desert houseplant. Warm matte terracotta earthenware container with thick lip, dark textured potting mix, and a distinctively ribbed globose barrel cactus topped with golden spine clusters and a pale woolly apical crown.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.183) extents (0.210, 0.210, 0.365)
    -> x in [-0.105, 0.105]  y in [-0.105, 0.105]  z in [0.001, 0.365]
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
  - Saucer: drainage saucer tray; bbox center (0.000, 0.000, 0.013) extents (0.180, 0.180, 0.025)  [src/parts/saucer.py]
  - PotBody: main conical pot chamber; bbox center (0.000, 0.000, 0.090) extents (0.185, 0.185, 0.150)  [src/parts/pot_body.py]
  - PotRim: projecting top collar of pot; bbox center (0.000, 0.000, 0.180) extents (0.210, 0.210, 0.040)  [src/parts/pot_rim.py]
  - Soil: potting soil substrate; bbox center (0.000, 0.000, 0.175) extents (0.178, 0.178, 0.020)  [src/parts/soil.py]
  - CactusBody: fleshy ribbed cactus stem; bbox center (0.000, 0.000, 0.260) extents (0.170, 0.170, 0.170)  [src/parts/cactus_body.py]
  - CactusCrown: woolly apical crown; bbox center (0.000, 0.000, 0.350) extents (0.060, 0.060, 0.025)  [src/parts/cactus_crown.py]
  - SpineRings: protective radial spines and areoles; bbox center (0.000, 0.000, 0.265) extents (0.185, 0.185, 0.150)  [src/parts/spine_rings.py]

ACCEPTANCE:
  - [a1] Overall height is between 0.34 m and 0.38 m, standing on z=0
  - [a2] Terracotta pot features distinct tapered lower body, upper overhanging rim collar, and drainage saucer
  - [a3] Soil surface is visible inside the pot rim surrounding the cactus base
  - [a4] Cactus body is rounded/globular with vertical ribbed valleys and ridges
  - [a5] Apex of the cactus displays a pale woolly crown tuft
  - [a6] Spine clusters are attached along the ribs radiating outwards in golden-yellow tone
  - [must1] Includes: ribbed round cactus body
  - [must2] Includes: terracotta pot with rim
  - [must3] Includes: soil surface
"""
import bpy
from mathutils import Vector

from parts.saucer import build_saucer
from parts.pot_body import build_pot_body
from parts.pot_rim import build_pot_rim
from parts.soil import build_soil
from parts.cactus_body import build_cactus_body
from parts.cactus_crown import build_cactus_crown
from parts.spine_rings import build_spine_rings


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
    build_saucer()
    build_pot_body()
    build_pot_rim()
    build_soil()
    build_cactus_body()
    build_cactus_crown()
    build_spine_rings()
    _selfcheck()


main()
