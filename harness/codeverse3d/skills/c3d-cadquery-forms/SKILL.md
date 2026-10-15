---
name: c3d-cadquery-forms
description: "Use when writing CadQuery src/model.py - baseline, part, detail, refine, rebuild or repair: build the planned forms and get past the OCC kernel. Gives the kernel's native makers per form as an optional toolbox, the probed kernel rules that decide whether the script builds at all, a few strong optional practices, and the four traps our own lint and runtime wrapper are written to catch."
license: Apache-2.0
compatibility: CadQuery 2.8 on OCP; one module src/model.py exporting a module-level `result`.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Every kernel rule below was probed against the CadQuery 2.8.0 installed in this repo on 2026-08-25 and cross-checked with the cadquery lint, the run_cq wrapper and the language contract. What is NOT available is score evidence. This harness has ZERO graded cadquery bench runs, so nothing here is ranked by measured defect frequency the way the blender and urdf bundles are. Routed by default since 2026-09-22 (C3D_SKILLS_UNVERIFIED defaults on); still unranked until cadquery has 20 graded runs.'
  verified: "2026-08-25"
  target_metric: "build_failure_rate"
  target_direction: "down"
  target_unit: "fraction of rounds whose build failed"
  target_measurable: "true"
  target_baseline: "none: n=0, bench/out holds no graded cadquery run (2026-08-25)"
---

# CadQuery: forms, and the kernel rules that gate them

**Evidence warning, read it once:** we have no graded CadQuery runs. The API rules below are
verified against the installed kernel; nothing here is ranked by measured defect frequency.

## Form to technique — an optional toolbox

The plan names language-neutral forms; the kernel has a native maker for most of them. These
are options under their real API names, not a mapping to follow.

| form | a native option |
|---|---|
| slab / block / panel | `.box(x, y, z)`, `centered=(True, True, False)` to stand on zero |
| shaft / disc / cylinder | `.circle(r).extrude(h)`; `.cylinder(h, r)` takes height FIRST |
| revolved profile — vase, finial, baluster, dome, barrel | closed half-profile on `"XZ"` (`.polyline`/`.spline` + `.close()`) then `.revolve(360, (0,0,0), (0,1,0))`; keep the profile at radius >= 0 |
| ring / band / torus | `.moveTo(R, 0).circle(r).revolve(angle, ...)` — see kernel rule 1 |
| extruded outline with softened edges | outline `.polyline().close().extrude(t)`, then `.edges("|Z").fillet(r)`; `cq.Sketch().rect().vertices().fillet()` for a rounded rectangle |
| tube swept along a path | `.circle(r).sweep(path)`; the profile plane must be perpendicular to the path start |
| tapered solid — horn, spout, mast | `.circle(r1).workplane(offset=h).circle(r2).loft()`, or `.extrude(h, taper=deg)` |
| lofted transition between sections | `.loft()` between profiles with matching vertex counts (circle to circle, rect to rect) |
| shell with wall thickness | `.faces(">Z").shell(-t)` from ONE open face; the kernel refuses it on a lofted round body — subtract a shrunken inner loft instead |
| carved cavity / slot / bore | `.cut(cutter)`, `.hole(d)`, `.cutBlind(-depth)`, `.slot2D(length, width)` |
| faceted solid — die, gem | `.polygon(n, d).extrude(h)`, facets via `taper=` or a loft between polygons |
| repeated feature — grid, ring of N, point list | `.rarray(dx, dy, nx, ny)`, `.polarArray(radius, start, angle, count)` or `.pushPoints([...])` before the feature |
| mirror pair | `.mirror("XZ")` on the piece, then place both in the Assembly |
| where a feature goes | string selectors `"|Z"`, `">Z"`, `"<Y"`, `"%CIRCLE"`, `"#Z"`; `cq.selectors.BoxSelector(p0, p1)` or `NearestToPointSelector(p)` when a rotated feature is invisible to the string form; `.faces(">Z").workplane()` / `.workplane(offset=h)` to build on a face |
| embossed label | `.text(txt, size, depth)` — may raise headless (no fonts), so `try/except` it |

## Kernel rules — probed against CadQuery 2.8.0 in this repo

1. **`.center()` before a revolve is fatal; use `.moveTo()`.** `Workplane("XZ").center(R, 0)
   .circle(r).revolve(360, ...)` raises `StdFail_NotDone: BRep_API: command not done`. The
   identical chain with `.moveTo(R, 0)` builds a valid torus. `.center()` moves the
   workplane origin, so the profile ends up sitting on the axis. Passing an explicit axis
   does not rescue it — both forms fail.
2. **A union that does not overlap fails silently.** Two spheres placed exactly tangent
   union into **2 solids** and still report `isValid() == True`; the same pair with 0.5 mm
   of overlap gives 1 solid. `.isValid()` will not catch this and neither will a render —
   `check_connectivity` reports the second solid as a stray island. `len(wp.solids().vals())`
   after a union or a cut catches it before a build; a through cut that splits a part also
   leaves 2 valid solids.
3. **Each plan part is its own Assembly child.** A pair unioned across a plan boundary reads
   as a missing part in `check_contract`. Touching parts: a gap ≤ 2 mm is contact, overlap
   > 2 mm is WARN, > 10 mm is ERROR (`check_connectivity`; the table is in `c3d-part-contact`).
4. **Keep solids closed.** Export runs through STL as well as GLB; `.shell()` keeps a solid
   watertight, hand-assembled faces do not.
5. Features under ~0.5 mm come out invalid at OCC tolerance.

## Strong options — optional practices

**Fillets the kernel accepts.** Refusal is not monotone in the radius: on a 3 mm shelled
wall a rim fillet passed at 1.4 mm, failed at 1.5 mm, passed again at 2.0 mm and failed at
3.0 mm, so "try a slightly bigger radius" is not a strategy. A conservative budget (the
hint the runtime prints after a refusal says 0.45 x the thinnest adjacent wall), few edges
per `.fillet()` call, and `try/except` around fillets that are only cosmetic is the strong
option. Whether a fillet goes before or after a boolean that touches its edge is not a rule
either way — only the build result counts. Where `.fillet()` is refused on a thin wall, a
`.cut()` with a rounded tool softens the same edge. (The claim that the limit is half the
shortest adjacent *edge* is false here: a 10 mm radius on a 10 mm vertical edge builds.)

**Mating faces.** A mating coordinate computed from the plan numbers of the face it meets
lands inside the gate's contact band (flush counts) without nudging. A cutter that overshoots the face it exits avoids
coplanar-face boolean failures; the coplanar case passed in this probe, so the overshoot is
a habit, not a measured need.

**Selectors after booleans.** Edge indices reshuffle after a boolean; re-select by geometry
(`">Z"`, `BoxSelector`) rather than by a cached index, and print `len(wp.edges(sel).vals())`
when a selector might match nothing — an empty selection raises `ValueError`.

**Measure before you render.** Print per part what the gates read — `BoundingBox()` extents
and `zmin`, `len(solids().vals())`, `Volume()`, `isValid()` — then the track loop: `build`
-> `check_contract` -> `check_connectivity` -> `render_views`. `Volume()` catches
hollow-when-it-should-be-solid, the solid count catches a split, `zmin` catches a floater,
all before a render is spent.

## The four traps our own tooling is written to catch

* **No module-level `result`** — ERROR from `lint:cadquery`. It must not live under
  `if __name__ == "__main__":`, and `sys.exit()` before it is set is its own error.
* **`result` that is not a `cq.Assembly`** — the whole model exports as ONE node called
  `Object`, so the contract gate reports *every* plan part missing. Use
  `result.add(solid, name="PascalName", color=cq.Color(r, g, b))` per plan part; an `.add()`
  without `name=` gets a uuid node name and has the same effect.
* **A trailing selector** — a chain ending `.faces(">Z")` leaves Face objects on the stack.
  The wrapper recovers the parent solid and warns; end the chain on the solid.
* **Radians into a degrees argument** — `.rotate(p0, p1, angle)`, `cq.Location(v, axis, deg)`,
  `.revolve(deg)` and `twistExtrude(h, deg)` all take degrees. A `math.pi` expression in that
  slot is a lint ERROR. Also `cq.Solid.makeSphere(r)` without angle arguments builds a
  hemisphere (measured: z extent half of x and y); use `cq.Workplane("XY").sphere(r)`.

More probe results and the failing/passing chains: `references/kernel-probes.md`.
