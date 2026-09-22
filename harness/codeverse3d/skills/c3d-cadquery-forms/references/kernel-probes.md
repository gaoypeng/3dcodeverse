# CadQuery kernel probes — run against the CadQuery 2.8.0 in this repo, 2026-08-25

Each result below is a run, not a recollection. Reproduce any of them in a scratch script.

## Revolve: `.moveTo` vs `.center`

| chain (R = 50 mm, r = 8 mm, plane `"XZ"`) | result |
|---|---|
| `.moveTo(R, 0).circle(r).revolve(360, (0,0,0), (0,1,0))` | valid torus, 1 solid, bbox 116 x 116 x 16 mm |
| `.moveTo(R, 0).circle(r).revolve(360)` (default axis) | same valid torus |
| `.center(R, 0).circle(r).revolve(360, (0,0,0), (0,1,0))` | `StdFail_NotDone: BRep_API: command not done` |
| `.center(R, 0).circle(r).revolve(360)` | `StdFail_NotDone: BRep_API: command not done` |

An explicit axis does not save `.center()`: the workplane origin moved with it, so the
profile is sitting on the axis of revolution either way.

## Fillet: where OCC actually refuses

100 x 60 x 10 mm box, fillet on the four vertical edges (each 10 mm long):

| radius | 2 mm | 4 mm | 4.5 mm | 5 mm | 6 mm | 10 mm |
|---|---|---|---|---|---|---|
| result | ok | ok | ok | ok | ok | ok |

A radius equal to the whole edge length builds. So the widely repeated "radius must be
under half the shortest adjacent edge" is not the rule here.

100 x 60 x **3 mm** slab, fillet on the top rim (the adjacent wall is 3 mm):

| radius | 2.0 | 2.4 | 2.6 | 2.8 | 2.9 | 3.0 |
|---|---|---|---|---|---|---|
| x thickness | 0.67 | 0.80 | 0.87 | 0.93 | 0.97 | 1.00 |
| result | ok | ok | ok | ok | ok | **StdFail_NotDone** |

Cylinder shelled to a 3 mm wall, then a rim fillet:

| radius | 0.5 | 1.0 | 1.4 | 1.5 | 2.0 | 3.0 |
|---|---|---|---|---|---|---|
| x wall | 0.17 | 0.33 | 0.47 | 0.50 | 0.67 | 1.00 |
| result | ok | ok | ok | **fail** | ok | **fail** |

The failures are **not monotone** — 1.5 mm fails while 2.0 mm succeeds. That is the whole
argument for a conservative fixed budget (0.45 x the thinnest adjacent wall, which is the
number the contract states and the number the runtime prints when OCC refuses) plus
`try/except` around fillets that are only cosmetic. Incrementally nudging the radius after a
failure can appear to work and then break on a neighbouring part.

## Booleans: the silent failures

| operation | solids | `isValid()` |
|---|---|---|
| union of two spheres placed exactly tangent | **2** | True |
| union of the same spheres with 0.5 mm overlap | 1 | True |
| box cut through by a full-width cutter | **2** | True |

`isValid()` is not a connectivity check. The only reliable assertion is
`len(wp.solids().vals()) == 1` on anything that is supposed to be one part.

An exactly-coplanar cutter (top face flush with the block's face) succeeded in this probe
and produced the same face count as an overshooting cutter — but the cookbook records the
failing case, so keep the >= 0.1 mm overshoot as a habit rather than relying on luck.

## Sphere helper

`cq.Solid.makeSphere(0.02)` -> bbox 40 x 40 x **20** mm: a hemisphere.
`cq.Workplane("XY").sphere(0.02)` -> bbox 40 x 40 x 40 mm. The lint warns on the first form.

## Trailing selector

`cq.Workplane("XY").box(...).faces(">Z")` leaves `Face` objects on the stack. The harness
wrapper falls back to `findSolid()` and records a warning; a chain that ends on a selector
with no recoverable parent is a hard export error.
