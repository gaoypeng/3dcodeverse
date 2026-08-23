"""String templates for the three.js skeleton (object.js + one stub per part)."""

from __future__ import annotations

PACKAGE_JSON = '{ "type": "module", "private": true }\n'

OBJECT_HEADER = """\
// {object_name} — three.js static object (raw ESM).  Harness contract:
//   export function build(THREE) -> THREE.Group   (root.name = object name)
//   Y up, +Z front, meters; object stands on y=0, footprint centred on the Y axis.
//   Parts live in ./parts/<snake>.js, each `export function build<Pascal>(THREE)`
//   returning a Group already at its WORLD pose.  object.js only assembles.
//   Optional idle animation: root.userData.tick = (t, dt) => {{ ... }}
//   Allowed imports: 'three', 'three/addons/...', relative './'.  No network, no DOM.
import * as THREE from 'three';
{imports}

export function build(THREE_) {{
  const root = new THREE.Group();
  root.name = '{object_name}';
{adds}
  return root;
}}
"""

PART_TEMPLATE = """\
// Part: {pascal}  ({role})
// {description}
// Plan bbox (world, meters): centre {center}  extents {extents}{material_line}
// Contract: export function build{pascal}(THREE) -> THREE.Group named '{pascal}', at WORLD pose.
// Y up, +Z front.  Use real sizes in meters.  Name every mesh.  No lights/cameras/renderers.
import * as THREE from 'three';
import {{ RoundedBoxGeometry }} from 'three/addons/geometries/RoundedBoxGeometry.js';

export function build{pascal}(THREE_) {{
  const group = new THREE.Group();
  group.name = '{pascal}';

  // ---- PLACEHOLDER (replace with real construction; keep the group name) ----
  // A rounded box filling the planned bbox.  Copyable recipes:
  //   box:        new THREE.BoxGeometry(w, h, d)
  //   bevelled:   new RoundedBoxGeometry(w, h, d, 4, Math.min(w, h, d) * 0.08)
  //   cylinder:   new THREE.CylinderGeometry(rTop, rBottom, h, 32)
  //   lathe:      new THREE.LatheGeometry(points /* Vector2[] (x=radius, y=height) */, 48)
  //   extrude w/ bevel:
  //     const shape = new THREE.Shape(); shape.moveTo(-w/2, -d/2); shape.lineTo(w/2, -d/2);
  //     shape.lineTo(w/2, d/2); shape.lineTo(-w/2, d/2); shape.closePath();
  //     const geo = new THREE.ExtrudeGeometry(shape, {{ depth: h, bevelEnabled: true,
  //       bevelThickness: 0.004, bevelSize: 0.004, bevelSegments: 3 }});
  //     geo.rotateX(-Math.PI / 2);            // extrude along +Y instead of +Z
  //   merge many: import {{ mergeGeometries }} from 'three/addons/utils/BufferGeometryUtils.js';
  const size = [{ex}, {ey}, {ez}];
  const geometry = new RoundedBoxGeometry(size[0], size[1], size[2], 3, Math.min(...size) * 0.08);
  const material = new THREE.MeshStandardMaterial({{ color: {color}, roughness: 0.6, metalness: 0.05 }});
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = '{pascal}Body';
  mesh.position.set({cx}, {cy}, {cz});   // world pose (bbox centre)
  group.add(mesh);
  // ---- END PLACEHOLDER ----

  return group;
}}
"""

PLACEHOLDER_COLORS = ("0x9aa5b1", "0xb08968", "0x7f8c8d", "0xc0a080", "0x6c7a89", "0xa0522d", "0x8fa3ad", "0xbfa27a")
