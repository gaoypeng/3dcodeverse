"""building.js — regressions for the facade and cottage bugs the showcase render caught."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("building.js", "materials.js")

_PRELUDE = """
import * as THREE from 'three';
import { block, cottage } from './lib/building.js';

const mk = (s0) => { let s = s0;
  return () => (s = (s * 16807) % 2147483647) / 2147483647; };

/** Emissive hex of every named mesh part. */
function parts(root) {
  const out = {};
  root.traverse((o) => { if (o.isMesh) out[o.name] = o.material.emissive ? o.material.emissive.getHex() : null; });
  return out;
}

/** Name of the first mesh a ray meets, or null. */
function firstHit(root, from, dir) {
  const meshes = [];
  root.traverse((o) => { if (o.isMesh) meshes.push(o); });
  const hit = new THREE.Raycaster(
      new THREE.Vector3().fromArray(from),
      new THREE.Vector3().fromArray(dir).normalize(), 0, 400)
      .intersectObjects(meshes, true);
  return hit.length ? hit[0].object.name : null;
}
"""


def _measure(script: str) -> dict:
    return measure(_PRELUDE + script, _LIBS)



def test_roof_clutter_uses_the_hosts_local_roof_after_placement():
    """World roof height was added as local Y, applying host transforms twice."""
    out = _measure("""
import { roofClutter } from './lib/building.js';
const layouts = [false,true].map(transformed => {
  const parent = new THREE.Group(), host = new THREE.Group();
  const roof = new THREE.Mesh(new THREE.BoxGeometry(10,4,6), new THREE.MeshStandardMaterial());
  roof.position.set(2,3,-1); host.add(roof); parent.add(host);
  if(transformed) {
    parent.position.set(19,-3,7); parent.rotation.set(.1,.5,-.2);
    parent.scale.set(.8,1.3,1.6);
    host.position.set(8,11,-4); host.rotation.set(.2,-.7,.1);
    host.scale.set(2,.6,1.2);
  }
  roofClutter(host,{rand:()=>.5,tanks:0,units:1});
  const clutter=host.getObjectByName('RoofClutter'), unit=clutter.children[0];
  unit.geometry.computeBoundingBox();
  const local = unit.geometry.boundingBox.clone().translate(unit.position).translate(clutter.position);
  return {center:local.getCenter(new THREE.Vector3()).toArray(),
    size:local.getSize(new THREE.Vector3()).toArray(),foot:local.min.y};
});
console.log(JSON.stringify(layouts));
""")
    plain, transformed = out
    assert plain["center"][0] == pytest.approx(2)
    assert plain["center"][2] == pytest.approx(-1)
    assert plain["foot"] == pytest.approx(5)
    assert transformed["foot"] == pytest.approx(plain["foot"])
    assert transformed["center"] == pytest.approx(plain["center"])
    assert transformed["size"] == pytest.approx(plain["size"])


@pytest.fixture(scope="module")
def lit_block() -> dict:
    return _measure("""
const b = block({ w: 16, d: 14, h: 26, style: 'masonry', lit: 0.45,
                  rand: mk(5) });
const glow = [];
b.traverse((o) => {
  if (o.isMesh && o.material.emissive && o.material.emissiveIntensity > 0
      && o.material.emissive.getHex() !== 0) {
    glow.push({ name: o.name, emissive: o.material.emissive.getHex(),
                intensity: o.material.emissiveIntensity,
                tris: o.geometry.index ? o.geometry.index.count / 3
                    : o.geometry.attributes.position.count / 3 });
  }
});
// Panes are boxes: 12 triangles each, lit or dark. The ratio IS the
// `lit` fraction the caller asked for -- unless the whole shell glows.
const litPanes = glow.reduce((a, g) => a + g.tris, 0) / 12;
let darkPanes = 0;
b.traverse((o) => { if (o.isMesh && o.name.startsWith('Glazing'))
  darkPanes += (o.geometry.index ? o.geometry.index.count / 3
      : o.geometry.attributes.position.count / 3) / 12; });
console.log(JSON.stringify({
  revealEmissive: parts(b).Reveals,
  glow,
  litPanes, darkPanes,
}));
""")


def test_a_lit_block_lights_windows_not_the_whole_shell(lit_block):
    """The reveal shell is ONE box spanning the building."""
    m = lit_block
    assert m["revealEmissive"] == 0, (
        "the interior shell itself glows -- every window in the building "
        "lights from one box")
    assert all(g["name"].startswith("LitRooms") for g in m["glow"]), m["glow"]
    # `lit: 0.45` has to mean 45% of the windows, not the building.
    frac = m["litPanes"] / (m["litPanes"] + m["darkPanes"])
    assert 0.25 < frac < 0.65, (frac, m["litPanes"], m["darkPanes"])
    assert m["darkPanes"] >= 8, ("no window stayed dark", m["darkPanes"])


# ------------------------------------------------------------- the cottage

@pytest.fixture(scope="module")
def cot() -> dict:
    return _measure("""
const W = 8, D = 7, H = 3.2, PITCH = 0.85 * W / 2;
const c = cottage({ w: W, d: D, h: H, dormers: 2, roof: 'gable', rand: mk(23) });
// apex up: the gable triangle is narrow near the ridge, wide at the eaves (walls mesh only)
const wallsOnly = new THREE.Group();
c.traverse((o) => { if (o.isMesh && o.name === 'Walls') wallsOnly.add(o.clone()); });
const gableAt = (frac) => firstHit(wallsOnly, [W * 0.35, H + PITCH * frac, 40], [0, 0, -1]);
console.log(JSON.stringify({ gableHigh: gableAt(0.75), gableLow: gableAt(0.15) }));
""")


def test_cottage_gable_ends_are_apex_up(cot):
    """`rotateX(+PI/2)` put the 3-gon's apex at y=-1: every gable shipped upside down (a bbox cannot tell)."""
    assert cot["gableLow"] == "Walls", cot
    assert cot["gableHigh"] is None, cot
