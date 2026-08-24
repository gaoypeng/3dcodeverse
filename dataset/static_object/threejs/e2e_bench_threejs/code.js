// ParkBench — three.js static object (raw ESM).  Harness contract:
//   export function build(THREE) -> THREE.Group   (root.name = object name)
//   Y up, +Z front, meters; object stands on y=0, footprint centred on the Y axis.
//   Parts live in ./parts/<snake>.js, each `export function build<Pascal>(THREE)`
//   returning a Group already at its WORLD pose.  object.js only assembles.
//   Optional idle animation: root.userData.tick = (t, dt) => { ... }
//   Allowed imports: 'three', 'three/addons/...', relative './'.  No network, no DOM.
import * as THREE from 'three';
import { buildSideFrame } from './parts/side_frame.js';
import { buildCenterSupportRib } from './parts/center_support_rib.js';
import { buildBottomStretcher } from './parts/bottom_stretcher.js';
import { buildSeatSlat } from './parts/seat_slat.js';
import { buildBackrestSlat } from './parts/backrest_slat.js';
import { buildFastenerBolts } from './parts/fastener_bolts.js';

export function build(THREE) {
  const root = new THREE.Group();
  Object.setPrototypeOf(root, THREE.Scene.prototype);
  root.name = 'ParkBench';
  root.add(...buildSideFrame(THREE).children);
  root.add(buildCenterSupportRib(THREE));
  root.add(buildBottomStretcher(THREE));
  root.add(buildSeatSlat(THREE));
  root.add(buildBackrestSlat(THREE));
  root.add(buildFastenerBolts(THREE));
  return root;
}
