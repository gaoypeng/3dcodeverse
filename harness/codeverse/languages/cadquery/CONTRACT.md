# CadQuery authoring contract

You write ONE file, `src/model.py`, in **raw CadQuery** (`import cadquery as cq`, plus `math` /
`random`). The harness imports it in a sandboxed subprocess, reads the module-level `result` and
exports `artifacts/object.glb` (Y-up, named nodes, part colours), `object.step` and `object.stl` itself.

## Frame, units, placement
* **Z is up, -Y is the front**, +X is the right. **Units are meters.**
* The object **stands on z = 0** and its footprint is **centred on the Z axis**.
* Respect the plan bboxes to ±1 cm; attached parts must touch (tiny overlaps are fine, gaps are not).

## `result`
* **Preferred**: `result = cq.Assembly()` with one `.add(shape, name="PascalName", color=cq.Color(r, g, b))`
  per part. Names are the plan's PascalCase part names; instances `Leg_0 … Leg_3`.
  Place instances with `loc=cq.Location(cq.Vector(x, y, z))` (shape built at the origin), or translate the shape.
  Sub-assemblies are allowed (`legs = cq.Assembly(); ... ; result.add(legs, name="Legs")`).
* Acceptable: `result = <cq.Workplane>` — exported as ONE grey node (no part names → weaker judging).
* `result` must exist **at module level after import** (not only under `if __name__ == "__main__":`).

## Forbidden (lint rejects)
* file IO, `open()`, `cq.exporters.*`, `cq.importers.*`, `.save()` / `.export()`, `show_object()`, `exit()`
* imports other than `cadquery`, `math`, `random`, `numpy` (+ stdlib data helpers)

## CadQuery pitfalls (most build failures)
* `cq.Workplane("XY").box(x, y, z)` is **centred** at the origin; `.cylinder(height, radius)` — height first;
  `.circle(r).extrude(h)` starts at the workplane (z = 0) and goes +Z.
* `.translate()`, `.rotate()`, `.mirror()` return **new** objects — always reassign.
* `.rotate(axisStart, axisEnd, angle)` and `cq.Location(vec, axis, angle)` take **degrees**.
* Fillets/chamfers fail with `BRep_API: command not done` when the radius ≥ the adjacent wall thickness
  or neighbouring fillets conflict: keep radius < 0.45 × min thickness, fillet few edges
  (`.edges("|Z")`, `.edges(">Z")`), fillet before booleans, wrap cosmetic fillets in `try/except`.
* String selectors (`|Z`, `>X`, `%CIRCLE`) are axis aligned; after booleans re-select by geometry, never by index.
* `cq.Solid.makeSphere(r)` → use `cq.Workplane().sphere(r)`; `makeCone(r1, r2, h)` has its base at z = 0.
* Sweeps/lofts: profile perpendicular to the path start; `sweep(path, isFrenet=True)`; loft wires same orientation.
* Revolve on `"XZ"`: `cq.Workplane("XZ").polyline(pts).close().revolve(360, (0, 0, 0), (0, 1, 0))` —
  the axis is in workplane-local coordinates (local Y = world Z); the profile must not cross the axis.
* Keep features ≥ 1e-4 m (OCC tolerance is 1e-7); avoid zero-thickness faces; `.clean()` after unions.
* Patterns: `.rarray()`, `.polarArray()`, `.pushPoints()` beat Python loops of `.union()`.

## Minimal example (copy the pattern)
```python
import cadquery as cq

top = cq.Workplane("XY").box(0.5, 0.5, 0.04).edges("|Z").fillet(0.02).translate((0, 0, 0.58))
leg = cq.Workplane("XY").box(0.04, 0.04, 0.56)

result = cq.Assembly(name="SideTable")
result.add(top, name="TableTop", color=cq.Color(0.55, 0.36, 0.2))
for i, (sx, sy) in enumerate([(1, -1), (-1, -1), (-1, 1), (1, 1)]):
    result.add(leg, name=f"Leg_{i}", loc=cq.Location(cq.Vector(sx * 0.21, sy * 0.21, 0.28)),
               color=cq.Color(0.55, 0.36, 0.2))
```
