"""The placement row's `inner` names count FAMILIES (2026-09-07).

D59 lists a row's named descendants (capped at `MAX_INNER_NAMES`) so the plan-contents check
can see through a wrapper group.  The cap counted every distinct name, so a wrapper holding
30 `Planter_N` before the hero clone hid the hero and the check reported it missing.  One
slot per family (`Planter_3` / `Planter.003` → `Planter`) keeps the hero visible and the
by-name check still matches on the plan's word.
"""
from __future__ import annotations

import pytest

from codeverse.config import get_settings
from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]

RUNTIME_JS = get_settings().runtime_js_dir()


def test_thirty_numbered_props_before_the_hero_do_not_hide_it():
    body = f"""
import * as THREE from 'three';
import {{ sceneCensus }} from '{RUNTIME_JS}/lib/host_census.mjs';
import {{ MAX_INNER_NAMES }} from '{RUNTIME_JS}/lib/host_placement.mjs';
const solid = new THREE.MeshStandardMaterial();
const mk = (name, s, x, z) => {{ const m = new THREE.Mesh(new THREE.BoxGeometry(s, s, s), solid); m.name = name; m.position.set(x, s / 2, z); return m; }};
const hero = new THREE.Group(); hero.name = 'LoungeSofa';
for (const n of ['Seat', 'Back', 'ArmL', 'ArmR', 'Cushion.001', 'Cushion.002']) hero.add(mk(n, 0.4, hero.children.length * 0.45, 0));
const scene = new THREE.Scene();
const env = new THREE.Group(); env.name = 'Environment'; scene.add(env);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 8, 8), solid); ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; env.add(ground);
const zone = new THREE.Group(); zone.name = 'PergolaLounge'; scene.add(zone);
const wrap = new THREE.Group(); wrap.name = 'LoungeCorner';
for (let i = 0; i < 30; i++) wrap.add(mk('Planter_' + i, 0.3, 10 + (i % 6) * 0.5, 8 + Math.floor(i / 6) * 0.5));
const c = hero.clone(); c.position.set(14, 0, 8); wrap.add(c); zone.add(wrap);
scene.updateMatrixWorld(true);
const rows = sceneCensus(scene, THREE, {{ placement: true }}).placement.assets;
const row = rows.find((a) => a.name === 'LoungeCorner');
console.log(JSON.stringify({{ cap: MAX_INNER_NAMES, inner: row.inner, hasHero: row.inner.includes('LoungeSofa') }}));
"""
    got = run_node_json(body)
    assert got["hasHero"], got
    assert "Planter" in got["inner"] and "Planter_0" not in got["inner"], got
    assert len(got["inner"]) <= got["cap"]


def test_a_wrapper_of_scattered_instances_reports_the_instance_size():
    """`families`: twelve 2.2 m fence panels along a 15 m path are one row ('PicketFences');
    the row's bbox is the run, `families.PicketFence` is the panel (loop 9 lighthouse,
    2026-09-07: the scale check read 14.7 m against the plan's 2.4 m panel)."""
    body = f"""
import * as THREE from 'three';
import {{ sceneCensus }} from '{RUNTIME_JS}/lib/host_census.mjs';
const solid = new THREE.MeshStandardMaterial();
const panel = (i) => {{
  const g = new THREE.Group(); g.name = 'PicketFence';
  for (let p = 0; p < 6; p++) {{ const m = new THREE.Mesh(new THREE.BoxGeometry(0.08, 1.2, 0.03), solid); m.name = 'Picket_' + p; m.position.set(-1.0 + p * 0.4, 0.6, 0); g.add(m); }}
  const rail = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.06, 0.03), solid); rail.name = 'Rail'; rail.position.set(0, 0.9, 0); g.add(rail);
  g.position.set(0, 0, i * 1.25); return g;
}};
const scene = new THREE.Scene();
const env = new THREE.Group(); env.name = 'Environment'; scene.add(env);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 8, 8), solid); ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; env.add(ground);
const zone = new THREE.Group(); zone.name = 'PathAndFence'; scene.add(zone);
const run = new THREE.Group(); run.name = 'PicketFences';
for (let i = 0; i < 12; i++) run.add(panel(i));
zone.add(run);
scene.updateMatrixWorld(true);
const row = sceneCensus(scene, THREE, {{ placement: true }}).placement.assets.find((a) => a.name === 'PicketFences');
console.log(JSON.stringify({{ size: row.bbox.size, families: row.families }}));
"""
    got = run_node_json(body)
    assert got["size"][2] > 13, got                       # the run
    fam = got["families"]["PicketFence"]
    assert fam["n"] == 12 and 2.1 < fam["size_m"] < 2.3, got   # the panel
    assert got["families"]["Picket"]["n"] == 72 and got["families"]["Picket"]["size_m"] < 1.3, got
