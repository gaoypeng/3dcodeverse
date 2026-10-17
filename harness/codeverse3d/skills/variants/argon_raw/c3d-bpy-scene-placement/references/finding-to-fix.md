# Finding to fix, where rows come from, and a worked zone

Companion to `c3d-bpy-scene-placement`. Read against `codeverse3d/spatial/scene_placement.py`,
`runtime_js/lib/host_placement.mjs`, `codeverse3d/languages/wrappers/_census_glb.py` and the
`Placement` section of `codeverse3d/config.py` on 2026-10-14. No library ships in raw mode: each
row's fix is a move you compute from world-space vertices; any construction that passes
`check_placement` is as good.

## 1. Finding to fix

| kind, and what the line says | severity | it means | what meets it |
|---|---|---|---|
| `floating` — `X is floating 0.24 m above Terrain (touches nothing)` | WARN; ERROR past `floating_error_m`, or when more than `floating_error_count` assets float | the lowest foot column hangs in the air | lower it by the printed number: its lowest vertices onto `ctx.height_at`, or onto the support's top for a thing on a thing |
| `unsupported` — `X is unsupported: hovers 0.03 m above Terrain and touches nothing` | WARN | off the contact tolerance but inside the floating band | seat it; if it really hangs, tag it `obj["placement"] = "free"` |
| `sunken` — `X is sunken 0.31 m into Terrain` | WARN; ERROR past `sunk_error_m` when the burial also passes `sunk_error_frac` of the height | a foot column sits below the surface | raise it by the number, drop its `sink`, or — only if it is driven or grown in — give it a `partial_ok` name |
| `interpenetration` — `A and B overlap (41% of the smaller box; 23% of its vertices inside the other)` | WARN past `overlap_warn`, ERROR past `overlap_error` | two rows share volume | move the smaller one out, shrink one, remove one, or make them ONE row (parts parented under one root, a joined mesh) |
| `cantilevered` — one column rests, the worst one hangs | WARN | a thing placed at its centre's ground height on a slope or a bump: the downhill feet hang | seat it (the lowest ground wins, the rest sink), a pad in `height_at`, seated segments, or a turn along the contour |
| `free_exempt` — too many assets tagged free | WARN past `free_exempt_warn`, ERROR past `free_exempt_error` | the tag is hiding things from the gate | untag everything that stands; `free` is for hanging and airborne things |
| `scale` — `each of the 6 lantern instances in Square/Lanterns measures 2.80 m but the plan sized lantern at ~0.60 m (4.7x)` | WARN, ERROR by factor | the asset is built at the wrong size | scale the SOURCE collection's build, not the Empties |
| `out_of_bounds` — `X (planned content 'bollard') sits entirely outside the plan bounds + 8 m` | WARN | a row carrying a PLANNED content's name lies wholly past the bounds plus max(5 m, 10 % of the span); unplanned scenery past the bounds is never reported | move it in (clamp its x / y into the zone bbox), or leave it if the brief puts it there and say so |
| `world_edge` — `the world ends at its bounds: the farthest standing geometry (>= 2 m tall) reaches N m from the bounds centre` | WARN | an outdoor plan whose content or ground groups at least 2 m tall all stop inside 1.25 × the bounds' half-extent | whatever the brief's horizon holds, continued past the bounds from the env and named `Sky…` when wide |
| `truncated` — `… and 4 more floating finding(s) not listed` | INFO | the report keeps the worst 8 per kind | fix the listed ones, rebuild, read the next batch |

The table under the findings stops at 400 assets or after 4 s of sampling and says so; a scene
that hits either limit has more placed rows than a judge can read anyway — fewer, merged rows,
with the hundreds as instanced clouds, is what fits.

## 2. Where a row comes from

The census GLB writer walks the evaluated depsgraph; the placement table reads the GLB:

* a collection-instance Empty becomes one node whose children are the instanced meshes — one
  clone per placed copy, so each copy is a row sampled at its own feet;
* a mesh with children is its own group: the subtree is one row;
* any other instance cloud (geometry nodes, particles, dupli-verts) becomes one gpu-instancing
  node per source mesh — an `InstancedMesh` to the gate, counted as instances and exempt from
  per-asset placement, and never a support;
* custom properties of plain value travel as node extras, which is how `obj["placement"] = "free"`
  reaches the gate; a property on the zone's collection exempts the whole zone;
* an object whose every material is volume-only is fog: not indexed, not a support, not an
  overlap partner, and the `no_fog` check counts it as atmosphere.

Supports are the OTHER rows' meshes plus the terrain and floors: a lantern resting on a post is
measured to the post, a crate on a jetty to the jetty, a boat on a water-named plane is on water.

Seat and check against the same point set the gate measures — an object's own geometry, its
children's and, for a collection instance, the source collection's under the Empty's transform —
so an instance Empty is seated by its source's vertices, and the root of a finished prop is the
thing to move.

## 3. A worked zone, one way

```python
def feet(obj, dg):                                 # the gate's foot band, world space, evaluated (modifiers count)
    inst = obj.instance_type == "COLLECTION"       # an instance Empty: its source's meshes through its matrix
    m0 = obj.matrix_world if inst else Matrix()    # (a mesh root with children: add theirs the same way)
    pts = []
    for o in (obj.instance_collection.all_objects if inst else [obj]):
        if o.type == "MESH":
            pts += [m0 @ o.matrix_world @ v.co for v in o.evaluated_get(dg).to_mesh().vertices]
            o.evaluated_get(dg).to_mesh_clear()
    z0, z1 = min(p.z for p in pts), max(p.z for p in pts)
    return [p for p in pts if p.z <= z0 + max(0.02, 0.05 * (z1 - z0))]

def seat(obj, height_at, dg):                      # the lowest-hanging foot touches, none floats
    obj.location.z += min(height_at(p.x, p.y) - p.z for p in feet(obj, dg))
```

`dg = bpy.context.evaluated_depsgraph_get()`, `Matrix` from `mathutils`.  The band is the gate's:
the vertices within max(0.02 m, 5 % of the height) of the lowest, of which it samples up to 5
columns (the lowest and the band's ±x / ±y extremes).

`matrix_world` is current only after `bpy.context.view_layer.update()` (or compose the parent
chain yourself); a child or an instance Empty adds its own vertices through its own matrix.

A thing named `Shrub…` carries a `partial_ok` word, so a small sink on it is accepted; a bench or
a lantern carries no such word and is measured as it stands. A kite with `kite["placement"] = "free"`
is airborne by design.

## 4. The loop

```
build  ->  check_placement  ->  fix every ERROR, then the deepest WARN  ->  build
```

`check_placement` reads the census of the last build; `rebuild=true` re-takes the census when
you doubt it. A round that ends on an ERROR is capped by the rubric on each one.
