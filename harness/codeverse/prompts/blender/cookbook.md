# Blender (bpy) cookbook — static objects, Blender 4.2–5.x headless

Every snippet below runs as-is in `blender -b --factory-startup` (they are executed in
order by the harness test suite).  Z-up, −Y front, meters, PascalCase object names.
Files: `src/model.py` (entry) + `src/parts/<snake>.py` (one `build_<snake>()` per plan part) —
see "File layout".  The harness inlines the relevant chapters into your prompts; the full
file is at `.3dcv/cookbook.md` in your workspace.

## File layout (multi-file: model.py + parts/<snake>.py)

The harness lays down one file per plan part so parts can be refined in parallel:

```text
src/model.py            ENTRY — imports the builders, calls them in plan order, self-checks
src/parts/seat.py       def build_seat() -> bpy.types.Object     (one per plan part, snake_case)
src/parts/leg.py        def build_leg()  -> Leg_0..Leg_3, each TOP-LEVEL (instances loop here; no parent Empty)
src/parts/_common.py    optional shared helpers (underscore = not a part; never required)
```

Rules that the lint enforces: the part file `src/parts/<snake>.py` must define
`build_<snake>()`; part files only DEFINE (no module-level `build_*()` / `main()` calls —
model.py calls each builder once); `src/` is on `sys.path`, so model.py does
`from parts.seat import build_seat` (or `import parts.seat`).  Build errors come back as
`src/parts/<file>.py:<line>`.  A complete verified 3-file example is in the contract.

A part file, fully self-contained (own helpers, plan numbers on top):

`src/parts/mug_body.py`
```py
import bpy, bmesh

MUG_R, MUG_H, WALL = 0.042, 0.095, 0.004          # plan numbers (metres)

def _link(obj):
    bpy.context.scene.collection.objects.link(obj); return obj

def build_mug_body() -> bpy.types.Object:
    """MugBody — cylinder on z=0, hollowed later with a boolean; returns the object at world pose."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=MUG_R, radius2=MUG_R, depth=MUG_H)
    me = bpy.data.meshes.new("MugBody"); bm.to_mesh(me); bm.free()
    body = bpy.data.objects.new("MugBody", me); body.location = (0, 0, MUG_H / 2)
    return _link(body)
```
`src/model.py`
```py
import bpy
from parts.mug_body import build_mug_body

def main() -> None:
    build_mug_body()
    assert all('.' not in o.name for o in bpy.data.objects), "auto-suffixed names"

main()

```

Small objects (1–2 parts) may keep everything in one `src/model.py`; the rest of this
cookbook is written that way.  Every snippet below assumes these helpers (define them once
per file that uses them — part files are self-contained):

```python
import bpy, bmesh, math, random
from mathutils import Vector, Matrix

random.seed(0)                              # deterministic everywhere

# ---- plan numbers (metres) --------------------------------------------------
MUG_R, MUG_H, WALL = 0.042, 0.095, 0.004
HANDLE_R, HANDLE_TUBE = 0.030, 0.007

# ---- helpers ----------------------------------------------------------------
def link(obj: bpy.types.Object) -> bpy.types.Object:
    """Link into the scene collection (data-API objects are not linked by default)."""
    bpy.context.scene.collection.objects.link(obj)
    return obj

def obj_from_bmesh(name: str, bm: bmesh.types.BMesh, location=(0, 0, 0)) -> bpy.types.Object:
    """bmesh → mesh → object, freed and linked.  Name is the PLAN part name."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free(); me.update()
    o = bpy.data.objects.new(name, me)
    o.location = location
    return link(o)

def activate(obj: bpy.types.Object) -> None:
    """Make `obj` the only selected + active object (needed before ANY bpy.ops)."""
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

def apply_modifiers(obj: bpy.types.Object) -> None:
    """Apply every modifier in stack order (headless-safe via temp_override)."""
    if obj.data.users > 1:                       # linked duplicates cannot be applied
        obj.data = obj.data.copy()
    for m in list(obj.modifiers):
        with bpy.context.temp_override(object=obj, active_object=obj,
                                       selected_objects=[obj], selected_editable_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=m.name)

def apply_transforms(obj: bpy.types.Object) -> None:
    """Bake location+rotation+scale into the mesh (all three explicit!)."""
    activate(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
```

## Primitives (data API, no operator context)

`bmesh.ops.create_*` never depends on selection/active object, so it is safe in loops.
Primitives are centred on their origin; translate with `location`.

```python
def make_box(name, size, location=(0, 0, 0)) -> bpy.types.Object:
    """Axis-aligned box, size=(sx, sy, sz) full extents, centred at `location`."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    return obj_from_bmesh(name, bm, location)

def make_cylinder(name, radius, depth, location=(0, 0, 0), segments=32, radius2=None, rings=1):
    """Cylinder (or cone when radius2 given) along local Z, centred at `location`.
    rings > 1 subdivides the length (needed before bending / lattice deforms)."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                          radius1=radius, radius2=radius if radius2 is None else radius2, depth=depth)
    if rings > 1:
        side = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-9]
        bmesh.ops.subdivide_edges(bm, edges=side, cuts=rings - 1, use_grid_fill=True)
    return obj_from_bmesh(name, bm, location)

def make_sphere(name, radius, location=(0, 0, 0), segments=32, rings=16):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    return obj_from_bmesh(name, bm, location)

def make_torus(name, major_r, minor_r, location=(0, 0, 0), major_seg=48, minor_seg=16):
    """Torus in the XY plane (operator version — fine headless, then we capture the object)."""
    bpy.ops.mesh.primitive_torus_add(major_radius=major_r, minor_radius=minor_r,
                                     major_segments=major_seg, minor_segments=minor_seg,
                                     location=location)
    o = bpy.context.active_object
    o.name = name
    return o

def make_tube(name, radius, wall, depth, location=(0, 0, 0), segments=48):
    """Hollow open tube: outer cylinder minus inner, via bmesh solidify of a shell."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=segments,
                          radius1=radius, radius2=radius, depth=depth)
    bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=wall)
    return obj_from_bmesh(name, bm, location)

# quick demo (the harness test runs this): a mug body + ring handle
body = make_cylinder("MugBody", MUG_R, MUG_H, (0, 0, MUG_H / 2), 48)
handle = make_torus("MugHandle", HANDLE_R, HANDLE_TUBE, (MUG_R + HANDLE_R - 0.004, 0, MUG_H * 0.55))
handle.rotation_euler = (math.radians(90), 0, 0)     # stand the ring up in the XZ plane
```

Why: operators (`bpy.ops.mesh.primitive_cube_add`) change selection, need a context and
use `size=2` by default (a 2 m cube!) — data-API primitives avoid all three traps.
Numbers: 24–48 segments for visible cylinders, 8–12 for thin rods/bolts.

## Origins and placement

```python
def set_origin_to_bottom(obj: bpy.types.Object) -> None:
    """Move the origin to the bottom-centre of the mesh (world pose unchanged)."""
    me = obj.data
    xs = [v.co.x for v in me.vertices]; ys = [v.co.y for v in me.vertices]; zs = [v.co.z for v in me.vertices]
    c = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
    me.transform(Matrix.Translation(-c))
    obj.matrix_world = obj.matrix_world @ Matrix.Translation(c)

def world_bbox(obj: bpy.types.Object):
    """(min, max) Vectors of the evaluated world-space bbox."""
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return (Vector(map(min, zip(*pts))), Vector(map(max, zip(*pts))))

def drop_to_ground(objs) -> None:
    """Translate the whole assembly so its lowest point is z = 0."""
    zmin = min(world_bbox(o)[0].z for o in objs)
    for o in objs:
        o.location.z -= zmin

set_origin_to_bottom(body)
print("mug bbox", world_bbox(body))
```

Why: `obj.dimensions` ignores rotation; `bound_box` is object-space — always go through
`matrix_world` and `view_layer.update()` after changing transforms.

## Modifiers (bevel, boolean, array, mirror, solidify, screw, subsurf)

Add with `obj.modifiers.new(name, TYPE)`, set properties, then `apply_modifiers(obj)`
when you need the final mesh (booleans, joins, exports all behave better applied).

```python
def add_bevel(obj, width=0.003, segments=3, angle_deg=30.0):
    """Rounded edges: the single cheapest realism upgrade.  width < half the thinnest wall."""
    b = obj.modifiers.new("Bevel", 'BEVEL')
    b.width = width; b.segments = segments
    b.limit_method = 'ANGLE'; b.angle_limit = math.radians(angle_deg)
    b.harden_normals = False
    return b

def boolean_cut(obj, cutter, solver='MANIFOLD') -> None:
    """obj -= cutter, applied, cutter deleted.  MANIFOLD (5.0+) is fast + robust on closed
    meshes; fall back to EXACT when inputs are not watertight.  Cutter must overshoot the
    face it pierces by ≥ 5 mm, never be coplanar with it."""
    m = obj.modifiers.new("Cut", 'BOOLEAN')
    m.operation = 'DIFFERENCE'; m.object = cutter
    solvers = m.bl_rna.properties["solver"].enum_items.keys()     # 4.2: FAST/EXACT; 5.0: +MANIFOLD
    m.solver = solver if solver in solvers else 'EXACT'
    try:
        apply_modifiers(obj)
    except RuntimeError:                          # MANIFOLD refused (non-manifold input)
        m = obj.modifiers.new("Cut", 'BOOLEAN'); m.operation = 'DIFFERENCE'; m.object = cutter
        m.solver = 'EXACT'; apply_modifiers(obj)
    bpy.data.objects.remove(cutter, do_unlink=True)

def boolean_union(obj, other) -> None:
    m = obj.modifiers.new("Union", 'BOOLEAN'); m.operation = 'UNION'; m.object = other
    m.solver = 'EXACT'; apply_modifiers(obj)
    bpy.data.objects.remove(other, do_unlink=True)

def add_array(obj, count, offset_xyz):
    a = obj.modifiers.new("Array", 'ARRAY'); a.count = count
    a.use_relative_offset = False; a.use_constant_offset = True
    a.constant_offset_displace = offset_xyz
    return a

def add_mirror(obj, axis='X', bisect=False):
    """Mirror across the OBJECT ORIGIN plane (put the origin on the symmetry plane!)."""
    m = obj.modifiers.new("Mirror", 'MIRROR')
    idx = 'XYZ'.index(axis)
    m.use_axis = [i == idx for i in range(3)]
    m.use_bisect_axis = [bisect and i == idx for i in range(3)]
    m.use_clip = True; m.merge_threshold = 0.0005
    return m

def add_solidify(obj, thickness=0.003, inward=True):
    s = obj.modifiers.new("Solidify", 'SOLIDIFY')
    s.thickness = thickness; s.offset = -1 if inward else 1; s.use_even_offset = True
    return s

def add_subsurf(obj, levels=1):
    s = obj.modifiers.new("Subsurf", 'SUBSURF'); s.levels = levels; s.render_levels = levels
    return s

# demo: hollow the mug with a boolean, bevel the rim, mirror a pair of feet
cutter = make_cylinder("MugCutter", MUG_R - WALL, MUG_H, (0, 0, MUG_H / 2 + WALL + 0.005), 48)
boolean_cut(body, cutter)
add_bevel(body, 0.0015, 2)
apply_modifiers(body)
foot = make_box("FootL", (0.02, 0.02, 0.01), (0, 0, 0.005))
foot.data.transform(Matrix.Translation((-0.03, 0, 0)))   # mesh offset so the origin stays on x=0
add_mirror(foot, 'X'); apply_modifiers(foot); foot.name = "Feet"
```

Why: `MANIFOLD` is ~10× faster and never produces slivers but needs watertight inputs;
`EXACT` is the universal fallback; never use `FLOAT`/`FAST` (self-intersections).
Array/mirror/screw results are *one object* — correct for repeated detail that belongs to one part.

## Screw and lathe (rotational parts: bottles, vases, knobs, threads)

```python
def lathe(name, profile_xz, segments=48, location=(0, 0, 0)) -> bpy.types.Object:
    """Revolve a polyline profile [(r, z), ...] about Z into a closed solid.
    Profile runs from the axis (r = 0) at the bottom up and back to the axis at the top."""
    bm = bmesh.new()
    verts = [bm.verts.new((r, 0.0, z)) for r, z in profile_xz]
    edges = [bm.edges.new((a, b)) for a, b in zip(verts, verts[1:])]   # spin needs EDGES
    bmesh.ops.spin(bm, geom=verts + edges, cent=(0, 0, 0), axis=(0, 0, 1),
                   angle=2 * math.pi, steps=segments, use_merge=True)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)   # weld the seam + axis points
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return obj_from_bmesh(name, bm, location)

bottle = lathe("Bottle", [(0.0, 0.0), (0.032, 0.0), (0.034, 0.010), (0.034, 0.16),
                          (0.014, 0.21), (0.014, 0.25), (0.0, 0.25)])
bottle.data.shade_smooth()

def add_screw_thread(profile_obj, turns=6, pitch=0.002, steps=24):
    """Thread from a small profile polygon placed at radius r: SCREW modifier."""
    s = profile_obj.modifiers.new("Screw", 'SCREW')
    s.angle = math.radians(360 * turns); s.screw_offset = pitch * turns
    s.steps = steps; s.render_steps = steps; s.iterations = 1; s.axis = 'Z'
    s.use_merge_vertices = True
    return s
```

Why: revolved profiles give smooth, correct silhouettes for anything round; one list of
(r, z) points is the whole shape.  Segments 48 for hero parts, 24 for small knobs.

## Curves → tubes, ropes, cables, rails

```python
def make_tube_along(name, points, radius, resolution=12, smooth=True) -> bpy.types.Object:
    """Round tube following `points` (world coords), converted to a real mesh."""
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'; cu.bevel_depth = radius; cu.bevel_resolution = resolution // 2
    cu.use_fill_caps = True; cu.resolution_u = 12
    sp = cu.splines.new('NURBS' if smooth else 'POLY')
    sp.points.add(len(points) - 1)
    for p, (x, y, z) in zip(sp.points, points):
        p.co = (x, y, z, 1.0)
    if smooth:
        sp.use_endpoint_u = True; sp.order_u = min(4, len(points))
    o = link(bpy.data.objects.new(name, cu))
    with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o],
                                   selected_editable_objects=[o]):
        bpy.ops.object.convert(target='MESH')
    bm = bmesh.new(); bm.from_mesh(o.data)                 # weld the bevel seam (else 1 island per strip)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(o.data); bm.free(); o.data.update()
    return o

cable = make_tube_along("Cable", [(0.05, 0, 0.30), (0.10, 0.02, 0.20), (0.18, 0.0, 0.08),
                                  (0.30, -0.03, 0.01)], radius=0.004)
```

Why: a NURBS curve with `bevel_depth` is the cheapest correct pipe/rope; convert to mesh
so the exporter sees geometry.  Radius 3–6 mm cables, 10–25 mm rails, 20–40 mm pipes.

## Deformation (bend, taper, lattice)

```python
def bend(obj, angle_deg, axis='X'):
    """Bend a Z-long part (pipe elbow, bow, hook) AROUND `axis` (X or Y, perpendicular to
    its length).  The bend starts at the OBJECT ORIGIN: put the origin at one end and
    give the mesh ≥ 16 rings along its length (make_cylinder(..., rings=24))."""
    d = obj.modifiers.new("Bend", 'SIMPLE_DEFORM'); d.deform_method = 'BEND'
    d.angle = math.radians(angle_deg); d.deform_axis = axis
    return d

def taper(obj, factor=0.5, axis='Z'):
    d = obj.modifiers.new("Taper", 'SIMPLE_DEFORM'); d.deform_method = 'TAPER'
    d.factor = factor; d.deform_axis = axis
    return d

def lattice_squash(obj, scale_xyz, points=3):
    """Free-form squash/stretch via a lattice fitted to the object's bbox."""
    lat = bpy.data.lattices.new(obj.name + "Lat")
    lat.points_u = lat.points_v = lat.points_w = points
    lo = link(bpy.data.objects.new(obj.name + "Lat", lat))
    lo.location = obj.location; lo.scale = obj.dimensions
    m = obj.modifiers.new("Lattice", 'LATTICE'); m.object = lo
    return lo, m

pipe = make_cylinder("Elbow", 0.012, 0.20, (0.2, 0.2, 0.0), 24, rings=24)
pipe.data.transform(Matrix.Translation((0, 0, 0.10)))   # mesh now spans z 0..0.2 from the origin
bend(pipe, 90, 'X'); apply_modifiers(pipe)              # quarter-circle elbow, r ≈ 0.127
```

Why: SIMPLE_DEFORM bends relative to the object origin and needs ≥ 16 loops along the
length; a centred origin gives a symmetric "C" (±angle/2), an end origin a clean arc.

## Subdivision + creases (smooth organic parts with sharp seams)

```python
def crease_edges(obj, predicate, value=1.0):
    """Set crease on edges where predicate(v1, v2) is True (data attribute API, 4.x+)."""
    me = obj.data
    attr = me.attributes.get("crease_edge") or me.attributes.new("crease_edge", 'FLOAT', 'EDGE')
    for e, slot in zip(me.edges, attr.data):
        v1, v2 = me.vertices[e.vertices[0]].co, me.vertices[e.vertices[1]].co
        slot.value = value if predicate(v1, v2) else 0.0

cushion = make_box("Cushion", (0.40, 0.40, 0.08), (0.6, 0, 0.04))
crease_edges(cushion, lambda a, b: abs(a.z - b.z) < 1e-6 and a.z > 0.0)   # keep the top rim crisp
add_subsurf(cushion, 2); apply_modifiers(cushion); cushion.data.shade_smooth()
```

Why: a box + subsurf 2 + creases = cushion/pillow/soft shell in four lines; without the
crease it becomes a blob.

## Materials (Principled BSDF; flat PBR survives GLB export)

GLB keeps ONLY base colour, metallic, roughness, emission, alpha and normal *textures*.
Procedural node trees (noise, wave) are NOT exported — use them only for your own
preview, and prefer flat PBR + geometry detail + vertex colours for anything judged.

```python
def make_pbr(name, rgb, roughness=0.5, metallic=0.0, emission=None, alpha=1.0):
    mat = bpy.data.materials.new(name)                  # node tree exists by default
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = 3.0
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        mat.surface_render_method = 'BLENDED'           # 4.2+; blend_method is gone
    return mat

PALETTE = {                                              # rgb 0-1 linear, roughness, metallic
    "oak":      ((0.55, 0.36, 0.18), 0.55, 0.0),
    "walnut":   ((0.28, 0.17, 0.09), 0.50, 0.0),
    "steel":    ((0.62, 0.63, 0.65), 0.30, 1.0),
    "brass":    ((0.83, 0.62, 0.28), 0.35, 1.0),
    "black_plastic": ((0.02, 0.02, 0.02), 0.45, 0.0),
    "white_ceramic": ((0.92, 0.92, 0.90), 0.15, 0.0),
    "fabric":   ((0.35, 0.40, 0.55), 0.95, 0.0),
    "glass":    ((0.85, 0.90, 0.95), 0.05, 0.0),         # + alpha 0.25
    "rubber":   ((0.05, 0.05, 0.05), 0.85, 0.0),
}
def assign(obj, key):
    rgb, rough, metal = PALETTE[key]
    obj.data.materials.append(make_pbr(f"{obj.name}_{key}", rgb, rough, metal))

assign(body, "white_ceramic"); assign(handle, "white_ceramic"); assign(bottle, "glass")

def vertex_color_by_height(obj, low_rgb, high_rgb, name="Col"):
    """Per-vertex colour gradient (exported to GLB as COLOR_0; set vertexColors in viewers)."""
    me = obj.data
    zs = [v.co.z for v in me.vertices]; z0, z1 = min(zs), max(zs) or 1e-6
    col = me.color_attributes.new(name, 'FLOAT_COLOR', 'POINT')
    for v, slot in zip(me.vertices, col.data):
        t = (v.co.z - z0) / max(z1 - z0, 1e-6)
        slot.color = tuple(a + (b - a) * t for a, b in zip(low_rgb, high_rgb)) + (1.0,)

vertex_color_by_height(bottle, (0.2, 0.5, 0.2), (0.8, 0.9, 0.8))

# procedural wood (preview-only; judge renders via GLB will show the flat base colour)
def make_wood_nodes(name="WoodProcedural"):
    mat = bpy.data.materials.new(name); nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    wave = nt.nodes.new("ShaderNodeTexWave"); wave.inputs["Scale"].default_value = 40.0
    wave.inputs["Distortion"].default_value = 6.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.45, 0.28, 0.13, 1); ramp.color_ramp.elements[1].color = (0.62, 0.42, 0.22, 1)
    nt.links.new(wave.outputs["Fac"], ramp.inputs["Fac"]); nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.55
    return mat
```

Why: Principled inputs since 4.0 are named `"Specular IOR Level"`, `"Transmission Weight"`,
`"Coat Weight"`, `"Sheen Weight"`, `"Emission Color"` + `"Emission Strength"` — the old
`"Specular"`, `"Transmission"`, `"Emission"` keys raise KeyError.

### Several colours on ONE mesh (`material_index`)

The contract is one mesh object per plan part, so a black collar on a white arm is a
per-FACE material, not a second object: the slots live on the mesh, the index on the
polygon, and every polygon starts on slot 0 — append the base material FIRST.

```python
def add_slot(obj, mat):
    """Append a material to the mesh; returns the slot index you write per face."""
    obj.data.materials.append(mat)
    return len(obj.data.materials) - 1


def new_faces(bm, op, **kw):
    """`create_cube`/`create_cone` return {'verts'} ONLY, so `res["faces"]` raises and
    `res.get("faces", [])` paints nothing — diff the face set instead."""
    before = set(bm.faces)
    op(bm, **kw)
    return set(bm.faces) - before


bm = bmesh.new()
new_faces(bm, bmesh.ops.create_cube, size=0.04)                     # the tube stays on slot 0
for f in new_faces(bm, bmesh.ops.create_cube, size=0.06,
                   matrix=Matrix.Translation((0, 0, 0.04))):
    f.material_index = 1                                # the collar; BEFORE bm.to_mesh(me)
arm = obj_from_bmesh("DemoTwoTone", bm, (3.0, 0, 0.05))
add_slot(arm, make_pbr("DemoWhite", (0.88, 0.88, 0.86), 0.45))      # slot 0 = the default
add_slot(arm, make_pbr("DemoBlack", (0.02, 0.02, 0.02), 0.5))
for poly in arm.data.polygons:                          # same thing on a finished mesh;
    if poly.center.z > 0.03:                            # poly.center is in LOCAL space
        poly.material_index = 1
```

Slots without indices are the silent failure: every material is there, the part exports in
one flat colour, and the judge caps the run for `untextured_flat`.  The build census warns
when a mesh carries slots no polygon uses — that warning means the assignment no-opped,
not that a material is missing.

## Structure: names, collections, parenting, instancing, join/separate

```python
def collection(name: str) -> bpy.types.Collection:
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name); bpy.context.scene.collection.children.link(col)
    return col

def move_to(obj, col):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)

def linked_copy(src: bpy.types.Object, name: str, location) -> bpy.types.Object:
    """Instance: shares mesh data (cheap, identical detail).  Give each its own plan name."""
    o = bpy.data.objects.new(name, src.data)
    o.location = location; o.rotation_euler = src.rotation_euler; o.scale = src.scale
    return link(o)

def join(objs, name: str) -> bpy.types.Object:
    """Join into the FIRST object (its transform wins); result renamed to `name`."""
    target = objs[0]
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.join()
    target.name = name
    return target

def separate_loose(obj) -> list:
    """Split an object into its mesh islands (returns all resulting objects)."""
    before = set(bpy.data.objects)
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                   selected_editable_objects=[obj]):
        bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.separate(type='LOOSE')
        bpy.ops.object.mode_set(mode='OBJECT')
    return [obj] + sorted(set(bpy.data.objects) - before, key=lambda o: o.name)

legs = collection("Legs")
leg0 = make_cylinder("Leg1", 0.02, 0.42, (0.18, 0.18, 0.21), 16)
for i, (sx, sy) in enumerate([(-1, 1), (-1, -1), (1, -1)], start=2):
    linked_copy(leg0, f"Leg{i}", (sx * 0.18, sy * 0.18, 0.21))
for o in bpy.data.objects:
    if o.name.startswith("Leg"): move_to(o, legs)
```

Why: use COLLECTIONS for organisation (they are not exported as nodes).  **Never parent
parts or instances under an Empty**: the harness measures top-level GLB nodes as parts, so
an Empty parent merges its whole subtree into ONE part (plan part reported missing) — and an
empty with non-identity scale also skews the baked world transforms.  `join()` inherits
the ACTIVE object's transform; make the largest/identity object active.

## Proportions and detail (how to look good cheaply)

| feature | recipe | size |
|---|---|---|
| rounded edges | `add_bevel(width=t/4, segments=2–3)` | furniture 2–4 mm, devices 0.5–1 mm, vehicles 5–15 mm |
| chamfer | bevel `segments=1` | 1–3 mm |
| panel seam / gap | boolean-cut a 1 mm × 2 mm groove, or build panels 1 mm apart | 0.8–1.5 mm |
| inset panel | `bmesh.ops.inset_region(thickness=…, depth=−…)` on the face | inset 5–10 mm, depth 1–3 mm |
| fillet at a joint | small torus/cylinder collar overlapping both parts | r 3–8 mm |
| fasteners | `make_cylinder(r=2–4 mm, depth=1–2 mm)` pressed 0.5 mm into the surface, arrayed | head Ø 5–8 mm |
| rims / lips | lathe profile with a 2 mm bump | |
| feet / glides | short cylinders Ø 15–25 mm, h 5–10 mm, under each leg | |
| handles / knobs | torus or lathe, overlapping the panel ≥ 2 mm | knob Ø 25–35 mm |
| cushions | box + subsurf 2 + creases + scale 0.98 | 60–120 mm thick |
| spokes / slats / louvres | one part + `add_array` or a polar loop of `linked_copy` | slat 10–15 mm thick |

```python
def inset_top(obj, thickness=0.008, depth=-0.002):
    """Recessed top panel: inset the +Z face (bmesh on the real mesh)."""
    bm = bmesh.new(); bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    top = max(bm.faces, key=lambda f: f.calc_center_median().z)
    bmesh.ops.inset_region(bm, faces=[top], thickness=thickness, depth=depth, use_even_offset=True)
    bm.to_mesh(obj.data); bm.free(); obj.data.update()

def polar_copies(src, n, radius, center, name_fmt="{base}{i}"):
    """src + n-1 linked copies evenly on a circle (bolts, spokes, chair legs).
    center=(cx, cy, z); copies are named <Base>2..N, src itself is moved to slot 1."""
    cx, cy, z = center
    base = src.name.rstrip("0123456789")
    out = [src]
    for i in range(n):
        a = 2 * math.pi * i / n
        loc = (cx + radius * math.cos(a), cy + radius * math.sin(a), z)
        o = src if i == 0 else linked_copy(src, name_fmt.format(base=base, i=i + 1), loc)
        o.location = loc; o.rotation_euler.z = a
        if i: out.append(o)
    return out

lid = make_box("Lid", (0.30, 0.20, 0.012), (0.9, 0, 0.306))
inset_top(lid, 0.01, -0.002); add_bevel(lid, 0.002, 2); apply_modifiers(lid)
bolt = make_cylinder("Bolt1", 0.003, 0.002, (0, 0, 0), 12)
polar_copies(bolt, 6, 0.06, (0.9, 0, 0.3125))   # 6 bolt heads on a Ø 0.12 circle, sunk 0.5 mm into the lid
```

Rules of thumb: a silhouette needs ≥ 3 distinct masses; every large flat face needs one
break (seam, inset, bevel, trim); thin things (blades, sheet, glass) are 1–3 mm, never 0;
every part that a human would touch has a rounded edge.

## Density: visual complexity without hand-modelling every screw

Measured on this harness (183 judged rounds): **triangles inside a part are free score, extra
top-level parts are not** — ρ(tri_per_part, geometry_detail) ≈ +0.08 while ρ(n_plan_parts,
assembly_fit) = −0.48.  Every recipe below adds density *inside* a part the plan already names.

**Where the detail budget goes.** A viewer (and the judge's montage) looks, in order, at the
silhouette, then the 2–3 largest faces, then whatever is at eye height and at the front, then the
places where two materials meet.  Spend there and stop.

| priority | what | typical tri cost | recipe |
|---|---|---|---|
| 1 | silhouette breaks (taper, waist, overhang) | 0 (shape the primitive) | `taper`, `lathe`, profile sweep |
| 2 | bevel every hard edge | ×2–3 verts on that part | `add_bevel(w, 2–3)` |
| 3 | panel lines / shut lines on big faces | 200–600 | `panel_lines` |
| 4 | fasteners at real joints | 60–200 each | `polar_copies`, `bolt_ring` |
| 5 | repeated countable features (slats, spokes, dentils) | 100–400 each | `add_array`, `radial_array` |
| 6 | greebles in a bounded patch | 300–1500 | `greeble_patch` |
| 7 | wear/variation in materials | 0 | per-instance material tint |

Do **not** spend budget on: the underside, the back of a wall-mounted object, interior volumes the
camera cannot enter, or subdividing a flat panel.

### Parametric repetition (one part, N features, one loop)

```python
def radial_array(obj, count, *, center=(0.0, 0.0), axis='Z'):
    """`count` copies of `obj` around `center` — ONE mesh, one modifier, N features.
    Spokes, dentils, flutes, cage wires, balusters, turbine blades.
    The Empty is a construction aid: it is applied and deleted here, so no non-mesh
    object ever reaches the export (the contract allows meshes only)."""
    piv = bpy.data.objects.new(f"{obj.name}_Pivot", None)
    piv.location = (center[0], center[1], 0.0)
    piv.rotation_euler['XYZ'.index(axis)] = 2 * math.pi / count
    link(piv)
    m = obj.modifiers.new("Radial", 'ARRAY')
    m.count, m.use_relative_offset, m.use_object_offset, m.offset_object = count, False, True, piv
    apply_modifiers(obj)
    bpy.data.objects.remove(piv, do_unlink=True)
    return obj


def flutes(obj, count, radius, depth, height, z0, *, center=(0.0, 0.0), segments=8):
    """`count` vertical grooves cut into a cylindrical body of radius `radius` standing on
    `center` (pump bodies, columns, knurled knobs, fluted table legs)."""
    cutter = make_cylinder(f"{obj.name}_Flute", depth, height,
                           (center[0] + radius, center[1], z0 + height / 2), segments)
    radial_array(cutter, count, center=center)
    boolean_cut(obj, cutter)          # boolean_cut applies and deletes the cutter
    return obj


# 24 spokes on a wheel, then 8 flutes cut into a column — two loops, two modifiers
spoke = make_cylinder("DemoSpoke", 0.004, 0.30, (0.0, 0.0, 0.15), 8)
radial_array(spoke, 24)
column = make_cylinder("DemoColumn", 0.045, 0.60, (0.0, 0.0, 0.30), 48)
flutes(column, 8, 0.045, 0.006, 0.55, 0.03)
assert len(column.data.vertices) > 96, "the flutes did not cut: is the cutter overlapping the body?"
```

### Instancing with per-instance variation

Linked copies share mesh data (free memory, free triangles in Blender's sense) but they may
differ in transform and material.  Identical repeats read as CG; 2–5 % variation reads as real.

```python
def varied_copies(src, n, place, *, seed=0, jitter_m=0.0, tilt_deg=0.0, scale_pct=0.0, mats=None):
    """n named copies of `src` (`Name_0..Name_{n-1}`), each with a seeded wobble.
    `place(i) -> (x, y, z)`; the copies stay TOP-LEVEL (never parented to an Empty)."""
    rnd = random.Random(seed)
    base = src.name
    out = []
    for i in range(n):
        o = src if i == 0 else linked_copy(src, f"{base}_{i}", (0, 0, 0))
        x, y, z = place(i)
        o.name = f"{base}_{i}"
        o.location = (x + rnd.uniform(-jitter_m, jitter_m), y + rnd.uniform(-jitter_m, jitter_m), z)
        o.rotation_euler = (math.radians(rnd.uniform(-tilt_deg, tilt_deg)),
                            math.radians(rnd.uniform(-tilt_deg, tilt_deg)), o.rotation_euler.z)
        s = 1.0 + rnd.uniform(-scale_pct, scale_pct) / 100.0
        o.scale = (s, s, s)
        if mats:
            o.data = o.data.copy()          # break the link ONLY to vary the material slot
            o.data.materials.clear()
            o.data.materials.append(mats[i % len(mats)])
        out.append(o)
    return out


slat = make_box("DemoSlat", (0.30, 0.012, 0.05), (0, 0, 0))
slats = varied_copies(slat, 7, lambda i: (0.0, 0.0, 0.10 + 0.07 * i), seed=3, jitter_m=0.0008,
                      tilt_deg=0.6, scale_pct=1.5)
assert [o.name for o in slats] == [f"DemoSlat_{i}" for i in range(7)]
assert len({tuple(round(v, 5) for v in o.location) for o in slats}) == 7   # really varied
```

### Procedural greebles, bounded to a region

Greebles are surface clutter (vents, boxes, ribs) that reads as machinery.  Bound them to a patch
so they never break the silhouette or the part's plan bbox.

```python
def greeble_patch(name, origin, size_xy, *, n=24, seed=0, h_range=(0.002, 0.010),
                  cell=0.02, material=None):
    """One mesh of `n` seeded boxes inside an (x, y) patch at z=origin[2], heights in
    `h_range`.  Join it INTO the host part afterwards so it stays one named object."""
    rnd = random.Random(seed)
    bm = bmesh.new()
    sx, sy = size_xy
    for _ in range(n):
        w = rnd.uniform(cell * 0.35, cell * 1.4)
        d = rnd.uniform(cell * 0.35, cell * 1.4)
        h = rnd.uniform(*h_range)
        cx = rnd.uniform(-sx / 2 + w / 2, sx / 2 - w / 2)
        cy = rnd.uniform(-sy / 2 + d / 2, sy / 2 - d / 2)
        m = Matrix.Translation((cx, cy, h / 2 - 0.0005)) @ Matrix.Diagonal((w, d, h, 1.0))
        bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    obj = obj_from_bmesh(name, bm, origin)
    if material is not None:
        obj.data.materials.append(material)
    return obj


host = make_box("DemoHousing", (0.24, 0.16, 0.10), (0.6, 0, 0.05))
greebles = greeble_patch("DemoHousing_Greebles", (0.6, 0, 0.10), (0.20, 0.12), n=26, seed=7)
housing = join([host, greebles], "DemoHousing")   # ONE named part, 26 extra shapes of detail
assert len(housing.data.polygons) == 6 + 26 * 6, "greebles were not joined into the host part"
```

### Profile sweeps (a section dragged along a path)

The cheapest way to make mouldings, handrails, rims, tubing and cornices look machined: draw the
*section* once, sweep it along the path.

```python
def sweep_profile(name, path_pts, profile_pts, *, closed_path=False, tilt_deg=0.0, resolution=6):
    """Sweep a 2-D `profile_pts` [(x, y) in the section plane] along `path_pts` [(x, y, z)]
    using a curve bevel object — cornices, handrails, rims, picture frames, gutters."""
    prof = bpy.data.curves.new(f"{name}_Prof", 'CURVE')
    prof.dimensions = '2D'
    sp = prof.splines.new('POLY')
    sp.points.add(len(profile_pts) - 1)
    for p, (x, y) in zip(sp.points, profile_pts):
        p.co = (x, y, 0.0, 1.0)
    sp.use_cyclic_u = True
    cu = bpy.data.curves.new(f"{name}_Path", 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_mode, cu.bevel_object, cu.resolution_u = 'OBJECT', bpy.data.objects.new(f"{name}_ProfObj", prof), resolution
    bpy.context.scene.collection.objects.link(cu.bevel_object)
    ps = cu.splines.new('POLY')
    ps.points.add(len(path_pts) - 1)
    for p, (x, y, z) in zip(ps.points, path_pts):
        p.co = (x, y, z, 1.0)
    ps.use_cyclic_u = closed_path
    obj = bpy.data.objects.new(name, cu)
    link(obj)
    activate(obj)
    bpy.ops.object.convert(target='MESH')
    obj = bpy.context.object
    bpy.data.objects.remove(bpy.data.objects[f"{name}_ProfObj"], do_unlink=True)
    return obj


CORNICE = [(0.0, 0.0), (0.045, 0.0), (0.045, 0.012), (0.030, 0.020), (0.030, 0.032), (0.0, 0.032)]
cornice = sweep_profile("DemoCornice", [(-0.6, -0.3, 0.9), (0.6, -0.3, 0.9), (0.6, 0.3, 0.9), (-0.6, 0.3, 0.9)],
                        CORNICE, closed_path=True)

# a curved path needs SAMPLES: one point per 5-10 degrees, not four corners.
HANDRAIL = [(0.045, 0.0), (0.020, 0.018), (-0.020, 0.018), (-0.045, 0.0), (0.0, -0.012)]
helix = [(0.70 * math.cos(math.radians(a)), 0.70 * math.sin(math.radians(a)), 0.95 + 0.0075 * a)
         for a in range(0, 365, 5)]
rail = sweep_profile("DemoHandrail", helix, HANDRAIL)
assert len(cornice.data.polygons) >= 24 and len(rail.data.polygons) >= 300, "sweep produced no surface"
```

### Boolean detailing (grooves, slots, holes — in one cut)

Build ALL cutters of one kind as a single mesh, then cut once: `n` booleans cost `n` evaluations
and `n` chances to produce non-manifold garbage; one cutter costs one.

```python
def panel_lines(obj, lines, *, width=0.0015, depth=0.0015):
    """Cut shut-lines into a body.  `lines` = [(center_xyz, size_xyz)] in world metres —
    make one thin box per line, join them, cut once."""
    bm = bmesh.new()
    for (cx, cy, cz), (sx, sy, sz) in lines:
        m = Matrix.Translation((cx, cy, cz)) @ Matrix.Diagonal(
            (max(sx, width), max(sy, width), max(sz, depth), 1.0))
        bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    boolean_cut(obj, obj_from_bmesh(f"{obj.name}_Lines", bm))
    return obj


def bolt_ring(obj, n, radius, center, *, head_r=0.004, head_h=0.0015, seg=12):
    """n bolt heads standing 1.5 mm proud on a circle, joined INTO `obj` (one part)."""
    cx, cy, cz = center
    bm = bmesh.new()
    for i in range(n):
        a = 2 * math.pi * i / n
        m = Matrix.Translation((cx + radius * math.cos(a), cy + radius * math.sin(a), cz))
        bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=head_r, radius2=head_r * 0.9,
                              depth=head_h, matrix=m)
    heads = obj_from_bmesh(f"{obj.name}_Bolts", bm)
    return join([obj, heads], obj.name)


door = make_box("DemoDoor", (0.42, 0.018, 0.36), (1.2, 0, 0.18))
panel_lines(door, [((1.2, -0.009, 0.36), (0.42, 0.004, 0.0015)),
                   ((1.2, -0.009, 0.00), (0.42, 0.004, 0.0015))])
door = bolt_ring(door, 6, 0.10, (1.2, -0.010, 0.18))
assert len(door.data.polygons) > 6, "the panel lines / bolts did not land on the door"
```

### Bevel / solidify / weighted-normal stacks (sheet metal and cast parts)

```python
def shade_smooth(obj):
    """Smooth shading on the MESH (4.1+ removed `use_auto_smooth`; a Smooth-by-Angle
    modifier is what the UI now adds, and the GLB exporter bakes its normals)."""
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return obj


def sheet_metal(obj, *, thickness=0.0012, bevel=0.0008, segments=2):
    """Give a slab or zero-thickness shape a real sheet-metal read: wall, rounded edge,
    shading that follows the bevel.  Order matters — solidify BEFORE bevel."""
    add_solidify(obj, thickness, inward=False)
    add_bevel(obj, bevel, segments)
    wn = obj.modifiers.new("WeightedNormal", 'WEIGHTED_NORMAL')   # keeps the bevel crisp
    wn.keep_sharp = True
    return shade_smooth(obj)


def cast_part(obj, *, fillet=0.004, segments=3):
    """Cast/moulded read: one generous fillet on every edge, smooth shading, no sharp corners."""
    add_bevel(obj, fillet, segments)
    return shade_smooth(obj)


tray = make_box("DemoTray", (0.26, 0.18, 0.004), (1.8, 0, 0.002))
sheet_metal(tray)
housing = make_box("DemoCastHousing", (0.12, 0.09, 0.07), (2.2, 0, 0.035))
cast_part(housing)
```

### The density check

Before you finish, ask of each part: *what would tell a photograph of the real thing from this?*
If the answer is "the edges are perfectly sharp" → bevel.  "It is one flat face" → panel line or
inset.  "It has no fixings" → bolt ring.  "The repeats are identical" → `varied_copies`.  "It is
all one grey" → split the materials, per face if it is one part (`material_index`, above).  Then measure: `measure` reports the triangle count — if you
are under the detail budget's floor, you have not detailed anything yet.

## Common objects — dimensions (metres) and decomposition

| object | overall (W × D × H) | parts (plan names) | key numbers |
|---|---|---|---|
| dining chair | 0.45 × 0.50 × 0.90 | Seat, Backrest, LegFrontLeft/Right, LegBackLeft/Right, Stretchers | seat h 0.45, seat t 0.04, leg Ø/□ 0.035, back top 0.90, back rake 8° |
| armchair / sofa seat | 0.85 × 0.90 × 0.85 (sofa W 1.8–2.2) | Base, SeatCushion(s), BackCushion(s), ArmLeft, ArmRight, Legs | seat h 0.42, cushion 0.10–0.12, arm h 0.60, arm w 0.15–0.25 |
| dining table | 1.6 × 0.9 × 0.75 | Top, Apron, Leg1..4 | top t 0.03–0.04, leg 0.07 □ at 0.05 inset |
| coffee table | 1.2 × 0.6 × 0.45 | Top, Leg1..4 or Base | top t 0.025 |
| desk lamp | 0.18 Ø base, H 0.45–0.60 | Base, ArmLower, ArmUpper, Shade, Bulb | base t 0.02, arm Ø 0.012, shade Ø 0.15 h 0.12 |
| floor lamp | 0.30 Ø base, H 1.6 | Base, Pole, Shade | pole Ø 0.025, shade Ø 0.40 |
| mug | Ø 0.085 × 0.095 | MugBody, MugHandle | wall 4 mm, bottom 6 mm, handle tube Ø 12 mm |
| bottle (wine) | Ø 0.075 × 0.30 | Body, Neck, Cap | neck Ø 0.028, shoulder at 0.20 |
| bicycle | 1.75 × 0.60 × 1.05 | Frame, FrontWheel, RearWheel, Fork, Handlebar, Saddle, SeatPost, Crank, PedalLeft/Right, Chain | wheel Ø 0.70 (700c), tyre w 0.03, BB height 0.27, saddle h 0.95 |
| car (sedan) | 4.6 × 1.85 × 1.45 | Body, Cabin(glass), WheelFL/FR/RL/RR, Bumpers, Mirrors, Lights | wheel Ø 0.65, wheelbase 2.7, ground clearance 0.15 |
| house (small) | 10 × 8 × 7 | Walls, Roof, Door, Windows, Chimney, Steps | wall h 2.8/storey, roof pitch 30–40°, door 0.9 × 2.1, window 1.2 × 1.2 at sill 0.9 |
| tree (deciduous) | crown Ø 6–9, H 10–14 | Trunk, Branches, Crown(s) | trunk Ø 0.4 at base, first branch at 2.5, crown = 3–6 overlapping spheres |
| door | 0.9 × 0.045 × 2.05 | Leaf, Frame, Handle, Hinges | handle h 1.05, frame 0.07 wide |
| bookshelf | 0.80 × 0.30 × 1.80 | SideLeft, SideRight, Top, Bottom, Shelf1..4, Back | panel 18 mm, shelf pitch 0.33 |
| kitchen cabinet | 0.60 × 0.58 × 0.87 | Carcass, Door(s)/Drawer(s), Handle(s), Plinth | plinth h 0.10, front 18 mm, handle proud 30 mm |
| monitor | 0.62 × 0.20 × 0.45 | Panel, Stand, Base | panel t 0.02, bezel 8 mm, base 0.25 × 0.20 |
| keyboard | 0.44 × 0.14 × 0.03 | Body, Keys(instanced) | key 0.018 pitch 0.019 |

Decomposition recipe (any object): 1 main mass → 2–6 secondary masses that TOUCH it →
attachments (handles, feet, trim) that overlap ≥ 2 mm → detail pass (bevels, seams,
fasteners).  Name every mass as the plan does; instances by `linked_copy`.

## Keyframed motion for a scene hero (the harness exports it as a glTF clip)

A scene hero may carry ONE 2 s loop (24 fps, frames 1–49, ends where it starts); the
scene plays it by itself.  Keyframe the moving object's OWN transform, and build that
object's mesh AROUND ITS PIVOT so the rotation happens where the hinge is — an object whose
origin sits at its centre swings about its centre, and the sails fly off the hub.

```python
import bpy, bmesh, math
from mathutils import Matrix

def build_sail_rotor() -> bpy.types.Object:
    """Four lattice sails on a hub; the mesh is centred on the hub axis so rotation is about it."""
    bm = bmesh.new()
    for i in range(4):                                   # sails built AROUND (0, 0, 0)
        a = i * math.pi / 2
        sail = bmesh.new()
        bmesh.ops.create_cube(sail, size=1.0)
        sail.transform(Matrix.Diagonal((11.0, 0.15, 1.6, 1.0)))       # a lattice sail, 11 m long
        sail.transform(Matrix.Translation((5.5, 0.0, 0.0)))          # out from the hub
        sail.transform(Matrix.Rotation(a, 4, "Y"))                    # around the hub axis
        me_s = bpy.data.meshes.new("_sail"); sail.to_mesh(me_s); sail.free()
        bm.from_mesh(me_s); bpy.data.meshes.remove(me_s)
    me = bpy.data.meshes.new("SailRotor"); bm.to_mesh(me); bm.free()
    rotor = bpy.data.objects.new("SailRotor", me)
    rotor.location = (0.0, -3.2, 18.0)                   # the HUB's world position = the pivot
    bpy.context.scene.collection.objects.link(rotor)
    sc = bpy.context.scene; sc.frame_start, sc.frame_end = 1, 49
    for frame, ang in ((1, 0.0), (25, math.pi / 4), (49, math.pi / 2)):   # a quarter turn: 4 sails loop seamlessly
        rotor.rotation_euler = (0.0, ang, 0.0)           # about the hub axis (-Y is the front: the axis is Y here)
        rotor.keyframe_insert("rotation_euler", index=1, frame=frame)
    return rotor
```

* Keep the moving part a normal named MESH object (no Empty pivots — an Empty is not a
  part and the census counts it as junk); parent nothing to it.
* A loop of a quarter turn for 4-fold sails, a half turn for 2-fold, a full turn for a
  wheel; a swing goes −a → +a → −a.  Amplitude must read at scene distance (≥ 0.15 rad or
  ≥ 0.05 m); the scene samples t = 0 and t = 1.5 s.
* Linear interpolation for a wheel (`kp.interpolation = 'LINEAR'` on the fcurve's
  keyframe points) so it does not ease at the loop seam.

## Pitfalls (symptom → cause → fix)

1. **`bm.verts[i]` raises "outdated internal index table"** → call
   `bm.verts.ensure_lookup_table()` (also `edges`/`faces`) after `from_mesh` or any
   topology op before indexing.
2. **"context is incorrect" / poll() failed in background** → the operator needs an active +
   selected object or edit mode: use `activate(obj)` or `bpy.context.temp_override(...)`
   as in `apply_modifiers`; prefer data API (`bmesh`, `me.transform`) over `bpy.ops`.
3. **`bpy.ops.object.transform_apply(scale=True)` moved my part to the origin** → the other
   flags default True and baked location.  Always pass `location=, rotation=, scale=`
   explicitly (see `apply_transforms`).
4. **Modifier apply fails "Modifier cannot be applied to a multi-user mesh"** →
   `obj.data = obj.data.copy()` first (`apply_modifiers` does it).
5. **Names like `Leg.001`** → you created two objects/meshes with the same name; give every
   instance its plan name (`Leg2`); rename meshes too if you care (`o.data.name = o.name`).
6. **Boolean does nothing / leaves holes** → cutter coplanar with a face or not closed.
   Overshoot the cutter by ≥ 5 mm, make it watertight (`cap_ends=True`), use `EXACT`.
7. **Dark/inverted shading, inside-out parts** → flipped normals after `bmesh.ops.scale`
   with negative factors or after mirroring: `bmesh.ops.recalc_face_normals(bm, faces=bm.faces)`
   or `bpy.ops.mesh.normals_make_consistent` in edit mode.
8. **`mesh.use_auto_smooth` AttributeError** (removed 4.1) → `obj.data.shade_smooth()` then
   `bpy.ops.object.shade_smooth_by_angle(angle=math.radians(30))` under `temp_override`.
9. **`bsdf.inputs["Specular"]` KeyError** → 4.x names: `"Specular IOR Level"`,
   `"Transmission Weight"`, `"Emission Color"`/`"Emission Strength"`, `"Coat Weight"`.
10. **`mat.use_nodes = True`** is a no-op/deprecated in 5.0 — the node tree already exists.
11. **Object scaled with `obj.scale` looks right but exports wrong / bevels uneven** → bake
    with `apply_transforms(obj)` before bevel/boolean; keep scale (1,1,1) on export.
12. **`bpy.ops.mesh.primitive_cube_add(size=1, scale=(…))`** — default size is 2 m; with
    `scale=` and `size=1` you get exact extents, but still bake the scale afterwards.
13. **Degenerate geometry** (zero-area faces, zero-thickness walls, coincident faces of two
    parts) → z-fighting and boolean failures.  Offset coincident faces by 0.5 mm or overlap
    parts by 2 mm; remove doubles `bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)`.
14. **Units**: the scene is in meters; a 450 mm seat is `0.45`, never `450`.  If you see a
    bbox of 450 × 500 × 900 the plan numbers were typed in mm.
15. **`bpy.context.object` is None in background** → do not rely on it; keep your own refs.
16. **Deprecated in 4.x/5.x (do not use)**: `mesh.calc_normals()`, `mesh.calc_normals_split()`,
    `edge.crease` / `edge.bevel_weight` (use attributes `crease_edge`, `bevel_weight_edge`),
    `bpy.ops.*({'object': o})` dict overrides, `mat.blend_method`, `scene.node_tree`,
    `BLENDER_EEVEE_NEXT` (5.0 is `BLENDER_EEVEE`), `bgl`, `gpu.types.GPUShader(...)`.
17. **`bmesh.ops.create_cone` with `radius2=0`** makes a cone with a degenerate cap — fine
    for visuals; for booleans use `radius2=1e-4`.
18. **A curve object left un-converted** exports nothing (the harness converts meshes
    only) → always `convert(target='MESH')` as in `make_tube_along`.
19. **Huge scripts / per-vertex Python loops over 500 k verts** time out → use modifiers,
    `foreach_set`, or fewer segments.
20. **Parenting with scaled parents** → children inherit scale; export bakes it, bevels go
    elliptical.  Do not parent parts at all — an Empty parent also merges its children into
    ONE measured part (plan part reported missing); group with collections instead.
21. **`ModuleNotFoundError: No module named 'parts.seat_cushion'`** → the part file must be
    `src/parts/seat_cushion.py` (snake_case of the plan name); `src/` is on `sys.path`, do
    not add path hacks.  **`ImportError: cannot import name 'build_x'`** → the part file must
    define exactly `def build_<snake>()`.
22. **A part appears twice (`Seat.001`)** → the part file calls `build_seat()` at module level
    AND model.py calls it.  Part files only define; model.py calls each builder once.

## Self-check (before you call it done)

```python
def selfcheck(expected_names, bbox_hint=None, tol=0.01):
    """Print a census the way the harness measures it; raise on contract violations."""
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    names = sorted(o.name for o in meshes)
    missing = [n for n in expected_names if n not in names]
    extra = [n for n in names if n not in expected_names]
    assert not missing, f"missing parts: {missing}"
    assert not extra, f"unexpected objects: {extra}"
    assert all('.' not in n for n in names), f"auto-suffixed names: {[n for n in names if '.' in n]}"
    lo = Vector((min(world_bbox(o)[0].x for o in meshes), min(world_bbox(o)[0].y for o in meshes),
                 min(world_bbox(o)[0].z for o in meshes)))
    hi = Vector((max(world_bbox(o)[1].x for o in meshes), max(world_bbox(o)[1].y for o in meshes),
                 max(world_bbox(o)[1].z for o in meshes)))
    assert abs(lo.z) < tol, f"lowest point z={lo.z:.4f}, expected 0"
    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)
    assert tris < 600_000, f"too many triangles: {tris}"
    print(f"[selfcheck] parts={len(meshes)} tris={tris} extents={tuple(round(v, 3) for v in (hi - lo))}")
    if bbox_hint:
        for a, b in zip(hi - lo, bbox_hint):
            assert abs(a - b) < 0.05, f"extents {tuple(hi - lo)} vs plan {bbox_hint}"

# (the demo objects above are not one coherent object, so only run the census part here)
print(len([o for o in bpy.data.objects if o.type == 'MESH']), "mesh objects")
```

Then use the tools: `build` → `render_sheet` → `check_connectivity` → `isolate` the part
you doubt → `cross_section` through cavities → `check_contract`.  Fix the worst finding,
rebuild, repeat.  Done = all green and the sheet reads as the object from every view.
