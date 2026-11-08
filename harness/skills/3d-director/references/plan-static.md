# plan.json for `static_object`

Fields: `object_name`, `summary`, `overall_bbox`, `style_notes`, `parts[]`, `acceptance[]`.
A `bbox` is `{"center": [x, y, z], "extents": [w, h, d]}` in the LANGUAGE's frame (the
`3d-lang-*` skill states it), metres, 3 decimals.  Numbers only — the builder (you, later)
never re-derives a layout.

1. `object_name` PascalCase.  `summary`: what it is, overall size in metres, the recognisable
   features.
2. `overall_bbox`: real-world size of a real reference instance (think of the catalogue item),
   never a unit cube; bottom on the ground, footprint centred on the up axis, so the centre's
   up-coordinate is half the height.
3. `parts[]`: as many as the object really has.  Every nameable feature in the request and every
   DEFINING feature of the category (a violin's f-holes, a clock's hands, a wrench's jaws) is its
   own part or named with numbers in another part's `description` — a feature named nowhere is
   not built and the judge marks it missing.  Per part:
   * `name` PascalCase, unique; `role` one line; `description`: what it IS, with its numbers —
     profile, edge treatment, counted repeats (a plain description builds a plain primitive);
   * `bbox`: parts that touch share a face or overlap by at most 0.002 m (deeper is
     interpenetration, a gap over 0.002 m is no contact); no part bbox outside `overall_bbox`;
   * `attach_to`: the ONE part it is joined to (the main body has none; must name a real part);
   * `material`: plain words with colour (`oiled oak, warm brown`, `brushed steel`);
   * `instances` + `symmetry` (`none` | `mirror_x` | `mirror_y` | `radial`): 4 legs = one part,
     `instances: 4`, bbox of ONE copy; a mirror pair is one part with `mirror_x`, `instances: 2`;
   * `children[]` (≤ 8, only for a part a parts diagram would explode — a mechanism, a housing):
     each `name`, `description` in numbers, `bbox` INSIDE the parent's (same frame; the validator
     rejects one sticking out past max(10 mm, 15 %)), `material` when it differs, `instances`.
     A child is a planning unit: the part still exports as ONE object named after the parent;
   * `detail_hint`: one line on the parts that carry the identity — what makes THAT part read as
     real.
4. `style_notes`: era/style cues and the proportions that make it read as THAT object.
5. `acceptance[]`: `{"id": "a1", "text": ..., "how": "visual" | "measure", "priority": "must" | "should"}`
   — one per user constraint and must-have, one per key visible feature; `must` only for what
   makes it the wrong object when absent (an unverified `must` caps the score at 0.6).  The
   dimensions from `spec.json` and ground contact are checked whether or not you list them.

Rules: count the request's nameable features first and cover each; real proportions over
invention; every `attach_to` names an existing part; names unique.
