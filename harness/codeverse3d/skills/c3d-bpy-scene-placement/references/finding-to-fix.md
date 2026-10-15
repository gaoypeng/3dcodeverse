# Finding to fix, where rows come from, and a worked zone

Companion to `c3d-bpy-scene-placement`. Read against `codeverse3d/spatial/scene_placement.py`,
`runtime_js/lib/host_placement.mjs`, `codeverse3d/languages/wrappers/_census_glb.py` and the
`Placement` section of `codeverse3d/config.py` on 2026-10-14. The lib calls are the scene_blender
starter's (`src/lib/`, harness-owned: call it, never edit it).

## 1. Finding to fix

| kind, and what the line says | severity | it means | do this |
|---|---|---|---|
| `floating` — `X is floating 0.24 m above Terrain (touches nothing)` | WARN; ERROR past `floating_error_m`, or when more than `floating_error_count` assets float | the lowest foot column hangs in the air | `lib.place.seat(obj, ctx.height_at)`, or `lib.place.rest_on(obj, support)` for a thing on a thing; the printed number IS the gap |
| `unsupported` — `X is unsupported: hovers 0.03 m above Terrain and touches nothing` | WARN | off the contact tolerance but inside the floating band | `seat` it; if it really hangs, tag it `obj["placement"] = "free"` |
| `sunken` — `X is sunken 0.31 m into Terrain` | WARN; ERROR past `sunk_error_m` when the burial also passes `sunk_error_frac` of the height | a foot column sits below the surface | raise it by the number, drop its `sink`, or — only if it is driven or grown in — give it a `partial_ok` name |
| `interpenetration` — `A and B overlap (41% of the smaller box; 23% of its vertices inside the other)` | WARN past `overlap_warn`, ERROR past `overlap_error` | two rows share volume | move the smaller one out, shrink one, or make them ONE prop (`lib.mesh.parent_under`, or one `Batch`) so there is one row |
| `cantilevered` — one column rests, the worst one hangs | WARN | a thing placed at its centre's ground height on a slope or a bump: the downhill feet hang | `seat` it (the lowest ground wins, the rest sink), then flatten a pad in `height_at`, split it into seated segments, or turn it along the contour |
| `free_exempt` — too many assets tagged free | WARN past `free_exempt_warn`, ERROR past `free_exempt_error` | the tag is hiding things from the gate | untag everything that stands; `free` is for hanging and airborne things only |
| `scale` — `each of the 6 lantern instances in Square/Lanterns measures 2.80 m but the plan sized lantern at ~0.60 m (4.7x)` | WARN, ERROR by factor | the asset is built at the wrong size | scale the SOURCE collection's build, not the Empties |
| `out_of_bounds` — `X sits entirely outside the plan bounds` | ERROR | wrong coordinates | `lib.place.keep_inside(obj, bbox)` with the zone bbox |
| `truncated` — `… and 4 more floating finding(s) not listed` | INFO | the report keeps the worst 8 per kind | fix the listed ones, rebuild, read the next batch |

The table under the findings stops at 400 assets or after 4 s of sampling and says so; a scene
that hits either limit has more placed rows than a judge can read anyway — merge props and move
ground cover to `gn_scatter`.

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

`seat`, `seat_instance`, `rest_on`, `clear_of`, `keep_inside` and `bbox_world` all read the same
point set the gate measures — an object's own geometry, its children's and, for a collection
instance, the source collection's under the Empty's transform — so an instance Empty is a valid
argument to every one of them, and the root of a finished prop is the thing to seat.

## 3. A worked zone

```python
from lib import place, scatter

def build(ctx):
    rng, h = ctx.rng(11), ctx.height_at
    bench = scatter.instance(ctx.collection, ctx.assets["bench"], place.unique_name("Bench", 0),
                             3.0, -2.0, height_at=h, rot_z=0.4)            # one Empty, seated: ONE row
    pts = scatter.poisson(rng, area=(<x0>, <y0>, <x1>, <y1>), min_dist=2.5, max_count=12)   # the zone's plan box
    for i, (x, y) in enumerate(pts):
        scatter.instance(ctx.collection, ctx.assets["shrub"], place.unique_name("Shrub", i), x, y,
                         height_at=h, rot_z=rng.uniform(0.0, 6.28), scale=rng.uniform(0.8, 1.2), sink=0.03)
    lantern = <a lib.mesh prop built in this zone, finished under one root>   # a mesh root: one row
    place.rest_on(lantern, bench)                                             # measured to the BENCH
    kite = <a lib.mesh prop>
    kite.location.z += 4.0
    kite["placement"] = "free"                                                # airborne by design
    for a, b, frac in place.clear_of([o for o in ctx.collection.objects if o.parent is None]):
        print("overlap before build:", a, b, frac)                            # fix every pair it names
    return ctx.collection
```

`Shrub` carries a `partial_ok` word, so its small `sink` is accepted; the bench, the lantern and
the kite carry no such word and are measured as they stand.

## 4. The loop

```
build  ->  check_placement  ->  fix every ERROR, then the deepest WARN  ->  build
```

`check_placement` reads the census of the last build; pass `rebuild=true` only when you doubt
that census. Never finish a round on an ERROR.
