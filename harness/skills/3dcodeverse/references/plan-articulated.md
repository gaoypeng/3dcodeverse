# plan.json for `articulated_object`

Everything in `plan-static.md` (same `parts[]` schema and the same brevity — one short sentence
per part, one line of `summary` that also says what moves, 3 significant figures) plus
`root_link` and `joints[]`.  The frame is Z up, −Y front, metres, radians; every number describes the REST pose (q = 0: doors
closed, drawers in, lids down unless a joint's `rest` says otherwise), bottom on z = 0.

* `parts` are LINKS: one per rigid body that moves as a unit (a drawer is ONE link with its
  front, box, runners and handle).  Detail goes into `children` (sub-parts inside the link's
  bbox, exported as part of the link), never into extra links — every extra link costs a joint
  and a collision pair.  At rest no two links overlap by more than 2 mm (the build fails above
  5 mm); a fixed child touches its parent or sinks ≤ 2 mm; a hinged or sliding child's mesh is
  within 10 mm of its parent's.
* `root_link`: the grounded base (cabinet, chassis).
* `joints[]`, one per moving link, forming a single tree from `root_link`:
  `{"name", "type", "parent", "child", "axis", "pivot", "lower", "upper", "rest", "motion", "mimic"}`
  * `type`: `revolute` (hinges, lids; radians, range ≤ 2π), `prismatic` (drawers, slides;
    metres, range ≤ 5 m), `continuous` (wheels, fans), `fixed`.
  * `axis`: unit vector in the plan frame; `pivot`: a WORLD point ON the hinge line or slide axis
    at rest — a pivot at a part's centre sweeps the part through its neighbour and defeats the
    motion check.  3 decimals.
  * `lower`/`upper`: the real travel (the sweep tests both ends and random combinations);
    `rest` inside them (0 when the authored pose is the home state).  Limits written in degrees
    are converted and recorded under `normalisations`; plan radians.
  * `motion`: one line whose FIRST word is the direction positive travel moves the child —
    front/back/up/down/left/right or ±x/±y/±z (the `motion_direction` gate checks the child's
    centroid displacement against it within a 60° cone).  Two axes, a negation or "spins" are
    not checked.
  * `parent`/`child` name top-level parts, never a sub-part (a referenced sub-part is promoted
    to a link and recorded under `normalisations`; plan it as a link yourself).
  * `mimic: {"joint": "<driver>", "multiplier": r, "offset": 0}` on every follower of a
    one-input mechanism (umbrella ribs, scissor arms, coupled folding legs); without it the sweep
    poses each link alone and judges states the mechanism cannot reach.  A mimic may not name
    itself, a fixed joint, an unknown joint, or close a loop.
* The number of non-fixed joints is the number of moving parts the brief names, give or take
  one; a rigid feature is a sub-part, not a link.
* `acceptance`: the brief's dimensions (`measure`), each named feature (`visual`) and ONE
  `how: "articulation"` item per joint stating the travel and that nothing is penetrated through
  it ("drawer slides out 0.35 m without touching the cabinet").

The shape and the length to write (not numbers to copy): a root link, one moving link with two
children, one joint, one articulation item.
```json
{"object_name": "DeskDrawerUnit", "summary": "Oak desk drawer unit 0.45 × 0.5 × 0.7 m; one drawer slides out 0.35 m to the front.", "root_link": "Cabinet",
 "overall_bbox": {"center": [0, 0, 0.35], "extents": [0.45, 0.5, 0.7]}, "style_notes": "Plain office carcass, 18 mm panels, no plinth, no lock.",
 "parts": [{"name": "Cabinet", "role": "carcass", "description": "Open-fronted box of 18 mm panels with a 0.41 × 0.14 m drawer opening at z = 0.45.", "bbox": {"center": [0, 0, 0.35], "extents": [0.45, 0.5, 0.7]}, "material": "oiled oak, mid brown"},
           {"name": "Drawer", "role": "the one moving body", "description": "Drawer box 12 mm sides, 2 mm clearance to the opening.", "attach_to": "Cabinet", "bbox": {"center": [0, -0.02, 0.45], "extents": [0.41, 0.46, 0.14]}, "material": "oiled oak, mid brown",
            "children": [{"name": "DrawerFront", "description": "Front panel, 3 mm chamfered edges.", "bbox": {"center": [0, -0.24, 0.45], "extents": [0.41, 0.02, 0.14]}},
                         {"name": "DrawerHandle", "description": "Bar handle on two 8 mm posts.", "bbox": {"center": [0, -0.26, 0.45], "extents": [0.12, 0.02, 0.02]}, "material": "brushed steel"}]}],
 "joints": [{"name": "DrawerSlide", "type": "prismatic", "parent": "Cabinet", "child": "Drawer", "axis": [0, -1, 0],
             "pivot": [0, -0.02, 0.45], "lower": 0, "upper": 0.35, "rest": 0, "motion": "front: the drawer pulls out towards -Y"}],
 "acceptance": [{"id": "a1", "text": "the drawer slides out 0.35 m without touching the cabinet", "how": "articulation", "priority": "should"}, …]}
```
