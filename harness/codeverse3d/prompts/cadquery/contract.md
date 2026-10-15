# CadQuery authoring contract — track `static_object`, language `cadquery`

## Files
* `src/model.py` is the entry.  The harness runs it in a subprocess (`src/` first on
  `sys.path`, so a helper module beside it imports) and reads the module-level variable
  **`result`**.  It exports STEP + STL and a GLB with one named node per part.  You never
  export, write files, print to files, or call `show_object`.

## `result`
* `result = cq.Assembly(name="<ObjectName>")` with one `.add(solid, name="<PartName>",
  color=cq.Color(r, g, b), loc=cq.Location(...))` per plan part.  Part names PascalCase,
  unique (they become the GLB node names), exactly as in the plan; instances
  `Name_0..Name_N-1`.  `cq.Color` takes 0–1 floats.  Assemblies nest; a child's `loc`
  composes with its parent's.
* A bare `result = <cq.Workplane>` builds (one solid, exported as the node `Object`), with a
  lint WARN and a build warning: the plan's part names are then not in the GLB.
* `result` must exist at import time (module level, NOT under `if __name__ == "__main__"`).
* Each part is a solid.  A chain that ends on a selector (`.faces(">Z")`, `.edges("|Z")`)
  exports the parent solid with a build warning; with no recoverable solid the build
  errors naming the stack types.  A bare `cq.Face` added to the assembly is flagged
  "contains no solid".

## Frame, units, placement — measured by `check_contract`
* **Z up, -Y front, +X right, metres at real-world scale** (the harness converts to Y-up
  GLB).  A part may be authored in world coordinates (identity `loc`) or at the origin and
  placed with `loc=cq.Location(cq.Vector(x, y, z))`; the wrapper applies `loc`.
* Lowest point within 10 mm of z = 0 (WARN; 30 mm is ERROR).  Footprint centre within
  max(10 mm, 10 % of the footprint extent) of the up axis (WARN).  Each part's bbox and the
  overall bbox within max(10 mm, 10 % of the plan extent) per axis (WARN; 3× that is ERROR).
  An exported part the plan does not name is an INFO — the judge still sees it.
* Angles: `.rotate(p0, p1, deg)`, `cq.Location(v, axis, deg)`, `.revolve(deg)` and
  `.twistExtrude(h, deg)` take **degrees**; `math` functions take radians.  The lint checks
  the three-argument `.rotate(...)` only (a `math.pi` expression there is an ERROR).

## Contact — measured by `check_connectivity`
* Touching parts: a surface gap ≤ 2 mm counts as contact; overlap > 2 mm is WARN
  "interpenetrate", > 10 mm (with at least 2 % of one part's surface inside) is ERROR (judge
  cap 0.70).  Parts whose largest extent is under 10 mm are skipped.
* Every part must be in the ground-touching component: a part the contact graph cannot reach
  from the parts on z = 0 is a floating ERROR (judge cap 0.60) — two parts touching only each
  other are both floating.  The finding prints the translation that closes the gap.
* Islands: a second solid inside a part (a cut that split it, a union that did not meet)
  smaller than 5 % of that part's largest extent is a stray-island WARN.  Each plan part is
  its own assembly child — two plan parts unioned into one read as a missing part.
* Budgets (`detail_budget`, sized from the plan's part × instance count): fewer than 350
  triangles per built object after tessellation is a WARN (floor clamped to 1 500 – 40 000),
  more than 20 000 per built object an ERROR (ceiling clamped to 60 000 – 600 000); a build
  over 3 s per built object (at least 20 s) is a WARN, and the build is killed at
  `build_timeout_s` (300 s by default).

## Imports — the lint's list
* Allowed: `cadquery` (as `cq`), `math`, `random`, `numpy`, and the stdlib data helpers
  `itertools`, `functools`, `collections`, `typing`, `dataclasses`, `enum`, `copy`,
  `statistics`, `operator`.  Anything else is a WARN "may not exist in the build sandbox".
* Forbidden (lint ERROR): `os`, `sys`, `pathlib`, `subprocess`, `shutil`, `socket`,
  `urllib`, `requests`, `http`, `ftplib`, `smtplib`, `webbrowser`, `ctypes`, `pickle`,
  `multiprocessing`, `threading`, `importlib`, `OCP`, `OCC`, `ocp_vscode`, `cq_editor`,
  `cq_warehouse`, `build123d`.
* Forbidden calls (lint ERROR): `show_object`, `show`, `open(`, `cq.exporters.*`,
  `cq.importers.*`, `.save()` / `.export*()` on the assembly, `exit` / `quit`.

## Kernel facts — what the build reports as an error
* `cq.Workplane("XY")` has normal +Z (up), `"XZ"` normal -Y (the front elevation), `"YZ"`
  normal +X.  `.extrude(h)` goes along the plane normal from the workplane; `.hole(d)` cuts
  in the -normal direction.
* `.box(x, y, z)` is centred on the workplane unless `centered=(…)` — `centered=False`
  puts the MIN corner at the origin on all three axes.  `.cylinder(height, radius)` takes
  height first.
* `.rotate()`, `.translate()` and `.mirror()` return NEW objects — assign the result.
* OCC refuses a fillet or chamfer whose radius reaches the adjacent wall, or whose edges
  conflict: `StdFail_NotDone: BRep_API: command not done`.  Uncaught, it fails the WHOLE build
  (no GLB) with the wrapper's hint (radius < 0.45 × the thinnest adjacent wall is the hint,
  not a gate); a cosmetic fillet wrapped in `try/except` that falls back to the unrounded
  solid is the one allowed exception (harness contract §5).  A selector that matches nothing
  raises `ValueError`.
* A revolve profile must stay at r ≥ 0; in `Workplane("XZ")` the axis `(0, 1, 0)` is
  plane-local (world Z).  `.center()` before a revolve moves the profile onto the axis and
  the kernel refuses; `.moveTo()` does not.
* `cq.Solid.makeSphere(r)` without angle arguments builds a hemisphere (lint WARN);
  `cq.Workplane("XY").sphere(r)` is a full sphere.
* Features under ~0.5 mm come out invalid at OCC tolerance.
* `.text()` may raise headless (no fonts).

## Interface example — runs as-is (CadQuery 2.8); the interface, not a model to copy
Two plan parts, `Body` and `Knob` × 2: a module-level `result`, one named, coloured child per
part, instances `Name_i`, placement by `loc`.  Sizes and positions are the plan rows' numbers.
```python
import cadquery as cq

body = cq.Workplane("XY").box(0.12, 0.08, 0.05, centered=(True, True, False))   # z 0 → 0.05
knob = cq.Workplane("XY").circle(0.01).extrude(0.02)                             # z 0 → 0.02
result = cq.Assembly(name="Object")
result.add(body, name="Body", color=cq.Color(0.55, 0.57, 0.6))
for i, x in enumerate((-0.04, 0.04)):   # each copy sits flush on Body's top face: contact
    result.add(knob, name=f"Knob_{i}", color=cq.Color(0.1, 0.1, 0.1), loc=cq.Location(cq.Vector(x, 0, 0.05)))
```
