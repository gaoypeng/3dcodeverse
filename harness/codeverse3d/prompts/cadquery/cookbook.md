# CadQuery cookbook — static objects, CadQuery 2.4–2.8

Every snippet runs as-is (the harness test suite executes them in order with plain
`python3`).  Z-up, −Y front, meters, PascalCase part names, `result = cq.Assembly`.
The harness inlines the relevant chapters into your prompts; the full file is at
`.3dcode/cookbook.md` in your workspace.

## Skeleton

```python
import cadquery as cq
import math

# ---- plan numbers (metres) --------------------------------------------------
BODY_W, BODY_D, BODY_H, WALL = 0.120, 0.080, 0.040, 0.002
LID_T, LIP = 0.003, 0.0015
WELD = 0.002                                       # parts overlap by this to "weld"

def safe_fillet(wp: cq.Workplane, selector: str, r: float) -> cq.Workplane:
    """Cosmetic fillet that never breaks the build: shrink, then give up."""
    for rr in (r, r * 0.5, r * 0.25):
        try:
            return wp.edges(selector).fillet(rr)
        except Exception:
            continue
    return wp

def safe_chamfer(wp: cq.Workplane, selector: str, d: float) -> cq.Workplane:
    try:
        return wp.edges(selector).chamfer(d)
    except Exception:
        return wp

def bbox(wp: cq.Workplane):
    """(xmin, ymin, zmin, xmax, ymax, zmax) of the FIRST solid — use to self-check."""
    b = wp.val().BoundingBox()
    return (b.xmin, b.ymin, b.zmin, b.xmax, b.ymax, b.zmax)

def build_body() -> cq.Workplane:
    outer = (cq.Workplane("XY").box(BODY_W, BODY_D, BODY_H, centered=(True, True, False))
             .edges("|Z").fillet(0.006))                            # z from 0 upward
    body = outer.faces(">Z").shell(-WALL)                           # open-top box, 2 mm walls
    return safe_fillet(body, "<Z", 0.001)                           # soften the bottom rim LAST (r < wall/2)

def build_lid() -> cq.Workplane:
    top = (cq.Workplane("XY").box(BODY_W, BODY_D, LID_T, centered=(True, True, False))
           .edges("|Z").fillet(0.006).translate((0, 0, BODY_H)))
    lip = (cq.Workplane("XY").rect(BODY_W - 2 * WALL - 0.0004, BODY_D - 2 * WALL - 0.0004)
           .rect(BODY_W - 2 * WALL - 0.0004 - 2 * LIP, BODY_D - 2 * WALL - 0.0004 - 2 * LIP)
           .extrude(0.004 + WELD).translate((0, 0, BODY_H - 0.004)))   # lip drops into the box
    return top.union(lip)

assy = cq.Assembly(name="Enclosure")
assy.add(build_body(), name="Body", color=cq.Color(0.25, 0.25, 0.27))
assy.add(build_lid(), name="Lid", color=cq.Color(0.85, 0.55, 0.15))
result = assy
print("body bbox", [round(v, 4) for v in bbox(build_body())])
```

Why: every part is a function returning one solid; `safe_*` keep cosmetic edge work from
killing the build; the lid's lip overlaps the body (weld) so the assembly is connected.

## Workplane idioms (the few you need)

```python
# Workplane names: "XY" (normal +Z, up), "XZ" (normal -Y: the FRONT elevation), "YZ" (normal +X)
# .box(x, y, z) is CENTRED unless centered=(…); .extrude(h) goes along the plane normal from z=0
plate = cq.Workplane("XY").box(0.10, 0.06, 0.004, centered=(True, True, False))   # bottom at z=0
# workplane offset + faces: stack features relative to the last face
boss = (plate.faces(">Z").workplane()            # plane on the top face
        .center(0.03, 0.0)                        # local offset (resets per workplane)
        .circle(0.008).extrude(0.012))            # boss grows UP from the face
# hole through everything from the top face
boss = boss.faces(">Z").workplane().center(-0.03, 0).hole(0.005)   # hole() is a THROUGH hole
# cboreHole / cskHole for counterbores; .hole(d, depth) for blind
# rotate(p0, p1, deg) about an arbitrary axis, translate(v): both RETURN NEW objects
tilted = boss.rotate((0, 0, 0), (1, 0, 0), 15).translate((0, 0.2, 0))
# pushPoints: put features at several local points
grid = (cq.Workplane("XY").box(0.08, 0.08, 0.005, centered=(True, True, False))
        .faces(">Z").workplane().pushPoints([(-0.02, -0.02), (0.02, 0.02)]).hole(0.004))
# selectors: "|Z" edges parallel Z, ">Z" max-Z face/edges, "<Y" min-Y, "%CIRCLE" round edges,
# "#Z" edges perpendicular to Z, "not(|Z)".  Combine: .edges("|Z and >X")
print("boss bbox", [round(v, 4) for v in bbox(boss)])
```

Why: offset workplanes + `faces(">Z").workplane()` build features where they belong
without hand-computing z; `centered=(True, True, False)` makes z = 0 the base so objects
stand on the ground by construction.

## 2D profiles: polyline, spline, slots, text, sketches

```python
# closed polyline profile (in metres) extruded; points in plane-local coords
bracket_profile = [(0, 0), (0.06, 0), (0.06, 0.004), (0.004, 0.004), (0.004, 0.05), (0, 0.05)]
lbracket = cq.Workplane("XZ").polyline(bracket_profile).close().extrude(-0.03)   # XZ normal is -Y: negative = towards +Y
# spline through points (smooth organic outline), mirrored for symmetry
half = [(0, 0), (0.02, 0.003), (0.04, 0.012), (0.05, 0.03), (0.045, 0.05), (0.03, 0.06), (0, 0.062)]
leaf = (cq.Workplane("XY").spline(half, includeCurrent=False).mirrorY().extrude(0.002))
# slot and rounded rect via Sketch API
s = cq.Sketch().rect(0.06, 0.03).vertices().fillet(0.006)
tag = cq.Workplane("XY").placeSketch(s).extrude(0.003)
slot = cq.Workplane("XY").slot2D(0.03, 0.006).extrude(0.002)          # length 30 mm, width 6 mm
# text (embossed label), font size in metres; fonts may be missing headless → wrap it
try:
    label = cq.Workplane("XY").text("C3V", 0.012, 0.001, halign="center", valign="center")
except Exception:
    label = None
print("lbracket bbox", [round(v, 4) for v in bbox(lbracket)], "leaf ok", leaf.val().isValid())
```

Why: `Sketch().rect().vertices().fillet()` is the robust rounded rectangle; polylines need
`.close()`; `mirrorY()` mirrors the pending wire about the plane's Y axis.

## Extrude, revolve, loft, sweep (the solid makers)

```python
# revolve: profile in the XZ plane, axis = local Y of XZ plane = world Z; r >= 0 always
vase_profile = [(0.0, 0.0), (0.035, 0.0), (0.045, 0.05), (0.03, 0.12), (0.02, 0.18), (0.025, 0.20), (0.0, 0.20)]
vase = cq.Workplane("XZ").polyline(vase_profile).close().revolve(360, (0, 0, 0), (0, 1, 0))
# loft between sections on parallel workplanes (same edge count/orientation is safest: circle→circle, rect→rect)
funnel = (cq.Workplane("XY").circle(0.04)
          .workplane(offset=0.05).circle(0.015)
          .workplane(offset=0.03).circle(0.015).loft(ruled=False))
# sweep a circle along a 3D path (tube / handle / pipe); profile plane must be perpendicular to the path start
path = cq.Workplane("XZ").spline([(0, 0), (0.03, 0.02), (0.06, 0.06), (0.06, 0.10)])
handle = (cq.Workplane("XY").circle(0.005).sweep(path, isFrenet=True))
# twisted / tapered extrude
twist = cq.Workplane("XY").rect(0.02, 0.02).twistExtrude(0.06, 90)
taper = cq.Workplane("XY").circle(0.03).extrude(0.05, taper=10)
print("vase ok", vase.val().isValid(), "funnel ok", funnel.val().isValid(), "handle ok", handle.val().isValid())
```

Why: these four make 90 % of non-boxy shapes; the classic failures are revolve profiles
crossing the axis (r < 0), lofts between wires with different vertex counts, and sweep
profiles not perpendicular to the path start.  Verify with `.val().isValid()`.

## Shell, fillet, chamfer — robustly

```python
cup = (cq.Workplane("XY").circle(0.04).extrude(0.09, taper=-3)    # flared cup (taper < 0 widens)
       .faces(">Z").shell(-0.003))                        # open top, 3 mm wall
cup = safe_fillet(cup, ">Z", 0.001)                       # rim round-over; may legally fail on thin walls
# shell() REFUSES lofted round bodies: hollow those by subtracting an inner loft instead
shade = (cq.Workplane("XY").circle(0.06).workplane(offset=0.12).circle(0.10).loft()
         .cut(cq.Workplane("XY").circle(0.057).workplane(offset=0.12).circle(0.097).loft()
              .translate((0, 0, 0.003))))                 # 3 mm wall, 3 mm floor
box_ = (cq.Workplane("XY").box(0.06, 0.04, 0.03, centered=(True, True, False))
        .edges("|Z").fillet(0.008)                        # vertical corners first (big radius)
        .edges(">Z").chamfer(0.002))                      # then the top rim (small)
print("cup ok", cup.val().isValid(), "shade ok", shade.val().isValid(), "box ok", box_.val().isValid())
```

Rules: fillet BEFORE booleans that would split the edge; radius < 0.45 × the thinnest
adjacent wall; biggest fillets first; shell from a single open face; if `BRep_API:
command not done` appears, halve the radius or fillet fewer edges (`"|Z"` only).

## Booleans (union / cut / intersect) and cavities

```python
block = cq.Workplane("XY").box(0.08, 0.05, 0.03, centered=(True, True, False))
cutter = (cq.Workplane("XY").box(0.06, 0.03, 0.03, centered=(True, True, False))
          .translate((0, 0, 0.006 + 0.0001)))             # cutter overshoots the top face by 6 mm+
pocketed = block.cut(cutter)                                # 6 mm floor left
pin = cq.Workplane("XY").circle(0.004).extrude(0.05).translate((0.03, 0, 0.02))
joined = pocketed.union(pin).clean()                        # clean() merges coplanar faces
core = block.intersect(cq.Workplane("XY").sphere(0.045).translate((0, 0, 0.015)))
print("pocket solids", len(pocketed.solids().vals()), "joined valid", joined.val().isValid())
```

Why: booleans fail on exactly coplanar faces — always overshoot cutters by ≥ 0.1 mm;
check `len(result.solids().vals()) == 1` after a cut (a cut that splits a part is a bug).

## Assemblies: names, colours, locations

```python
def place(part: cq.Workplane, xyz=(0, 0, 0), rz_deg: float = 0.0) -> cq.Location:
    """Location = translation + yaw (degrees).  Compose bigger rotations via .rotate() on the solid."""
    return cq.Location(cq.Vector(*xyz), cq.Vector(0, 0, 1), rz_deg)

stool = cq.Assembly(name="Stool")
seat = cq.Workplane("XY").circle(0.17).extrude(0.04).translate((0, 0, 0.41))
stool.add(seat, name="Seat", color=cq.Color(0.55, 0.34, 0.16))
leg = cq.Workplane("XY").circle(0.015).extrude(0.41 + WELD)      # authored at the origin
for i in range(3):
    a = math.radians(90 + 120 * i)
    stool.add(leg, name=f"Leg_{i}", color=cq.Color(0.3, 0.3, 0.32),
              loc=place(leg, (0.12 * math.cos(a), 0.12 * math.sin(a), 0)))
# sub-assemblies nest: stool.add(other_assy, name="Footrest", loc=...)
names = [c.name for c in stool.children]
print("stool parts", names, "valid", all(c.obj.val().isValid() for c in stool.children))
```

Why: one authored solid + N locations = instances that stay identical; names must be
unique (the GLB node names come from them); `cq.Color` takes 0–1 floats.

## Patterns: rect arrays, polar arrays, point lists

```python
grille = (cq.Workplane("XY").box(0.10, 0.06, 0.002, centered=(True, True, False))
          .faces(">Z").workplane().rarray(0.008, 0.008, 10, 6).hole(0.004))      # 10×6 holes
flange = (cq.Workplane("XY").circle(0.05).extrude(0.006)
          .faces(">Z").workplane().polarArray(0.04, 0, 360, 6).hole(0.005))      # 6 bolt holes
spokes = cq.Workplane("XY")
for i in range(8):
    a = 360 / 8 * i
    spokes = spokes.union(cq.Workplane("XY").box(0.08, 0.004, 0.004, centered=(False, True, False))
                          .rotate((0, 0, 0), (0, 0, 1), a))
print("grille holes ok", grille.val().isValid(), "spokes valid", spokes.val().isValid())
```

Why: `rarray`/`polarArray` + one cut are 10× faster than loops of cuts and never produce
slivers; loops of `.union` are fine for ≤ 12 items.

## Mechanical parts (recipes with numbers)

```python
def l_bracket(w=0.06, h=0.05, depth=0.03, t=0.004, hole_d=0.005) -> cq.Workplane:
    """Steel L-bracket with two slots; t 3–5 mm, fillet inside corner r = t."""
    prof = [(0, 0), (w, 0), (w, t), (t, t), (t, h), (0, h)]
    b = cq.Workplane("XZ").polyline(prof).close().extrude(-depth)
    b = safe_fillet(b, "|Y", t * 0.8)
    b = b.faces(">Z").workplane().center(0, 0).hole(hole_d)
    return b

def enclosure(w=0.12, d=0.08, h=0.04, wall=0.002, lid_t=0.003):
    """Two parts: open box + lid with a drop-in lip (1.5 mm)."""
    body = (cq.Workplane("XY").box(w, d, h, centered=(True, True, False))
            .edges("|Z").fillet(0.005).faces(">Z").shell(-wall))
    lid = (cq.Workplane("XY").box(w, d, lid_t, centered=(True, True, False)).edges("|Z").fillet(0.005)
           .translate((0, 0, h)))
    lip = (cq.Workplane("XY").rect(w - 2 * wall - 0.0004, d - 2 * wall - 0.0004)
           .rect(w - 2 * wall - 0.0034, d - 2 * wall - 0.0034).extrude(0.005).translate((0, 0, h - 0.003)))
    return body, lid.union(lip)

def spur_gear(module=0.002, teeth=20, thickness=0.008, bore=0.006) -> cq.Workplane:
    """Visual-grade gear: trapezoid teeth on a disc (not an involute; fine for renders)."""
    r_p = module * teeth / 2; r_o = r_p + module; r_r = r_p - 1.25 * module
    gear = cq.Workplane("XY").circle(r_r).extrude(thickness)
    tooth = (cq.Workplane("XY").polyline([(r_r - 0.0005, -module * 0.9), (r_o, -module * 0.45),
                                          (r_o, module * 0.45), (r_r - 0.0005, module * 0.9)]).close()
             .extrude(thickness))
    for i in range(teeth):
        gear = gear.union(tooth.rotate((0, 0, 0), (0, 0, 1), 360 / teeth * i))
    return gear.faces(">Z").workplane().hole(bore)

def knob(d=0.03, h=0.018, ribs=12) -> cq.Workplane:
    """Rotary knob: tapered cylinder, rib grooves, domed top, D-shaft bore."""
    k = cq.Workplane("XY").circle(d / 2).extrude(h, taper=6)
    k = safe_fillet(k, ">Z", 0.003)
    groove = cq.Workplane("XY").box(0.0015, 0.004, h * 0.7, centered=(True, True, False)).translate((0, d / 2, h * 0.35))
    for i in range(ribs):
        k = k.cut(groove.rotate((0, 0, 0), (0, 0, 1), 360 / ribs * i))
    return k.faces("<Z").workplane().hole(0.006, 0.010)

parts = [l_bracket(), *enclosure(), spur_gear(), knob()]
print("mech valid", [p.val().isValid() for p in parts])
```

Dimensions (m): bracket t 3–5 mm, holes Ø 4–6 mm; enclosure walls 1.5–2.5 mm, lid 2–3 mm;
gear module 1–3 mm, 12–40 teeth; knob Ø 20–35 mm, h 15–20 mm, shaft Ø 6 mm; handles
Ø 10–14 mm tube; table legs 35–70 mm square; shelf panels 18 mm; plinths 100 mm.

## Common objects — decomposition (CadQuery flavour)

| object | parts | build with |
|---|---|---|
| mug | Body (revolve + shell), Handle (sweep circle along spline, overlap 2 mm) | revolve, sweep |
| bottle | Body (revolve), Cap (cylinder + knurl polarArray cuts) | revolve, polarArray |
| chair | Seat (box + fillets), Legs ×4 (extrude, located), Backrest (box + rotate 8°), Stretchers | box, rotate, Location |
| table | Top (box + fillet), Apron ×4, Legs ×4 | box, translate |
| lamp | Base (revolve), Pole (cylinder), Shade (taper extrude + shell, or loft − inner loft) | extrude(taper), shell |
| enclosure | Body (shell), Lid (lip), Buttons (cylinders), Screws (polarArray) | shell, rarray |
| wheel | Rim (revolve), Tyre (torus via revolve of circle), Spokes (polar union), Hub | revolve, polar |
| bracket | L-profile polyline extrude + slots | polyline, cutThruAll |

## Measure while you build (numbers beat squinting)

```python
def report(wp: cq.Workplane, name: str = "part") -> dict:
    """Print the facts the harness will measure — call it after building each part."""
    shp = wp.val()
    bb = shp.BoundingBox()
    info = {
        "name": name,
        "extents_m": (round(bb.xlen, 4), round(bb.ylen, 4), round(bb.zlen, 4)),
        "zmin": round(bb.zmin, 4),
        "solids": len(wp.solids().vals()),
        "valid": shp.isValid(),
        "volume_m3": round(shp.Volume(), 8),
        "faces": len(wp.faces().vals()),
    }
    print(f"[report] {info}")
    return info

r = report(build_body(), "Body")
assert r["solids"] == 1 and r["valid"]
assert abs(r["extents_m"][2] - BODY_H) < 1e-6          # plan number check, in code
```

Why: `Volume()` catches hollow-when-it-should-be-solid (and vice versa) instantly;
`solids == 1` catches a cut that split the part; `zmin` catches floaters — all before
spending a render.

## Pitfalls (symptom → cause → fix)

1. **`.cylinder(height, radius)`** — height FIRST; `.box(x, y, z)` is centred unless
   `centered=(…)`; `.circle(r).extrude(h)` starts at the workplane (z = 0).
2. **Degrees vs radians**: `.rotate(..., deg)`, `cq.Location(v, axis, deg)`,
   `revolve(deg)`, `twistExtrude(h, deg)` are DEGREES; `math.sin` wants radians.
3. **`rotate/translate/mirror` return new objects** — assign the result (`wp = wp.rotate(...)`).
4. **`BRep_API: command not done`** → fillet radius ≥ wall, a fillet radius EQUAL to a
   later shell thickness (inner radius 0), fillet after a boolean that split the edge, or
   a degenerate edge.  Halve the radius, shell first and fillet last, select fewer edges,
   use `safe_fillet`.
5. **`ValueError: No edges selected` / "Fillets requires that edges be selected"** →
   the selector matched nothing (rotated features are invisible to `|Z`-style selectors).
   Print `len(wp.edges(sel).vals())`; use `cq.selectors.BoxSelector(p0, p1)` or
   `NearestToPointSelector(p)`.
6. **Shell fails** → pick ONE open face (`.faces(">Z").shell(-t)`), keep t < smallest
   feature, fillet outside corners first.  Shell never works on lofted round bodies:
   use `extrude(h, taper=…)` + shell, `outer.cut(inner_loft)`, or revolve a profile that
   already contains the wall.
7. **Two solids in one Workplane** (`len(wp.solids().vals()) > 1`) → a cut split your part
   or a union did not touch; overlap parts by ≥ 0.5 mm, check bounds.
8. **Coplanar boolean faces** → invalid/empty result; overshoot cutters 0.1–5 mm; `.clean()`.
9. **`cq.Solid.makeSphere(r)` gives a hemisphere** without angle args; use
   `cq.Workplane().sphere(r)`.
10. **Revolve axis**: in `cq.Workplane("XZ")` the axis `(0,1,0)` is plane-LOCAL = world Z.
    Profile must stay at r ≥ 0.
11. **`centered=False` on `.box`** puts the MIN corner at the origin on all three axes;
    use the tuple form `(True, True, False)` for "centred in XY, base on z = 0".
12. **Tiny features (< 0.3 mm)** at OCC's 1e-7 tolerance produce invalid shapes; keep
    features ≥ 0.5 mm, or model in mm and scale at the end (`.val().scale(0.001)`).
13. **`result` under `if __name__ == "__main__":`** → the harness never sees it.  Module level.
14. **`show_object`, `exporters.export`, file writes** → forbidden; the harness exports.
15. **Selectors after booleans** — edge indices reshuffle; re-select by geometry
    (`">Z"`, `BoxSelector`), never by cached index.
16. **Euler `cq.Location(x, y, z, rx, ry, rz)`** is easy to get wrong — build the solid
    in place with `.rotate(...).translate(...)` and use `Location` only for translation +
    one yaw.
17. **Very slow builds** → long loops of `.union` with fillets inside; fillet once at the
    end, use `rarray`/`polarArray`.
18. **`hole()` on the wrong side** → it cuts in the −normal direction of the workplane;
    stand on the top face (`faces(">Z").workplane()`).

## Self-check (before you call it done)

```python
def selfcheck(assy: cq.Assembly, expected_parts, overall_hint=None, tol=0.01) -> None:
    names = [c.name for c in assy.children]
    assert names == list(dict.fromkeys(names)), f"duplicate part names: {names}"
    missing = [n for n in expected_parts if n not in names]
    assert not missing, f"missing parts: {missing}"
    for c in assy.children:
        shp = c.obj.val() if hasattr(c.obj, "val") else c.obj
        assert shp.isValid(), f"invalid solid: {c.name}"
        assert len(c.obj.solids().vals()) == 1, f"{c.name} is {len(c.obj.solids().vals())} solids (must be 1)"
    bb = assy.toCompound().BoundingBox()
    assert abs(bb.zmin) < tol, f"lowest point z={bb.zmin:.4f}, expected 0"
    ext = (bb.xlen, bb.ylen, bb.zlen)
    print(f"[selfcheck] parts={len(names)} extents={tuple(round(e, 3) for e in ext)}")
    if overall_hint:
        for a, b in zip(ext, overall_hint):
            assert abs(a - b) < 0.05, f"extents {ext} vs plan {overall_hint}"

selfcheck(stool, ["Seat", "Leg_0", "Leg_1", "Leg_2"], (0.34, 0.34, 0.45))
selfcheck(result, ["Body", "Lid"])
```

Then: `build` → `render_sheet` → `check_connectivity` → `cross_section` through shells
→ `check_contract`.  Fix the worst finding, rebuild, repeat.

## Worked example: dining chair (8 parts, welded, on the ground)

```python
SEAT_W, SEAT_D, SEAT_T, SEAT_H = 0.44, 0.44, 0.035, 0.45
LEG, BACK_H, RAKE_DEG = 0.035, 0.92, 8.0

def chair_seat():
    return (cq.Workplane("XY").box(SEAT_W, SEAT_D, SEAT_T, centered=(True, True, False))
            .edges("|Z").fillet(0.02).edges(">Z").fillet(0.006).translate((0, 0, SEAT_H - SEAT_T)))

def chair_leg(h):
    return cq.Workplane("XY").rect(LEG, LEG).extrude(h + WELD).edges("|Z").chamfer(0.003)

def chair_back():
    """Two raked posts + 3 slats; built upright then raked back by RAKE_DEG about the seat rear edge."""
    post_h = BACK_H - SEAT_H
    posts = cq.Workplane("XY")
    for sx in (-1, 1):
        posts = posts.union(cq.Workplane("XY").rect(LEG, LEG * 0.8).extrude(post_h + WELD)
                            .translate((sx * (SEAT_W / 2 - LEG / 2), 0, SEAT_H - WELD)))
    for i in range(3):
        z = SEAT_H + 0.14 + i * 0.10
        posts = posts.union(cq.Workplane("XY").box(SEAT_W - LEG, 0.012, 0.05).translate((0, 0, z)))
    pivot = (0, SEAT_D / 2 - LEG / 2, SEAT_H - WELD)
    return posts.translate((0, SEAT_D / 2 - LEG / 2, 0)).rotate(pivot, (pivot[0] + 1, pivot[1], pivot[2]), RAKE_DEG)

chair = cq.Assembly(name="DiningChair")
chair.add(chair_seat(), name="Seat", color=cq.Color(0.6, 0.4, 0.2))
for i, (sx, sy) in enumerate([(1, 1), (-1, 1), (-1, -1), (1, -1)]):
    chair.add(chair_leg(SEAT_H - SEAT_T), name=f"Leg_{i}", color=cq.Color(0.45, 0.3, 0.15),
              loc=cq.Location(cq.Vector(sx * (SEAT_W / 2 - LEG / 2 - 0.01), sy * (SEAT_D / 2 - LEG / 2 - 0.01), 0)))
chair.add(chair_back(), name="Backrest", color=cq.Color(0.45, 0.3, 0.15))
stretch = cq.Workplane("XY").box(SEAT_W - LEG - 0.02 + 2 * WELD, 0.02, 0.02).translate((0, 0, 0.20))
chair.add(stretch.translate((0, SEAT_D / 2 - LEG / 2 - 0.01, 0)), name="StretcherBack", color=cq.Color(0.45, 0.3, 0.15))
chair.add(stretch.translate((0, -(SEAT_D / 2 - LEG / 2 - 0.01), 0)), name="StretcherFront", color=cq.Color(0.45, 0.3, 0.15))
selfcheck(chair, ["Seat", "Leg_0", "Leg_1", "Leg_2", "Leg_3", "Backrest", "StretcherBack", "StretcherFront"])
```

Why: the plan's numbers appear once as constants; legs are one solid placed four times;
the backrest is built upright and raked as a whole so its slats stay attached; every
part overlaps a neighbour by `WELD`.
