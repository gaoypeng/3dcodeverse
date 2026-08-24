"""RetroToaster — Blender (bpy) model.

Classic 1950s-style two-slot retro toaster with curved polished chrome body, side lever mechanism, rotary browning dial, and four rubber feet. Measures approximately 0.310 × 0.185 × 0.195 m.
Style: Mid-century aerodynamic retro styling: smooth dome-curved chrome housing, horizontal accent ridges, bakelite/plastic accents on lever knob and base, clean dual slots.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, 0.000, 0.098) extents (0.310, 0.185, 0.195)
    -> x in [-0.155, 0.155]  y in [-0.092, 0.092]  z in [0.001, 0.196]
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
  - BaseChassis: Molded heat-resistant plastic base plate supporting internal electronics and body shell; bbox center (0.000, 0.000, 0.022) extents (0.270, 0.176, 0.024)  [src/parts/base_chassis.py]
  - Foot x4: Non-slip rubber support feet; bbox center (0.105, -0.065, 0.005) extents (0.022, 0.022, 0.010)  [src/parts/foot.py]
  - BodyMain: Main curved outer toaster housing; bbox center (0.000, 0.000, 0.110) extents (0.260, 0.165, 0.160)  [src/parts/body_main.py]
  - TopTrim: Top chrome slot plate frame; bbox center (0.000, 0.000, 0.188) extents (0.230, 0.140, 0.008)  [src/parts/top_trim.py]
  - SlotLiners x2: Internal metal heating chamber slot guides; bbox center (0.000, -0.032, 0.135) extents (0.140, 0.030, 0.100)  [src/parts/slot_liners.py]
  - LeverSlideAssembly: Side carriage plunge lever and slider track; bbox center (0.148, 0.000, 0.130) extents (0.036, 0.030, 0.080)  [src/parts/lever_slide_assembly.py]
  - BrowningDial: Rotary timer/browning control knob; bbox center (0.000, -0.088, 0.055) extents (0.034, 0.016, 0.034)  [src/parts/browning_dial.py]
  - ControlButtons: Auxiliary function push buttons (Defrost / Cancel / Reheat); bbox center (0.000, -0.086, 0.085) extents (0.060, 0.010, 0.014)  [src/parts/control_buttons.py]
  - CrumbTrayHandle: Pull-out crumb tray lip/handle; bbox center (-0.138, 0.000, 0.018) extents (0.012, 0.065, 0.010)  [src/parts/crumb_tray_handle.py]

ACCEPTANCE:
  - [a1] Overall height sits between 0.18 m and 0.22 m, with feet resting at z=0 ± 0.001 m
  - [a2] Two distinct parallel bread slots on top face with clear internal cavity depth
  - [a3] Main body is smoothly rounded and bulbous with polished reflective chrome finish
  - [a4] Plunge lever mechanism extends outward from the right (+X) side wall
  - [a5] Circular rotary browning dial knob is mounted on the front (-Y) face
  - [a6] Four distinct support feet lift the plastic base slightly off the ground plane
  - [a7] Crumb tray pull handle visible along the lower chassis perimeter
  - [must1] Includes: two bread slots on top
  - [must2] Includes: side lever
  - [must3] Includes: dial knob
  - [must4] Includes: rounded body
  - [must5] Includes: small feet
"""
import bpy
from mathutils import Vector

from parts.base_chassis import build_base_chassis
from parts.foot import build_foot
from parts.body_main import build_body_main
from parts.top_trim import build_top_trim
from parts.slot_liners import build_slot_liners
from parts.lever_slide_assembly import build_lever_slide_assembly
from parts.browning_dial import build_browning_dial
from parts.control_buttons import build_control_buttons
from parts.crumb_tray_handle import build_crumb_tray_handle


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
    build_base_chassis()
    build_foot()
    build_body_main()
    build_top_trim()
    build_slot_liners()
    build_lever_slide_assembly()
    build_browning_dial()
    build_control_buttons()
    build_crumb_tray_handle()
    _selfcheck()


main()
