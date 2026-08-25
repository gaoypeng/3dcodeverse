---
name: cv3d-cadquery-forms
description: "Use when writing CadQuery src/model.py - baseline, part, detail, refine, rebuild or repair: build the planned forms and get past the OCC kernel. Gives the form-to-technique decision table, the fillet / revolve / boolean rules that decide whether the script builds at all, and the four traps our own lint and runtime wrapper are written to catch."
license: Apache-2.0
compatibility: CadQuery 2.8 on OCP; one module src/model.py exporting a module-level `result`.
metadata:
  evidence: inherited-unverified
  evidence_note: 'Every kernel rule below was probed against the CadQuery 2.8.0 installed in this repo on 2026-08-25 and cross-checked with the cadquery lint, the run_cq wrapper and the language contract. What is NOT available is score evidence. This harness has ZERO graded cadquery bench runs, so nothing here is ranked by measured defect frequency the way the blender and urdf bundles are. Routed off by default until cadquery has 20 graded runs.'
  verified: "2026-08-25"
  target_metric: "build_failure_rate"
  target_direction: "down"
  target_unit: "fraction of rounds whose build failed"
  target_measurable: "true"
  target_baseline: "none: n=0, bench/out holds no graded cadquery run (2026-08-25)"
---

# CadQuery: forms, and the kernel rules that gate them

**Evidence warning, read it once:** we have no graded CadQuery runs. The API rules below are
verified against the installed kernel; the *priority order* is inferred, not measured.

## Form to technique

Plans and judge feedback name language-neutral forms. Build each one as its own kind of
solid — a revolved profile faked from stacked boxes reads as a blocky toy.

| form | build it as |
|---|---|
| slab / block / panel | `.box(x, y, z)`, `centered=(True, True, False)` to stand on zero |
| shaft / disc / cylinder | `.circle(r).extrude(h)`; `.cylinder(h, r)` takes height FIRST |
| revolved profile — vase, finial, baluster, dome, barrel | closed half-profile on `"XZ"` (`.polyline`/`.spline` + `.close()`) then `.revolve(360, (0,0,0), (0,1,0))`; keep the profile at radius >= 0 |
| ring / band / torus | `.moveTo(R, 0).circle(r).revolve(angle, ...)` — see the revolve trap below |
| extruded outline with softened edges | outline `.polyline().close().extrude(t)`, then `.edges("|Z").fillet(r)` |
| tube swept along a path | `.circle(r).sweep(path)`; the profile plane must be perpendicular to the path start |
| tapered solid — horn, spout, mast | `.circle(r1).workplane(offset=h).circle(r2).loft()`, or `.extrude(h, taper=deg)` |
| lofted transition between sections | `.loft()` between profiles with matching vertex counts (circle to circle, rect to rect) |
| shell with wall thickness | `.faces(">Z").shell(-t)` from ONE open face; never on a lofted round body — subtract a shrunken inner loft instead |
| carved cavity / slot / bore | `.cut(cutter)`, `.hole(d)`, `.cutBlind(-depth)` |
| faceted solid — die, gem | `.polygon(n, d).extrude(h)`, facets via `taper=` or a loft between polygons |
| radial array of N | `.polarArray(radius, start, angle, count)` before the feature; loops of `.union` only up to ~12 items |
| mirror pair | `.mirror("XZ")` on the piece, then place both in the Assembly |

## Kernel rules — probed against CadQuery 2.8.0 in this repo, 2026-08-25

1. **`.center()` before a revolve is fatal; use `.moveTo()`.** `Workplane("XZ").center(R, 0)
   .circle(r).revolve(360, ...)` raises `StdFail_NotDone: BRep_API: command not done`. The
   identical chain with `.moveTo(R, 0)` builds a valid torus. `.center()` moves the
   workplane origin, so the profile ends up sitting on the axis. Passing an explicit axis
   does not rescue it — both forms fail.
2. **Fillet failure is not monotone in the radius, so stay well under the budget.** On a
   3 mm slab a top-rim fillet succeeded at 2.9 mm and failed at 3.0 mm; on a 3 mm shelled
   wall it succeeded at 1.4 mm, failed at 1.5 mm, succeeded again at 2.0 mm and failed at
   3.0 mm. "Try a slightly bigger radius" is therefore not a strategy. Keep radii below
   **0.45 x the thinnest adjacent wall** — the budget the language contract states and the
   exact hint the runtime appends to every OCC refusal — fillet before booleans that would
   split the edge, biggest radii first, and wrap purely cosmetic fillets in `try/except`.
   (The common claim that the limit is half the shortest adjacent *edge* is false here: a
   10 mm radius on a 10 mm vertical edge builds fine.)
3. **A union that does not overlap fails silently.** Two spheres placed exactly tangent
   union into **2 solids** and still report `isValid() == True`; the same pair with 0.5 mm
   of overlap gives 1 solid. `.isValid()` will not catch this and neither will a render.
   Assert `len(wp.solids().vals()) == 1` after every union and every cut — a through cut
   that splits a part also leaves 2 valid solids.
4. **`.cut()` is for removing material only.** Do not union across plan boundaries; each
   plan part stays its own solid in the Assembly. Overshoot cutters by >= 0.1 mm.
5. **Touching parts weld by overlapping 0.5-2 mm.** The CadQuery authoring contract's
   own figure, and the overlap its worked example uses; 2 mm is a ceiling, not a target,
   because the connectivity gate calls a deeper overlap interpenetration. Its contact and
   penetration thresholds are stated once, in `cv3d-part-contact`, and not repeated here.
6. **Keep solids closed.** Export runs through STL as well as GLB; `.shell()` keeps a solid
   watertight, hand-assembled faces do not.
7. Feature sizes >= 0.5 mm (OCC's tolerance eats smaller), <= 40 parts, build under 120 s.

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

## Before you finish

Print the facts the harness will measure while you build — extents, `zmin`,
`len(solids().vals())`, `Volume()`, `isValid()` per part — then `build` -> `render_sheet` ->
`check_connectivity` -> `check_contract`. Code to copy: cookbook `cadquery` sections
*Extrude, revolve, loft, sweep*, *Shell, fillet, chamfer — robustly*, *Booleans*,
*Measure while you build*, *Pitfalls*.

More probe results and the failing/passing chains: `references/kernel-probes.md`.
