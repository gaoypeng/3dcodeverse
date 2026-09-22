# Reading a contract fix hint and turning it into one edit

Every example below is a real `fix_hint` string from `bench/out` (mined 2026-08-25). The
gate writes the numbers in the language's authoring frame, so they can be used directly.

## 1. One axis short — the common case

```
part 'FacetedRoofCap' bbox deviates from the plan (worst 2.2x tolerance)
part 'FacetedRoofCap': size 262.0x79.2x55.1 vs planned 262.0x88.0x54.0 cm
  (D x-0.0cm, y-8.8cm, z+1.1cm); planned centre (0.000, -0.060, 2.880) m
```

Read it as: two axes are fine, the depth is 8.8 cm short on a planned 88 cm — a clean 10 %,
which is exactly the relative tolerance, so it tipped over at 2.2x. The cap was almost
certainly built from a profile whose depth constant was the *inner* span while the plan
meant the outer one, or a bevel ate the overhang. The edit is one constant, not a rebuild.

Anti-pattern: scaling the whole object by 88/79.2 to "fix the number". That trades one
finding for a wrong overall bbox and a broken contact graph.

## 2. Centre only — the part is the right size, in the wrong place

```
part 'FrontApron': centre off by (x+0.0cm, y-3.0cm, z-0.0cm);
  planned centre (0.000, -0.200, 0.402) m
```

No size clause at all, so the geometry is correct and only the placement is wrong. Move the
part by the negation of the delta. Do not touch its dimensions.

## 3. Both — a part that grew from one end

```
part 'LeftBraceLower': size 18.1x2.3x15.7 vs planned 22.0x3.0x20.0 cm
  (D x-3.9cm, y-0.7cm, z-4.3cm); centre off by (x+2.2cm, y+0.4cm, z-0.6cm)
```

A size delta with roughly half of it echoed in the centre means the part was built from a
fixed origin and stopped short: one end stayed put, the other never reached. Fix the length
constant and the centre follows; correcting the centre alone leaves the size wrong.

## 4. Instances

```
'FrontLeg' (each instance): size 6.5x7.0x30.1 vs planned 3.5x7.0x28.0 cm
  (D x+3.0cm, y-0.0cm, z+2.1cm)
```

`(each instance)` means the gate compared the plan box with the size of ONE copy — here a
3.5 cm leg came out 6.5 cm, most likely a radius used where a diameter was planned. The
other wording, `(all instances)`, means it compared the plan box with the union of every
copy. The gate picks whichever reading is closer to the plan, so you never have to guess
which one the planner meant: build one copy at the planned size and the per-instance
reading passes; build a set whose union matches and the union reading passes.

## 5. Footprint and ground

```
translate everything by (-0.019, +0.006, +0.000) m to centre the footprint
object floats 130.0 mm above the ground
```

Both hints hand you the vector. Apply it to the top-level container, once, at the end of
construction — not per part, or the parts drift relative to each other and the contact
graph changes.

## Order of work on a repair round

1. `check_contract`, and sort the findings by the "worst Nx tolerance" number in the message.
2. Fix ERRORs (ratio above 3x) first; they are the only ones that fail the gate.
3. Change constants, never the built geometry, wherever a constant exists.
4. `build` -> `check_contract` again. A fix that moves a second part into failure means the
   two shared a derived number and one of them was re-typed.
