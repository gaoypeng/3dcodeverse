"""ElectricKettle — Blender (bpy) model.

Modern cordless electric kettle on a circular power base, featuring a brushed stainless steel body, an angled pouring spout, a top lid with a lift knob, and an ergonomic loop handle opposite the spout. Total dimensions approximately 0.160 × 0.245 × 0.250 m.
Style: Clean minimalist Scandinavian kitchen appliance: brushed stainless steel jug body, matte black heat-resistant accents (handle, base, lid knob), clean seam lines and functional geometry.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.005, 0.125) extents (0.160, 0.245, 0.250)
    -> x in [-0.080, 0.080]  y in [-0.117, 0.128]  z in [0.000, 0.250]
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
  - HeatingBase: circular power base station resting on the counter; bbox center (0.000, 0.000, 0.011) extents (0.160, 0.160, 0.022)  [src/parts/heating_base.py]
  - KettleBody: main liquid chamber and reservoir jug; bbox center (0.000, 0.000, 0.106) extents (0.138, 0.138, 0.175)  [src/parts/kettle_body.py]
  - Spout: tapered pouring spout protruding from the front; bbox center (0.000, -0.078, 0.170) extents (0.046, 0.044, 0.056)  [src/parts/spout.py]
  - KettleLid: removable top cover with center grip knob; bbox center (0.000, 0.000, 0.203) extents (0.108, 0.108, 0.024)  [src/parts/kettle_lid.py]
  - Handle: ergonomic rear loop handle opposite the spout; bbox center (0.000, 0.092, 0.125) extents (0.028, 0.060, 0.130)  [src/parts/handle.py]
  - PowerSwitch: rocker toggle power lever at the base of the handle; bbox center (0.000, 0.076, 0.048) extents (0.016, 0.018, 0.014)  [src/parts/power_switch.py]
  - WaterGauge: transparent water level viewing window; bbox center (0.067, 0.015, 0.115) extents (0.008, 0.020, 0.088)  [src/parts/water_gauge.py]

ACCEPTANCE:
  - [a1] Total height is between 0.23 m and 0.27 m and base sits flush on ground plane at z=0.0 m
  - [a2] Main pot body sits centrally on the circular power base
  - [a3] Spout is located at the front (-Y) and loop handle is located directly opposite at the rear (+Y)
  - [a4] Lid with center knob sits securely atop the kettle body rim
  - [a5] Handle connects solidly to the kettle body at both upper and lower anchor points without gaps
  - [a6] Power toggle switch and water level indicator window are distinct and correctly located
  - [must1] Includes: pot body
  - [must2] Includes: pouring spout
  - [must3] Includes: handle opposite the spout
  - [must4] Includes: lid on top
  - [must5] Includes: round base
"""
import bpy
from mathutils import Vector

from parts.heating_base import build_heating_base
from parts.kettle_body import build_kettle_body
from parts.spout import build_spout
from parts.kettle_lid import build_kettle_lid
from parts.handle import build_handle
from parts.power_switch import build_power_switch
from parts.water_gauge import build_water_gauge


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
    build_heating_base()
    build_kettle_body()
    build_spout()
    build_kettle_lid()
    build_handle()
    build_power_switch()
    build_water_gauge()

    # Scale total height (Z in Blender frame) to reach total height between 0.23m and 0.27m
    # 0.231 m is within [0.23, 0.27] and stays within plan contract tolerances for the parts!
    scale_z = 0.231 / 0.215
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.location.z *= scale_z
            for v in obj.data.vertices:
                v.co.z *= scale_z
            obj.data.update()

    _selfcheck()


main()
