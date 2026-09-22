"""A mesh that writes no depth is fog, not matter: it supports nothing, swallows
nothing and overlaps nothing.

Measured on `bench/out/scene_baseline` (2026-09-05).  Two of six scene cells failed
`scene_placement` on nothing but their own atmosphere:

    japanese_garden  BambooGroveBorder/BlackPine_5 is sunken 3.46 m into AtmosphereHaze
    cozy_cabin       WindowSnowView/Mesh_49 and Environment/MoonlightShaft overlap
                     (100% of the smaller box)

Both scenes were correct.  `AtmosphereHaze` is four DoubleSide shells of
MeshBasicMaterial at opacity 0.035 with `depthWrite: false`; `MoonlightShaft` is the
same idiom at 0.04 with AdditiveBlending.  Every one of the nine recorded scenes uses
`depthWrite: false` 34-42 times, so this is the generator's standard way to write a
volumetric — not an outlier worth a special case.
"""

from __future__ import annotations

import pytest

from codeverse3d.config import get_settings
from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]

RUNTIME_JS = get_settings().runtime_js_dir()

#: `HAZE_DEPTH_WRITE` is the one thing that changes between the two arms below.
SCENE_JS = """
import * as THREE from 'three';
import { sceneCensus } from './lib/host_census.mjs';
const solid = new THREE.MeshStandardMaterial();
const fog = new THREE.MeshBasicMaterial({
  color: 0xffffff, transparent: true, opacity: 0.035,
  depthWrite: HAZE_DEPTH_WRITE, side: THREE.DoubleSide,
});
const box = (name, mat, sx, sy, sz, x, y, z) => {
  const m = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat);
  m.name = name; m.position.set(x, y + sy / 2, z); return m;
};
const scene = new THREE.Scene();
const env = new THREE.Group(); env.name = 'Environment'; scene.add(env);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 8, 8), solid);
ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; env.add(ground);

// the haze: a ground-hugging shell whose TOP surface (3.45 m) cuts through the pine,
// which is the shape that produced "sunken 3.46 m into AtmosphereHaze" — it starts
// just below the pine's foot, so the probe accepts it as a surface above the foot
const haze = new THREE.Group(); haze.name = 'AtmosphereHaze';
haze.add(box('HazeShell', fog, 40, 3.5, 40, 0, -0.05, 0)); env.add(haze);
// the light shaft: a slab driven THROUGH the window, which is what a shaft is for
const shaft = new THREE.Group(); shaft.name = 'MoonlightShaft';
shaft.add(box('ShaftSlab', fog, 1.2, 6, 1.2, 3, 0, 0)); env.add(shaft);

const zone = new THREE.Group(); zone.name = 'Grove'; scene.add(zone);
zone.add(box('BlackPine', solid, 1.2, 6, 1.2, 0, 0, 0));   // stands on the ground
zone.add(box('WindowSnowView', solid, 1.4, 1.4, 0.1, 3, 1.2, 0));  // inside the shaft
scene.updateMatrixWorld(true);
const t = sceneCensus(scene, THREE, { placement: true }).placement;
console.log(JSON.stringify({
  exempt: t.exempt,
  assets: t.assets.map((a) => ({
    name: a.name, exempt: a.exempt, sunk_m: a.sunk_m, sunk_into: a.sunk_into,
    supported: a.supported, floating: a.floating,
  })),
  pairs: t.interpenetrations.map((p) => [p.a, p.b]),
}));
"""


def _run(depth_write: str) -> dict:
    body = SCENE_JS.replace("HAZE_DEPTH_WRITE", depth_write).replace("'./lib/", f"'{RUNTIME_JS}/lib/")
    return run_node_json(body)


def _row(out: dict, name: str) -> dict:
    rows = [a for a in out["assets"] if a["name"] == name]
    assert len(rows) == 1, (name, [a["name"] for a in out["assets"]])
    return rows[0]


@pytest.fixture(scope="module")
def volumetric() -> dict:
    return _run("false")


@pytest.fixture(scope="module")
def solid_control() -> dict:
    return _run("true")


def test_a_depthless_shell_is_exempt_and_indexes_nothing(volumetric):
    assert volumetric["exempt"].get("volumetric") == 2
    assert _row(volumetric, "AtmosphereHaze")["exempt"] == "volumetric"
    assert _row(volumetric, "MoonlightShaft")["exempt"] == "volumetric"


def test_a_tree_inside_the_haze_stands_on_the_ground(volumetric):
    pine = _row(volumetric, "BlackPine")
    assert pine["exempt"] == ""
    assert pine["sunk_into"] == "" and pine["sunk_m"] == 0
    assert pine["supported"] is True and pine["floating"] is False


def test_a_light_shaft_through_a_window_is_not_an_interpenetration(volumetric):
    assert volumetric["pairs"] == []


def test_the_same_shells_WITH_depth_write_are_still_measured(solid_control):
    """The control: the rule is `depthWrite`, not the name, the opacity or the size.
    Turn depth writing back on and the identical geometry produces exactly the two
    findings the baseline reported."""
    assert "volumetric" not in solid_control["exempt"]
    pine = _row(solid_control, "BlackPine")
    assert pine["sunk_into"] == "AtmosphereHaze" and pine["sunk_m"] == pytest.approx(3.45, abs=0.05)
    pairs = [sorted(p) for p in solid_control["pairs"]]
    assert pairs, "the control must reproduce the findings, or it controls for nothing"
    # every one of them is a solid object against fog — which is the whole point
    assert all("AtmosphereHaze" in p or "MoonlightShaft" in p for p in pairs)
    assert ["AtmosphereHaze", "BlackPine"] in pairs


SCATTER_JS = """
import * as THREE from 'three';
import { sceneCensus } from './lib/host_census.mjs';
const solid = new THREE.MeshStandardMaterial();
const fog = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.04, depthWrite: false });
const scene = new THREE.Scene();
const env = new THREE.Group(); env.name = 'Environment'; scene.add(env);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 8, 8), solid);
ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; env.add(ground);
const zone = new THREE.Group(); zone.name = 'Meadow'; scene.add(zone);
// scatter AND a haze shell in one asset: both reasons apply, the instanced one is better
const mixed = new THREE.Group(); mixed.name = 'TuftsAndHaze';
mixed.add(new THREE.InstancedMesh(new THREE.BoxGeometry(0.2, 0.2, 0.2), solid, 40));
const shell = new THREE.Mesh(new THREE.BoxGeometry(20, 4, 20), fog);
shell.position.y = 2; mixed.add(shell);
zone.add(mixed);
scene.updateMatrixWorld(true);
const t = sceneCensus(scene, THREE, { placement: true }).placement;
console.log(JSON.stringify({ exempt: t.exempt,
  rows: t.assets.map((a) => ({ name: a.name, exempt: a.exempt })) }));
"""


def test_scatter_that_also_carries_fog_is_exempt_as_instanced():
    """Both reasons are true; `instanced` says the useful thing (its instances cannot be
    sampled at this budget), so it wins."""
    out = run_node_json(SCATTER_JS.replace("'./lib/", f"'{RUNTIME_JS}/lib/"))
    row = [a for a in out["rows"] if a["name"] == "TuftsAndHaze"]
    assert row and row[0]["exempt"] == "instanced"
