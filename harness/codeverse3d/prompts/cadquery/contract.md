# CadQuery authoring contract — track `static_object`, language `cadquery`

## Files
* `src/model.py` — ONE script, `import cadquery as cq` (+ stdlib `math`).  The harness
  imports it in a subprocess and reads the module-level variable **`result`**.
  It exports STEP + STL per part and a GLB with one named node per part.  You never
  export, print to files, or call `show_object`.

## `result`
* Preferred: `result = cq.Assembly(name="<ObjectName>")` with one `assy.add(solid,
  name="<PartName>", color=cq.Color(r, g, b), loc=cq.Location(...))` per plan part.
  Part names PascalCase, unique, exactly as in the plan.
* Allowed for a single-part object: `result = <cq.Workplane>` (one solid).
* `result` must exist at import time (module level, NOT under `if __name__ == "__main__"`).

## Frame, units, placement
* **Z up, -Y front, +X right, meters** (the harness converts to Y-up GLB).  Author each
  part in world coordinates (leave `loc` identity) OR author at the origin and place with
  `loc=cq.Location(cq.Vector(x, y, z))` — pick one style per file.
* Lowest point at z = 0, footprint centred on Z.  Real-world dimensions.
* Angles: `.rotate(p0, p1, deg)` and `cq.Location(v, axis, deg)` take **degrees**; `math`
  functions take radians.

## Allowed imports
`cadquery` (as `cq`), `math`, `itertools`, `functools`, `typing`, `dataclasses`.
No `OCP`/`OCC` direct calls, no numpy, no file I/O, no `cq.exporters`, no
`cq.importers`, no `import codeverse3d`.

## Forbidden
* No `show_object`, `exporters.export`, `open(`, `os`, `sys`, `subprocess`, network.
* Each part a valid solid (`.val().isValid()`), no empty Workplanes; fillets
  < 0.45 × thinnest adjacent wall; feature sizes ≥ 0.5 mm.
* Do not union parts across plan boundaries (keep one solid per part); small intentional
  overlap of **0.5–2 mm** between touching parts is required — that is how they "weld";
  deeper than 2 mm and the connectivity gate calls it interpenetration.

## Self-check (put at the end of the file)
```python
def _selfcheck(assy) -> None:
    names = [c.name for c in assy.children]
    assert names and len(names) == len(set(names)), f"duplicate/missing part names {names}"
    for c in assy.children:
        assert c.obj.val().isValid(), f"invalid solid in {c.name}"
```

## COMPLETE minimal example (verified with CadQuery 2.8)
```python
import cadquery as cq
import math

# --- plan numbers (metres) -------------------------------------------------
TOP_W, TOP_D, TOP_T, TOP_Z = 0.60, 0.40, 0.025, 0.45   # table top, its top face at 0.45
LEG_S, LEG_INSET = 0.035, 0.04                          # square leg side, inset from edge
WELD = 0.001                                            # legs poke 1 mm into the top

def build_top() -> cq.Workplane:
    return (cq.Workplane("XY")
            .box(TOP_W, TOP_D, TOP_T, centered=(True, True, False))   # z from 0 up
            .edges("|Z").fillet(0.02)            # round the 4 vertical corners
            .edges(">Z").chamfer(0.003)          # soften the top rim
            .translate((0, 0, TOP_Z - TOP_T)))

def build_leg(sx: int, sy: int) -> cq.Workplane:
    x = sx * (TOP_W / 2 - LEG_INSET - LEG_S / 2)
    y = sy * (TOP_D / 2 - LEG_INSET - LEG_S / 2)
    h = TOP_Z - TOP_T + WELD
    leg = cq.Workplane("XY").rect(LEG_S, LEG_S).extrude(h)          # z 0 → h
    try:
        leg = leg.edges("|Z").fillet(0.004)                            # cosmetic: may fail
    except Exception:
        pass
    return leg.translate((x, y, 0))

assy = cq.Assembly(name="SideTable")
assy.add(build_top(), name="Top", color=cq.Color(0.55, 0.33, 0.16))
for i, (sx, sy) in enumerate([(1, 1), (-1, 1), (-1, -1), (1, -1)]):
    assy.add(build_leg(sx, sy), name=f"Leg{i + 1}", color=cq.Color(0.2, 0.2, 0.22))

_selfcheck(assy)
result = assy
```
