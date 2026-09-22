# Single-shot response format (API backends without tools)

You cannot run tools in this mode.  Return the COMPLETE code for every file you create
or change, using this exact multi-file block format.  The harness parses it mechanically;
anything outside the blocks is discarded.

## Format

```text
=== FILE: src/model.py ===
<full file contents, no truncation, no "..." placeholders>
=== END FILE ===
=== FILE: src/parts/seat.js ===
<full file contents>
=== END FILE ===
```

Rules (all MUST):

1. Header line is exactly `=== FILE: <path> ===` with a path relative to the workspace
   root, always starting with `src/` (or `public/` for scene assets).  Footer line is
   exactly `=== END FILE ===`.  Both on their own line, no indentation, no code fences
   around them.
2. Put the **entire** file between header and footer — not a diff, not a fragment, not
   "rest unchanged".  A file you do not emit stays as it was.
3. No prose between blocks.  If you must explain something, put it in a code comment
   inside the file.  No markdown fences inside the block (write raw code, not ```python).
4. One block per path; if you emit the same path twice the LAST block wins.
5. Emit every file that the language contract requires (e.g. `src/model.py`;
   `src/object.js` + `src/parts/*.js`; `src/model.py` + `src/robot.urdf`;
   `src/scene.js` + `src/env.js` + …).  A missing required file = build failure.
6. Start your reply with the first `=== FILE:` line.  End it with the last
   `=== END FILE ===` line.  Nothing after it.

## Fallback (legacy one-file parse)

If — and only if — the task asks for exactly one file and you do not use the block
format, the harness takes the first fenced code block (```python / ```js / ```xml) in your
reply as that file.  Do not rely on this; use the block format.

## Worked example (two-file three.js object)

```text
=== FILE: src/parts/seat.js ===
import * as THREE from 'three';
export function buildSeat(THREE) {
  const g = new THREE.Group(); g.name = 'Seat';
  const m = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.04, 0.45),
                           new THREE.MeshStandardMaterial({ color: 0x8b5a2b }));
  m.position.y = 0.45; g.add(m);
  return g;
}
=== END FILE ===
=== FILE: src/object.js ===
import * as THREE from 'three';
import { buildSeat } from './parts/seat.js';
export function build(THREE) {
  const root = new THREE.Group(); root.name = 'Stool';
  root.add(buildSeat(THREE));
  return root;
}
=== END FILE ===
```
