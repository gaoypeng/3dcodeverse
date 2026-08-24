"""AlphabetBlockTower — Blender (bpy) model.

A playful vertical stack of five classic wooden alphabet blocks of progressively decreasing size, arranged in a gently twisted tower. The entire assembly stands 0.342 m high with beveled wooden edges and colorful letter panels.
Style: Traditional wooden toy aesthetic: natural pine core with rounded/beveled edges (2-3 mm bevel), recessed face panels with painted primary and secondary lacquer finishes, and raised embossed serif capital letters on all vertical faces.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.171) extents (0.106, 0.106, 0.342)
    -> x in [-0.053, 0.053]  y in [-0.053, 0.053]  z in [0.000, 0.342]
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
  - BlockBottom: largest foundation cube at base; bbox center (0.000, 0.000, 0.050) extents (0.100, 0.100, 0.100)  [src/parts/block_bottom.py]
  - BlockLowerMid: second alphabet block from bottom; bbox center (0.000, 0.000, 0.141) extents (0.103, 0.103, 0.085)  [src/parts/block_lower_mid.py]
  - BlockMiddle: middle alphabet block; bbox center (0.000, 0.000, 0.216) extents (0.095, 0.095, 0.070)  [src/parts/block_middle.py]
  - BlockUpperMid: fourth alphabet block from bottom; bbox center (0.000, 0.000, 0.277) extents (0.077, 0.077, 0.055)  [src/parts/block_upper_mid.py]
  - BlockTop: smallest topmost alphabet block; bbox center (0.000, 0.000, 0.322) extents (0.046, 0.046, 0.040)  [src/parts/block_top.py]

ACCEPTANCE:
  - [a1] Total height of stacked tower is 0.342 m ± 0.010 m and bottom sits directly on ground z=0
  - [a2] Exactly five distinct cubic alphabet blocks are present and stacked vertically
  - [a3] Cube side lengths decrease monotonically upward from 0.100 m down to 0.040 m
  - [a4] Each successive block is visibly rotated along the vertical Z axis creating a twisted spiral effect
  - [a5] Every upper block touches and seats solidly onto the top surface of the block below without gaps
  - [a6] Each block features beveled edges, framed borders, and distinct color-coded embossed letter details
  - [must1] Includes: five cubes
  - [must2] Includes: decreasing size upward
  - [must3] Includes: each slightly rotated
  - [must4] Includes: all stacked touching
"""
import bpy
from mathutils import Vector

from parts.block_bottom import build_block_bottom
from parts.block_lower_mid import build_block_lower_mid
from parts.block_middle import build_block_middle
from parts.block_upper_mid import build_block_upper_mid
from parts.block_top import build_block_top


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
    build_block_bottom()
    build_block_lower_mid()
    build_block_middle()
    build_block_upper_mid()
    build_block_top()
    _selfcheck()


main()
