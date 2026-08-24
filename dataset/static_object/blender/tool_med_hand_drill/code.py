"""CordlessPowerDrill — Blender (bpy) model.

Modern cordless power drill with pistol grip ergonomics, slide-in lithium-ion battery base, rotatable torque selector, keyless chuck, fluted steel drill bit, and motor ventilation slots. Overall size is approximately 0.080 × 0.275 × 0.230 m.
Style: Industrial power tool styling with high-impact molded cyan/teal composite casing, black ergonomic rubber overmolding, textured keyless chuck, and crisp mechanical accents.

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center (0.000, -0.043, 0.115) extents (0.080, 0.275, 0.230)
    -> x in [-0.040, 0.040]  y in [-0.180, 0.095]  z in [0.000, 0.230]
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
  - MainHousing: Upper motor enclosure and internal gear casing; bbox center (0.000, 0.020, 0.190) extents (0.075, 0.150, 0.080)  [src/parts/main_housing.py]
  - PistolGrip: Ergonomic handle bridging motor housing to battery foot; bbox center (0.000, 0.025, 0.110) extents (0.045, 0.065, 0.120)  [src/parts/pistol_grip.py]
  - RubberOvermold: Textured rubber grip surface on handle and rear housing; bbox center (0.000, 0.042, 0.130) extents (0.050, 0.055, 0.140)  [src/parts/rubber_overmold.py]
  - BatteryPack: Detachable rechargeable battery base; bbox center (0.000, 0.015, 0.025) extents (0.080, 0.130, 0.050)  [src/parts/battery_pack.py]
  - Trigger: Variable-speed power trigger switch; bbox center (0.000, -0.018, 0.150) extents (0.020, 0.028, 0.035)  [src/parts/trigger.py]
  - TorqueCollar: Adjustable clutch selector ring; bbox center (0.000, -0.065, 0.190) extents (0.058, 0.030, 0.058)  [src/parts/torque_collar.py]
  - KeylessChuck: Toolless 3-jaw drill chuck; bbox center (0.000, -0.098, 0.190) extents (0.046, 0.046, 0.046)  [src/parts/keyless_chuck.py]
  - DrillBit: Twist drill bit accessory; bbox center (0.000, -0.150, 0.190) extents (0.008, 0.060, 0.008)  [src/parts/drill_bit.py]
  - SideVents x2: Motor heat dissipation louvers; bbox center (0.037, 0.035, 0.190) extents (0.004, 0.045, 0.026)  [src/parts/side_vents.py]

ACCEPTANCE:
  - [a1] Overall height is roughly 0.23 m and the battery pack rests firmly on the ground plane at z=0
  - [a2] Pistol grip connects the upper horizontal motor housing down to the battery foot block
  - [a3] Red variable-speed trigger switch is situated within the ergonomic crook beneath the motor housing
  - [a4] Keyless chuck is mounted in front of the torque clutch ring with a fluted twist drill bit projecting forward (-Y)
  - [a5] Cooling ventilation slats are present symmetrically on both left and right flanks of the motor body
  - [a6] Overlapping contact verified between all joined parts with no floating components
  - [must1] Includes: pistol grip body
  - [must2] Includes: trigger
  - [must3] Includes: battery block under grip
  - [must4] Includes: chuck with a bit
  - [must5] Includes: side vents
"""
import bpy
from mathutils import Vector

from parts.main_housing import build_main_housing
from parts.pistol_grip import build_pistol_grip
from parts.rubber_overmold import build_rubber_overmold
from parts.battery_pack import build_battery_pack
from parts.trigger import build_trigger
from parts.torque_collar import build_torque_collar
from parts.keyless_chuck import build_keyless_chuck
from parts.drill_bit import build_drill_bit
from parts.side_vents import build_side_vents


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
    build_main_housing()
    build_pistol_grip()
    build_rubber_overmold()
    build_battery_pack()
    build_trigger()
    build_torque_collar()
    build_keyless_chuck()
    build_drill_bit()
    build_side_vents()
    _selfcheck()


main()
