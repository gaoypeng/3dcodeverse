"""DeskBrassTelescope — Blender (bpy) model.

Antique nautical tabletop refractor telescope on an extendable wooden tripod base. Features polished brass barrel, finder rings, altitude pivot mount, and a triangular splayed wood and brass tripod standing 0.42 m tall.
Style: Victorian nautical/scientific aesthetic: rich polished brass with subtle patina, warm dark mahogany legs, knurled adjustment thumbscrews, and classic stepped refractor barrel proportions.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, -0.020, 0.210) extents (0.320, 0.380, 0.420)
    -> x in [-0.160, 0.160]  y in [-0.210, 0.170]  z in [0.000, 0.420]
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
  - TripodLeg x3: splayed wooden support legs with brass fittings; bbox center (0.090, 0.050, 0.140) extents (0.220, 0.220, 0.280)  [src/parts/tripod_leg.py]
  - TripodSpreader: brass stabilizing arm brace connecting the three legs; bbox center (0.000, 0.000, 0.100) extents (0.180, 0.180, 0.020)  [src/parts/tripod_spreader.py]
  - TripodHub: central brass tripod head and column mounting base; bbox center (0.000, 0.000, 0.280) extents (0.060, 0.060, 0.035)  [src/parts/tripod_hub.py]
  - AzimuthColumn: vertical brass riser post and rotational spindle; bbox center (0.000, 0.000, 0.320) extents (0.035, 0.035, 0.060)  [src/parts/azimuth_column.py]
  - AltitudeCradle: U-shaped pivot yoke holding the telescope barrel; bbox center (0.000, 0.000, 0.365) extents (0.080, 0.040, 0.050)  [src/parts/altitude_cradle.py]
  - MainTelescopeTube: primary brass optical barrel; bbox center (0.000, -0.020, 0.380) extents (0.050, 0.240, 0.060)  [src/parts/main_telescope_tube.py]
  - ObjectiveLensAssembly: front objective lens flare housing and sunshade hood; bbox center (0.000, -0.170, 0.400) extents (0.046, 0.070, 0.050)  [src/parts/objective_lens_assembly.py]
  - EyepieceAssembly: rear focusing draw-tube and viewer ocular cup; bbox center (0.000, 0.130, 0.360) extents (0.032, 0.090, 0.036)  [src/parts/eyepiece_assembly.py]

ACCEPTANCE:
  - [a1] Overall height stands between 0.40 m and 0.44 m above the ground plane.
  - [a2] All 3 tripod leg tips contact the ground plane at z=0 without floating or penetration.
  - [a3] Telescope barrel is positioned along the Y axis pointing forward (-Y) on top of the brass pivot yoke.
  - [a4] Three mahogany legs are evenly spaced at 120 degrees with visible brass end caps.
  - [a5] Stepped objective lens at the front and narrower eyepiece draw-tube at the back are distinctly visible.
  - [a6] Tripod spreader brace physically connects between the three legs below the central hub.
"""
import bpy
from mathutils import Vector

from parts.tripod_leg import build_tripod_leg
from parts.tripod_spreader import build_tripod_spreader
from parts.tripod_hub import build_tripod_hub
from parts.azimuth_column import build_azimuth_column
from parts.altitude_cradle import build_altitude_cradle
from parts.main_telescope_tube import build_main_telescope_tube
from parts.objective_lens_assembly import build_objective_lens_assembly
from parts.eyepiece_assembly import build_eyepiece_assembly


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
    build_tripod_leg()
    build_tripod_spreader()
    build_tripod_hub()
    build_azimuth_column()
    build_altitude_cradle()
    build_main_telescope_tube()
    build_objective_lens_assembly()
    build_eyepiece_assembly()
    _selfcheck()


main()
