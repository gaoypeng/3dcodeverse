"""ToySteamLocomotive — Blender (bpy) model.

Classic toy steam locomotive and coal tender measuring approximately 0.20  0.84  0.28 m. Features a rounded boiler, cab with cutouts, cowcatcher, six spoked driving wheels with side rods, and a four-wheeled tender car with a mound of coal.
Style: Charming vintage toy styling with robust geometric shapes, bold primary/satin painted finishes, brass accents on the chimney and dome, and mechanical drive linkages.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.140) extents (0.200, 0.840, 0.280)
    -> x in [-0.100, 0.100]  y in [-0.420, 0.420]  z in [0.000, 0.280]
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
  - LocomotiveChassis: main structural frame of the locomotive engine; bbox center (0.000, -0.140, 0.075) extents (0.150, 0.480, 0.030)  [src/parts/locomotive_chassis.py]
  - Boiler: cylindrical steam boiler and steam dome; bbox center (0.000, -0.180, 0.155) extents (0.130, 0.320, 0.130)  [src/parts/boiler.py]
  - Smokestack: vertical exhaust chimney with flared rim; bbox center (0.000, -0.290, 0.235) extents (0.050, 0.050, 0.090)  [src/parts/smokestack.py]
  - Cowcatcher: wedge-shaped front track clearing grill; bbox center (0.000, -0.395, 0.055) extents (0.160, 0.070, 0.070)  [src/parts/cowcatcher.py]
  - LocomotiveCab: driver cab cabin with arched windows and curved roof; bbox center (0.000, 0.045, 0.175) extents (0.160, 0.150, 0.170)  [src/parts/locomotive_cab.py]
  - LocomotiveWheel x6: large spoked driving wheels; bbox center (0.080, -0.140, 0.050) extents (0.025, 0.100, 0.100)  [src/parts/locomotive_wheel.py]
  - ConnectingRod x2: horizontal driving linkage rod connecting wheel hubs; bbox center (0.095, -0.140, 0.050) extents (0.010, 0.280, 0.015)  [src/parts/connecting_rod.py]
  - TenderChassis: coal wagon body hitched behind locomotive; bbox center (0.000, 0.285, 0.110) extents (0.150, 0.250, 0.140)  [src/parts/tender_chassis.py]
  - CoalLoad: mounded coal pile inside the tender; bbox center (0.000, 0.285, 0.165) extents (0.130, 0.210, 0.050)  [src/parts/coal_load.py]
  - TenderWheel x4: smaller flanged wheels for tender wagon; bbox center (0.075, 0.285, 0.035) extents (0.020, 0.070, 0.070)  [src/parts/tender_wheel.py]

ACCEPTANCE:
  - [a1] Total object bounding height is approx 0.28 m with base sitting at z = 0.0 m
  - [a2] Boiler is a cylindrical body mounted along the forward section of the locomotive
  - [a3] Smokestack chimney stands vertically near front of boiler with a flared rim
  - [a4] Locomotive cab sits behind the boiler with visible window cutouts and roof
  - [a5] V-shaped cowcatcher plow is mounted at the frontmost end of the locomotive
  - [a6] Locomotive has exactly 6 driving wheels connected with side horizontal linkage rods
  - [a7] Coal tender follows behind with 4 wheels and a visible mounded coal load in its hopper
  - [must1] Includes: cylindrical boiler
  - [must2] Includes: smokestack
  - [must3] Includes: cab
  - [must4] Includes: cowcatcher
  - [must5] Includes: six large wheels with rods
  - [must6] Includes: separate tender with coal load and four wheels
"""
import bpy
from mathutils import Vector

from parts.locomotive_chassis import build_locomotive_chassis
from parts.boiler import build_boiler
from parts.smokestack import build_smokestack
from parts.cowcatcher import build_cowcatcher
from parts.locomotive_cab import build_locomotive_cab
from parts.locomotive_wheel import build_locomotive_wheel
from parts.connecting_rod import build_connecting_rod
from parts.tender_chassis import build_tender_chassis
from parts.coal_load import build_coal_load
from parts.tender_wheel import build_tender_wheel


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
    build_locomotive_chassis()
    build_boiler()
    build_smokestack()
    build_cowcatcher()
    build_locomotive_cab()
    build_locomotive_wheel()
    build_connecting_rod()
    build_tender_chassis()
    build_coal_load()
    build_tender_wheel()
    _selfcheck()


main()
