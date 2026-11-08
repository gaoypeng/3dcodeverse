# The interface, and nothing else (node, three@0.182 + GLTFExporter)

`src/parts/body.js`
```js
import * as THREE from 'three';
const SIZE = [0.3, 0.2, 0.3], CENTRE = [0, 0.1, 0];   // this part's plan bbox (world, metres)
export function buildBody(THREE) {
  const g = new THREE.Group(); g.name = 'Body';         // the plan's part name
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...SIZE), new THREE.MeshStandardMaterial({ color: 0x8a8f98 }));
  mesh.name = 'BodyShell';
  mesh.position.set(...CENTRE);                          // world pose: the centre, not the base
  g.add(mesh);
  return g;
}
```
`src/object.js`
```js
import * as THREE from 'three';
import { buildBody } from './parts/body.js';
export function build(THREE) {
  const root = new THREE.Group(); root.name = 'Object'; // the plan's object name
  root.add(buildBody(THREE));                           // one line per plan part
  return root;
}
export function selfcheck(THREE, root) {                // optional
  const box = new THREE.Box3().setFromObject(root);
  if (Math.abs(box.min.y) > 0.01) throw new Error(`object not on ground: min.y=${box.min.y}`);
}
```
What the part is made of — primitives, a lathe profile, an extruded outline, a swept tube,
merged pieces, instanced repeats, raw `BufferGeometry` — is the brief's and the plan's.
