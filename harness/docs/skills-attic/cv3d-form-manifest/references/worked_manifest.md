# A manifest, worked end to end

The session owns one part of a hand-crank coffee grinder: `BurrHousing`.
Nothing below is invented — every line is traced to a field the harness already
put in the prompt.

## 1. What the prompt gave us

From the ENGINEERING BRIEF block:

```
- reference instance: Hario Skerton Pro, ceramic conical burr, glass jar
- real-world dimensions (m): body height 0.215, jar diameter 0.092,
  crank radius 0.075, burr diameter 0.038, lid diameter 0.058
- sub-assemblies: Crank (handle, shaft, wing nut); BurrHousing (upper burr seat,
  ceramic cone, adjustment ring); Hopper (funnel, lid); Jar (glass body, thread)
- visible from outside: the wing nut, the adjustment ring's knurl, the seam
  between hopper and jar, the crank shaft entering the lid, the pour spout
- inside, NOT visible: the spring under the adjustment nut, the shaft bearing
- it does NOT have: a motor, a power cord, a hopper window
- SIGNATURE FEATURES: 1. the offset crank arm with a wooden knob;
  2. the knurled adjustment ring under the burr; 3. the glass jar with a
  moulded shoulder; 4. the ceramic conical burr visible from above
```

From the plan's part row:

```
name: BurrHousing        bbox: 0.062 x 0.062 x 0.058 m, centre (0, 0, 0.168)
role: "carries the upper burr and the grind-size adjustment"
symmetry: radial        instances: 1
children: [UpperBurrSeat, CeramicCone, AdjustmentRing]
detail_hint: "carries the visible mechanism — model the burr teeth, the knurl
              and the seam where the hopper drops in"
```

Signature features 2 and 4 land here. 1 and 3 belong to `Crank` and `Jar`.

## 2. `src/design/manifest_burr_housing.md`

```
HousingBody — shell with wall thickness — outer d 0.062, wall 0.003, h 0.034,
  base at z 0.151 — coaxial with Z, its top rim is where Hopper seats —
  visible wall thickness (kind 6) at the open top rim — children/UpperBurrSeat
CeramicCone — tapered solid — d 0.038 at base tapering to 0.006 at the tip,
  h 0.026, base at z 0.155 — coaxial, tip up, inside HousingBody —
  taper (kind 2) — signature feature 4
ConeTeeth — surface relief — 14 ribs 0.0012 proud, 0.0015 wide, running up the
  cone — radial array of 14 on the cone flank — surface relief (kind 7) —
  detail_hint "burr teeth"
AdjustmentRing — ring or band — outer d 0.058, inner d 0.048, h 0.008,
  base at z 0.143 — coaxial, immediately under HousingBody — chamfer 0.0008
  on both outer edges (kind 1) — signature feature 2
RingKnurl — surface relief — 36 flutes 0.0006 deep, full ring height —
  radial array of 36 on AdjustmentRing's outer face — surface relief (kind 7)
  — signature feature 2 "knurled"
SeatLip — layered or inset — a 0.002 step sunk 0.0015 into the top rim of
  HousingBody, full circumference — the hopper drops into it —
  cut-out/notch (kind 5) — detail_hint "the seam where the hopper drops in"
```

Six lines. Four refinement kinds (2, 5, 6, 7) plus the chamfer (1) — five kinds
in one part, which is the 1.0 anchor's requirement for the whole object.
`ConeTeeth`, `RingKnurl` and `SeatLip` are **inside** `BurrHousing`; the export
still has exactly one node called `BurrHousing`, so the connectivity gate sees
one part and the penetration test never runs between the cone and its own teeth.

Nothing models the spring or the bearing: they are on the `NOT visible` list.
Nothing models a hopper window: it is on the `does NOT have` list.

## 3. The two ways this goes wrong

**Padded.** The manifest grows to nineteen lines because "a real grinder has
screws". Eight of them become new top-level parts so they get names in the
render. The measured consequence on this harness is not neutral: over 44 static
runs part count correlates **+0.55** with gate ERRORs and **+0.01** with
`geometry_detail` (`docs/COMPLEXITY.md` §2.2). The recorded toaster rebuild that
did exactly this doubled its interpenetrating pairs (4 → 8), nearly tripled the
depth (3.5 mm → 9.0 mm) and lost 0.30 of overall score (§6). If a screw matters,
it is a refinement of kind 8 *inside* the part it fastens, sunk into the surface
so it touches.

**Refinement-free.** Six lines, correct dimensions, every element an
unmodified cylinder or box. The rubric anchor for that is explicit: "Every part
is an unmodified primitive; the only variation between parts is size, position
and colour" → **0.25**. Correct dimensions do not buy detail. Each line needs a
kind before you start typing geometry, because a bevel decided at manifest time
costs one extra argument and a bevel decided after the build costs a rewrite.

## 4. Reading it back

Before you call the part done, read your manifest beside the render sheet and
point at each line in the picture. A line you cannot point at was either never
built or is too small to see. The harness renders objects at 768 x 768
(`runtime_js/render_glb.mjs`), so on a 0.2 m object that fills the frame one
millimetre is roughly three pixels and half a millimetre is not a feature at
all. That is why the 1.0 anchor says "a few mm — screw heads, hinge leaves,
bead mouldings": grow the line to hardware scale or delete it.
