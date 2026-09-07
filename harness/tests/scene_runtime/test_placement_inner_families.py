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
