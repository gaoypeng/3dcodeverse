---
name: 3d-lang-cadquery
description: "Use when writing, refining or repairing a static_object in CadQuery (language cadquery): the module-level `result` assembly the wrapper reads, named parts and instances, the Z-up metre frame, the lint's import and call lists, the thresholds check-contract / check-connectivity / detail_budget measure, the OCC kernel facts that fail a build, and the 3dcode loop."
license: Apache-2.0
compatibility: CadQuery 2.8 (OCC B-rep kernel) run in a subprocess by the 3dcode build; exports STEP + STL + a GLB with one node per part.
metadata:
  verified: "2026-11-07"
---

# CadQuery — static objects

You drive a real B-rep kernel: shapes are solids with topology, and the kernel refuses an
operation it cannot make valid.  The build owns the export and the cameras: never save,
render or `show_object`.

## Files and `result`
* `src/model.py` is the entry.  The build runs it in a subprocess (`src/` first on `sys.path`,
  so a helper module beside it imports) and reads the module-level variable **`result`**; it
  must exist at import time (not under `if __name__ == "__main__"`).  `random` and numpy are
  seeded before the module runs; nothing may depend on time — the build runs twice (measure,
  render) and both must match.
* `result = cq.Assembly(name="<ObjectName>")` with one
  `.add(solid, name="<PartName>", color=cq.Color(r, g, b), loc=cq.Location(...))` per plan part.
  Part names PascalCase, unique, exactly the plan's (they become the GLB node names); instances
  `Name_0..Name_N-1`.  `cq.Color` takes 0–1 floats.  Assemblies nest; a child's `loc` composes
  with its parent's.
* A bare `result = <cq.Workplane>` builds one solid exported as the node `Object` (lint WARN;
  the plan's part names are then not in the GLB).  A chain ending on a selector exports the
  parent solid with a warning; no recoverable solid errors naming the stack types; a bare
  `cq.Face` is "contains no solid".
* `references/interface.md` is the interface, executable.

## Frame, units, placement (`3dcode check-contract`)
* Z up, −Y front, +X right, metres at real scale (the wrapper converts to Y-up GLB).  Author
  in world coordinates (identity `loc`) or at the origin and place with
  `loc=cq.Location(cq.Vector(x, y, z))`.  Export is AS AUTHORED: seat the assembly on the
  ground yourself.
* Lowest point within 10 mm of z = 0 (WARN; 30 mm ERROR).  Footprint centre within
  max(10 mm, 10 % of the footprint extent) of the up axis (WARN).  Each part's bbox and the
  overall bbox within max(10 mm, 10 % of the plan extent) per axis (WARN; 3 × ERROR).  A part
  the plan does not name is an INFO — the judge still sees the stray solid.
* Angles: `.rotate(p0, p1, deg)`, `cq.Location(v, axis, deg)`, `.revolve(deg)`,
  `.twistExtrude(h, deg)` take DEGREES; `math` takes radians.  The lint checks three-argument
  `.rotate(...)` (a `math.pi` expression there is an ERROR).

## Contact (`3dcode check-connectivity`)
* Surface gap ≤ 2 mm is contact; overlap > 2 mm WARN "interpenetrate", > 10 mm with ≥ 2 % of
  one part's surface inside ERROR (judge cap 0.70).  Parts under 10 mm are skipped.
* Every part must be in the ground-touching component; an unreachable part is a floating ERROR
  (cap 0.60) — two parts touching only each other are both floating.  The finding prints the
  translation that closes the gap.
* A second solid inside a part (a cut that split it, a union that did not meet) smaller than
  5 % of its largest extent is a stray-island WARN.  Two plan parts unioned into one read as a
  missing part.
* Budgets (`detail_budget`, sized from parts × instances): < 350 triangles per built object
  after tessellation WARN (floor clamped to 1 500–40 000), > 20 000 per built object ERROR
  (ceiling clamped to 60 000–600 000); a build over 3 s per built object (≥ 20 s) WARN;
  killed at 300 s.

## Imports and calls (the lint)
* Allowed: `cadquery` (as `cq`), `math`, `random`, `numpy`, `itertools`, `functools`,
  `collections`, `typing`, `dataclasses`, `enum`, `copy`, `statistics`, `operator`; anything
  else WARN "may not exist in the build sandbox".
* ERROR imports: `os`, `sys`, `pathlib`, `subprocess`, `shutil`, `socket`, `urllib`, `requests`,
  `http`, `ftplib`, `smtplib`, `webbrowser`, `ctypes`, `pickle`, `multiprocessing`, `threading`,
  `importlib`, `OCP`, `OCC`, `ocp_vscode`, `cq_editor`, `cq_warehouse`, `build123d`.
* ERROR calls: `show_object`, `show`, `open(`, `cq.exporters.*`, `cq.importers.*`, `.save()` /
  `.export*()` on the assembly, `exit` / `quit`.

## Kernel facts (what the build reports as an error)
* `cq.Workplane("XY")` normal +Z (up), `"XZ"` normal −Y (the front elevation), `"YZ"` normal
  +X.  `.extrude(h)` goes along the normal; `.hole(d)` cuts in −normal.
* `.box(x, y, z)` is centred unless `centered=(…)` — `centered=False` puts the MIN corner at the
  origin on all axes.  `.cylinder(height, radius)` takes height first.
* `.rotate()`, `.translate()`, `.mirror()` return NEW objects — assign the result.
* A fillet or chamfer whose radius reaches the adjacent wall, or whose edges conflict, raises
  `StdFail_NotDone: BRep_API: command not done` and fails the WHOLE build (no GLB); the hint is
  radius < 0.45 × the thinnest adjacent wall.  A cosmetic fillet in `try/except` falling back to
  the unrounded solid is the one allowed exception.  A selector matching nothing raises
  `ValueError`.
* A revolve profile stays at r ≥ 0; in `Workplane("XZ")` the axis `(0, 1, 0)` is plane-local
  (world Z).  `.center()` before a revolve moves the profile onto the axis and the kernel
  refuses; `.moveTo()` does not.
* `cq.Solid.makeSphere(r)` without angles is a hemisphere (lint WARN);
  `cq.Workplane("XY").sphere(r)` is full.  Features under ~0.5 mm come out invalid.  `.text()`
  may raise headless (no fonts).

## The loop
write → `3dcode build` (it surfaces the OCC exceptions with the line) → `3dcode check-contract`
+ `3dcode check-connectivity` → fix → repeat → `3dcode render-sheet` → OPEN the sheet →
`3dcode check` clean → finish.  Detail the brief asks for is scored; a feature the kernel
refused fails the build unless wrapped, and then it is not there.
