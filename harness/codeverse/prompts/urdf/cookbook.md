# URDF + bpy cookbook — articulated objects (`urdf_blender`)

Every `python` snippet runs as-is in `blender -b --factory-startup`; every `xml` snippet is
valid URDF (the harness test suite checks both).  Z-up, −Y front, meters, radians.
Use `read_cookbook(section="<heading>")` for one chapter.

## The frame recipe (memorise this, it is the whole trick)

```text
1. Build every link's mesh in WORLD coordinates at the ZERO configuration (closed/home).
2. One Blender object per link, obj.name == link name == plan part name.
3. Pick a PIVOT for every non-root link: a point on its joint axis, in world coords
   (hinge line, slide axis, axle).  Root pivot = (0, 0, 0).
4. URDF: every joint has rpy="0 0 0";  origin xyz = pivot_child − pivot_parent;
   axis xyz = the joint axis in WORLD coords;  every visual origin = "0 0 0".
5. The harness exports meshes/<link>.glb already shifted by −pivot_link, so a link's
   visual mesh is simply its world mesh in a frame parented at its pivot.
```

Pivot arithmetic is plain vector subtraction — do it in Python and print it:

```python
import bpy, bmesh, math
from mathutils import Vector

PIVOT = {                          # world-space pivots, filled while building
    "Base": Vector((0, 0, 0)),
}
def urdf_origin(child: str, parent: str) -> str:
    d = PIVOT[child] - PIVOT[parent]
    return f'<origin xyz="{d.x:.4f} {d.y:.4f} {d.z:.4f}" rpy="0 0 0"/>'
```

## Skeleton (`src/model.py` for articulated objects)

```python
def link_obj(o):
    bpy.context.scene.collection.objects.link(o); return o

def make_box(name, size, center):
    """Axis-aligned box; size = full extents, centre in world."""
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); o.location = center
    return link_obj(o)

def make_cyl(name, radius, depth, center, axis='Z', segments=32):
    """Cylinder along `axis` ('X'|'Y'|'Z'), centred at `center` (wheels: axis 'X')."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius, radius2=radius, depth=depth)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); o.location = center
    if axis == 'X': o.rotation_euler = (0, math.radians(90), 0)
    elif axis == 'Y': o.rotation_euler = (math.radians(90), 0, 0)
    return link_obj(o)

def join_as(name, objs):
    """Several pieces → ONE link object (mesh islands are fine, but weld them ≥ 2 mm)."""
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs: o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    objs[0].name = name
    return objs[0]

def material(obj, rgb, rough=0.5, metal=0.0):
    m = bpy.data.materials.new(obj.name + "Mat")
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1); b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    obj.data.materials.append(m)
```

## Worked example 0: cabinet (door revolute + handle fixed + drawer prismatic)

```python
W, D, H, T = 0.50, 0.40, 0.70, 0.018      # cabinet outer size + panel thickness (m)
DOOR_T, GAP = 0.018, 0.002                # front panel thickness, clearance
FRONT_Y = -D / 2 - DOOR_T / 2 - 0.001     # front panels sit 1 mm proud of the carcass
DRAWER_Z0, DRAWER_Z1 = 0.021, 0.156       # drawer box bottom/top (4 mm under the shelf)
SHELF_Z0, DOOR_Z0 = 0.160, 0.184          # shelf above the drawer; door bottom edge

# --- Carcass (root): 6 panels joined into ONE link object; opening faces -Y (front)
panels = [
    make_box("Carcass", (T, D, H), (-(W / 2 - T / 2), 0, H / 2)),            # left side
    make_box("Carcass_R", (T, D, H), ((W / 2 - T / 2), 0, H / 2)),           # right side
    make_box("Carcass_Top", (W - 2 * T, D, T), (0, 0, H - T / 2)),
    make_box("Carcass_Floor", (W - 2 * T, D, T), (0, 0, T / 2)),
    make_box("Carcass_Shelf", (W - 2 * T, D - T, T), (0, -T / 2, SHELF_Z0 + T / 2)),
    make_box("Carcass_Back", (W - 2 * T, T, H - 2 * T), (0, D / 2 - T / 2, H / 2)),
]
carcass = join_as("Carcass", panels); material(carcass, (0.72, 0.68, 0.62))
PIVOT["Carcass"] = Vector((0, 0, 0))

# --- Door: hinged on its LEFT edge, closed at q = 0; +q about -Z swings it open (to -Y)
door_h = H - DOOR_Z0 - GAP
door = make_box("Door", (W - 2 * GAP, DOOR_T, door_h), (0, FRONT_Y, DOOR_Z0 + door_h / 2))
material(door, (0.55, 0.35, 0.2))
PIVOT["Door"] = Vector((-(W / 2 - GAP), FRONT_Y, 0))        # vertical hinge line (z arbitrary → 0)

# --- DoorHandle: vertical bar on the door's free side, sunk 2 mm into the door (weld)
handle = make_box("DoorHandle", (0.012, 0.034, 0.12), (W / 2 - 0.05, FRONT_Y - DOOR_T / 2 - 0.015, 0.45))
material(handle, (0.8, 0.78, 0.74), 0.3, 1.0)
PIVOT["DoorHandle"] = Vector((W / 2 - 0.05, FRONT_Y - DOOR_T / 2 - 0.015, 0.45))

# --- Drawer: box + front panel joined; slides out towards -Y (front)
hb = DRAWER_Z1 - DRAWER_Z0
drawer = join_as("Drawer", [
    make_box("Drawer", (W - 2 * T - 2 * GAP, 0.382, hb), (0, -0.011, DRAWER_Z0 + hb / 2)),
    make_box("Drawer_Front", (W - 2 * GAP, DOOR_T, DOOR_Z0 - 0.005), (0, FRONT_Y, (0.002 + DOOR_Z0 - 0.003) / 2)),
])
material(drawer, (0.55, 0.35, 0.2))
PIVOT["Drawer"] = Vector((0, -0.011, DRAWER_Z0 + hb / 2))    # any point on the slide axis

for child, parent in (("Door", "Carcass"), ("DoorHandle", "Door"), ("Drawer", "Carcass")):
    print(child, urdf_origin(child, parent))
```

```xml
<?xml version="1.0"?>
<robot name="Cabinet">
  <link name="Carcass"><visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/Carcass.glb"/></geometry></visual></link>
  <link name="Door"><visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/Door.glb"/></geometry></visual></link>
  <link name="DoorHandle"><visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/DoorHandle.glb"/></geometry></visual></link>
  <link name="Drawer"><visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/Drawer.glb"/></geometry></visual></link>
  <!-- hinge on the left front edge; +q about -Z swings the free edge to -Y (open) -->
  <joint name="CarcassToDoor" type="revolute">
    <parent link="Carcass"/><child link="Door"/>
    <origin xyz="-0.248 -0.210 0" rpy="0 0 0"/>
    <axis xyz="0 0 -1"/>
    <limit lower="0" upper="1.6" effort="10" velocity="1"/>
  </joint>
  <joint name="DoorToDoorHandle" type="fixed">
    <parent link="Door"/><child link="DoorHandle"/>
    <origin xyz="0.448 -0.024 0.45" rpy="0 0 0"/>
  </joint>
  <!-- +q pulls the drawer out towards the front (-Y) -->
  <joint name="CarcassToDrawer" type="prismatic">
    <parent link="Carcass"/><child link="Drawer"/>
    <origin xyz="0 -0.011 0.0885" rpy="0 0 0"/>
    <axis xyz="0 -1 0"/>
    <limit lower="0" upper="0.30" effort="10" velocity="0.5"/>
  </joint>
</robot>
```

`joint_sweep` sanity: at door q = 1.2 the free edge is well in front (y < −0.5); at
drawer q = 0.3 the drawer front sits 0.3 m out; nothing collides at any sampled q.

## Worked example 1: laptop (base + lid hinge + two-part screen)

```python
BW, BD, BT = 0.33, 0.23, 0.018          # base footprint + thickness
LT, GAP = 0.006, 0.002                  # lid thickness, hinge clearance

# --- Base link (root) ---------------------------------------------------------
base = make_box("Base", (BW, BD, BT), (0, 0, BT / 2))
keys = make_box("Base_Keys", (BW - 0.04, BD * 0.45, 0.002), (0, -0.02, BT + 0.001 - 0.0005))  # 0.5 mm sunk
pad = make_box("Base_Pad", (0.10, 0.06, 0.001), (0, -BD / 2 + 0.045, BT + 0.0005 - 0.0003))
base = join_as("Base", [base, keys, pad]); material(base, (0.15, 0.15, 0.17), 0.4, 0.6)
PIVOT["Base"] = Vector((0, 0, 0))

# --- Lid link: hinge axis along X, 8 mm in from the back edge, 6.5 mm above the base top.
#     The hinge barrel belongs to the LID (it turns with it); closed (lying flat) at q = 0.
hinge_y, hinge_z = BD / 2 - 0.008, BT + 0.0065
LID_L = BD - 0.008 - GAP                                    # plate runs from the axis to the front
lid = make_box("Lid", (BW, LID_L, LT), (0, hinge_y - LID_L / 2, hinge_z))
screen = make_box("Lid_Screen", (BW - 0.02, LID_L - 0.03, 0.0005), (0, hinge_y - LID_L / 2, hinge_z + LT / 2 - 0.0002))
barrel = make_cyl("Lid_Hinge", 0.006, BW * 0.9, (0, hinge_y, hinge_z), axis='X', segments=24)
lid = join_as("Lid", [lid, screen, barrel]); material(lid, (0.2, 0.2, 0.22), 0.3, 0.6)
PIVOT["Lid"] = Vector((0, hinge_y, hinge_z))

print("BaseToLid", urdf_origin("Lid", "Base"))     # → <origin xyz="0.0000 0.1070 0.0245" rpy="0 0 0"/>
```

Axis sign check: a lid point at (0, −0.1, 0) relative to the hinge rotated by +q about
+X goes to (0, −0.1 cos q, −0.1 sin q) → z NEGATIVE → that closes INTO the base.  So the
axis is **−X** (`axis xyz="-1 0 0"`), limits 0 … 2.1 rad (120°).  The lid plate sits 3.5 mm
above the base top at q = 0 (keys are 1 mm below it): clearance, not contact.

```xml
<?xml version="1.0"?>
<robot name="Laptop">
  <link name="Base"><visual><origin xyz="0 0 0" rpy="0 0 0"/>
    <geometry><mesh filename="meshes/Base.glb"/></geometry></visual></link>
  <link name="Lid"><visual><origin xyz="0 0 0" rpy="0 0 0"/>
    <geometry><mesh filename="meshes/Lid.glb"/></geometry></visual></link>
  <joint name="BaseToLid" type="revolute">
    <parent link="Base"/><child link="Lid"/>
    <origin xyz="0 0.107 0.0245" rpy="0 0 0"/>
    <axis xyz="-1 0 0"/>
    <limit lower="0" upper="2.1" effort="5" velocity="2"/>
  </joint>
</robot>
```

## Worked example 2: hand cart (continuous wheels + prismatic telescopic handle)

```python
CW, CD, CH = 0.45, 0.35, 0.03             # deck
WR, WT = 0.10, 0.03                         # wheel radius / width

POST_Y, POST_Z0, POST_H, POST_W, WALL = CD / 2 - 0.016, WR + CH, 0.60, 0.032, 0.003
deck = make_box("Deck", (CW, CD, CH), (0, 0, WR + CH / 2))         # deck rides above the axle height
# the handle post is a HOLLOW square tube (4 walls) so the telescopic grip can slide inside it
walls = [make_box(f"Deck_PostW{i}", s, c) for i, (s, c) in enumerate([
    ((POST_W, WALL, POST_H), (0, POST_Y - POST_W / 2 + WALL / 2, POST_Z0 + POST_H / 2)),
    ((POST_W, WALL, POST_H), (0, POST_Y + POST_W / 2 - WALL / 2, POST_Z0 + POST_H / 2)),
    ((WALL, POST_W - 2 * WALL, POST_H), (-POST_W / 2 + WALL / 2, POST_Y, POST_Z0 + POST_H / 2)),
    ((WALL, POST_W - 2 * WALL, POST_H), (POST_W / 2 - WALL / 2, POST_Y, POST_Z0 + POST_H / 2))])]
deck = join_as("Deck", [deck, *walls]); material(deck, (0.5, 0.33, 0.18))
PIVOT["Deck"] = Vector((0, 0, 0))

for i, sx in enumerate((-1, 1)):
    name = f"Wheel{i + 1}"
    cx = sx * (CW / 2 + WT / 2 + 0.002)                                   # 2 mm outside the deck side
    w = make_cyl(name, WR, WT, (cx, -CD / 2 + 0.06, WR), axis='X')
    cap = make_cyl(name + "_Cap", 0.02, 0.012, (cx + sx * (WT / 2 + 0.003), -CD / 2 + 0.06, WR), axis='X')
    w = join_as(name, [w, cap]); material(w, (0.05, 0.05, 0.05), 0.85)  # cap overlaps the wheel 3 mm (same link)
    PIVOT[name] = Vector((cx, -CD / 2 + 0.06, WR))                        # on the axle

# axle stubs belong to the Deck; they stop 1 mm short of each wheel's inner face (no penetration, "touching")
axle = make_cyl("Deck_Axle", 0.008, CW + 0.002, (0, -CD / 2 + 0.06, WR), axis='X', segments=16)
deck = join_as("Deck", [deck, axle])

# telescopic handle slides UP (+Z) inside the post: zero config = fully retracted.
# grip 24 mm square inside the 26 mm bore (1 mm clearance), bottom 70 mm above the deck, top 20 mm above the post
grip = make_box("Handle", (0.024, 0.024, 0.55), (0, POST_Y, POST_Z0 + 0.07 + 0.275))
bar = make_box("Handle_Bar", (0.30, 0.024, 0.024), (0, POST_Y, POST_Z0 + 0.07 + 0.55 + 0.012 - 0.004))  # 4 mm weld
handle = join_as("Handle", [grip, bar]); material(handle, (0.7, 0.7, 0.72), 0.3, 1.0)
PIVOT["Handle"] = Vector((0, POST_Y, POST_Z0 + POST_H / 2))            # any point on the slide axis

for child, parent in (("Wheel1", "Deck"), ("Wheel2", "Deck"), ("Handle", "Deck")):
    print(child, urdf_origin(child, parent))
```

```xml
<?xml version="1.0"?>
<robot name="HandCart">
  <link name="Deck"><visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/Deck.glb"/></geometry></visual></link>
  <link name="Wheel1"><visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/Wheel1.glb"/></geometry></visual></link>
  <link name="Wheel2"><visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/Wheel2.glb"/></geometry></visual></link>
  <link name="Handle"><visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/Handle.glb"/></geometry></visual></link>
  <joint name="DeckToWheel1" type="continuous">
    <parent link="Deck"/><child link="Wheel1"/>
    <origin xyz="-0.242 -0.115 0.1" rpy="0 0 0"/><axis xyz="1 0 0"/>
    <limit effort="1" velocity="20"/>
  </joint>
  <joint name="DeckToWheel2" type="continuous">
    <parent link="Deck"/><child link="Wheel2"/>
    <origin xyz="0.242 -0.115 0.1" rpy="0 0 0"/><axis xyz="1 0 0"/>
    <limit effort="1" velocity="20"/>
  </joint>
  <joint name="DeckToHandle" type="prismatic">
    <parent link="Deck"/><child link="Handle"/>
    <origin xyz="0 0.159 0.43" rpy="0 0 0"/><axis xyz="0 0 1"/>
    <limit lower="0" upper="0.35" effort="10" velocity="0.5"/>
  </joint>
</robot>
```

Why: wheels are cylinders along X (`axis='X'`), their pivot is the axle point; the
telescopic handle's zero config is retracted (inside the post), q pulls it up.

## Joint types, axes, limits (what to write)

| joint | use | `<axis>` sign rule | `<limit>` |
|---|---|---|---|
| `revolute` | doors, lids, hinges, levers, knobs with stops | +q = opens / lifts / the "interact" direction | `lower upper effort velocity` all required |
| `prismatic` | drawers, slides, telescopes, pistons, buttons | +q = pulls OUT / extends | `lower="0" upper=<travel>` |
| `continuous` | wheels, fans, spinners, casters' roll | right-hand about axis | `effort velocity` only (NO lower/upper) |
| `fixed` | handles, knobs, feet, labels on a moving link | — | none |

Plausible ranges (SI):

| mechanism | type | lower | upper | notes |
|---|---|---|---|---|
| cabinet / room door | revolute | 0 | 1.6–1.9 rad | axis vertical through the hinge edge |
| laptop / lid / chest lid | revolute | 0 | 1.6–2.1 rad | axis along the back edge |
| toilet seat / piano fallboard | revolute | 0 | 1.5 rad | |
| oven / dishwasher door | revolute | 0 | 1.57 rad | axis along the bottom edge |
| drawer | prismatic | 0 | 0.35–0.45 m (≈ 0.8 × depth) | axis −Y (front) |
| keyboard tray / telescopic | prismatic | 0 | 0.2–0.4 m | |
| button / key | prismatic | 0 | 0.002–0.01 m | axis into the body |
| wheel / caster roll | continuous | — | — | axis = axle (usually X) |
| caster swivel | continuous | — | — | vertical axis above the wheel |
| knob / dial | revolute or continuous | −2.6 | 2.6 rad | |
| lever / switch | revolute | 0 | 0.5–0.9 rad | |
| faucet handle | revolute | 0 | 1.57 rad | |
| scissor / tongs | revolute | 0 | 0.6–1.0 rad | one joint; the other leg is the root |
| robot arm joint | revolute | −2.9 | 2.9 rad | |
| gripper finger | prismatic | 0 | 0.04 m | |

The harness renders the showcase pose at the plan's `rest` (e.g. door 0.3 rad, drawer
0.12 m) so the articulation is visible — you still build q = 0.

## Clearances, rest pose, hardware

* Moving part vs housing: 1–3 mm all round over the WHOLE range (drawer box inside the
  cabinet: 2 mm each side, 4 mm above; door leaf vs frame: 2 mm).  Test mentally at
  `upper`: does the door corner sweep through the side panel?  Put the hinge ON the edge.
* Zero config must not self-intersect: a closed door touches the carcass face
  (flush, 0–1 mm), never sinks into it.
* Handles / knobs / pulls: `fixed` joint to the MOVING link, welded 2 mm into it, pivot =
  handle centre.  Hinge barrels and drawer runners belong to the housing link (or to the
  moving link) — they are geometry, not joints.
* One mechanism per joint.  Doubles (two doors, four wheels) = separate links with their
  own pivots; identical meshes are fine.
* Runners/rails for drawers: two thin boxes on the cabinet sides + two on the drawer box;
  drawer box bottom 3 mm above the rail.

## URDF skeleton (copy, then fill)

```xml
<?xml version="1.0"?>
<robot name="ObjectName">
  <!-- one <link> per Blender object, names identical -->
  <link name="Base">
    <visual><origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry><mesh filename="meshes/Base.glb"/></geometry></visual>
    <!-- optional but welcome: <inertial><mass value="5"/><inertia ixx="0.1" iyy="0.1" izz="0.1" ixy="0" ixz="0" iyz="0"/></inertial> -->
  </link>
  <link name="Child">
    <visual><origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry><mesh filename="meshes/Child.glb"/></geometry></visual>
  </link>
  <joint name="BaseToChild" type="revolute">
    <parent link="Base"/><child link="Child"/>
    <origin xyz="PX PY PZ" rpy="0 0 0"/>     <!-- pivot_child - pivot_parent -->
    <axis xyz="0 0 1"/>                        <!-- unit, WORLD direction, +q = open -->
    <limit lower="0" upper="1.6" effort="10" velocity="1"/>
  </joint>
</robot>
```

## Pitfalls (symptom → cause → fix)

1. **Door swings into the cabinet** → axis sign.  Flip the axis (`0 0 1` ↔ `0 0 -1`);
   never make `lower` negative to compensate.
2. **Child jumps when q changes / orbits around the wrong point** → pivot not on the
   hinge line: the pivot must be a point ON the axis (edge of the door, the axle), not
   the part's centre.
3. **Lid/door geometry shifted after export** → you wrote a non-zero visual `<origin>` or
   `rpy` on a joint.  Everything is `0 0 0`; the harness does the shifting.
4. **"link X has two parent joints" / "not connected to root"** → every non-root link is
   the child of exactly one joint; handles attach to the door, not to the base AND door.
5. **Penetration at upper limit** (`joint_sweep` says `Drawer` hits `Base` at q = 0.4) →
   travel too long or clearance zero.  Shorten `upper` to 0.8 × depth, add 2 mm gaps.
6. **Parts float** (`check_connectivity`): hinge barrels, runners, knobs not overlapping.
   Weld 2 mm.
7. **`continuous` with `<limit lower upper>`** or `revolute` without limits → lint error.
8. **Blender object names ≠ link names** (`Door.001`, `door`) → mesh not found.  Exact
   PascalCase, unique, no suffix; join sub-pieces with `join_as`.
9. **Object not on the ground** → wheels' bottom / feet at z = 0 in the zero config.
10. **Mesh scale in URDF** (`scale="0.001 …"`) → forbidden; author in metres.
11. **Non-unit axis** (`0 0 2`) → lint error; also avoid `0.7 0.7 0` unless intended.
12. **Zero config is the open pose** → renders at rest look closed/odd and collision at
    `lower` fails; build closed, let the harness open it.
13. **Articulation invisible in renders** → plan `rest` 0 on every joint; ask for a
    non-zero showcase (door 0.3 rad, drawer 0.12 m) — that is the plan's job, not geometry's.
14. **Handles built in world but parented to the wrong link** → their pivot must be
    subtracted from the DOOR pivot (`urdf_origin("Handle", "Door")`), not from the base.

## Self-check (before you call it done)

```python
def urdf_selfcheck(expected_links):
    names = sorted(o.name for o in bpy.data.objects if o.type == 'MESH')
    missing = [n for n in expected_links if n not in names]
    extra = [n for n in names if n not in expected_links]
    assert not missing and not extra, f"links mismatch: missing={missing} extra={extra}"
    assert all("." not in n for n in names), f"auto-suffixed: {[n for n in names if '.' in n]}"
    for n in expected_links:
        assert n in PIVOT, f"no pivot recorded for {n}"
    bpy.context.view_layer.update()
    zmin = min((o.matrix_world @ Vector(c)).z for o in bpy.data.objects if o.type == 'MESH' for c in o.bound_box)
    assert abs(zmin) < 0.002, f"lowest point z={zmin:.4f}"
    print("[selfcheck] links", names, "pivots ok")

urdf_selfcheck(["Carcass", "Door", "DoorHandle", "Drawer", "Base", "Lid",
                "Deck", "Wheel1", "Wheel2", "Handle"])
```

Then: `build` → `render_sheet` → `check_connectivity` → `joint_sweep` on EVERY joint
(look at q = lower / mid / upper renders: does the right part move, the right way, without
penetration?) → `check_contract`.
