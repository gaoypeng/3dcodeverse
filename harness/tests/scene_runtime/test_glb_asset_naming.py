"""A loaded GLB root is named after its asset, or nothing can find it.

Measured on `bench/out/scene_baseline` (2026-09-05).  rooftop_garden's plan asked for
one `blender_glb` asset, `LoungeSofa`; the Blender sub-run built it (asset judge
0.618), the assembler wrote the loader, the browser loaded it and put it in the scene
-- `glb_assets: {meshes_in_scene: 1, in_scene: true}` -- and `scene_placement` still
raised two ERRORs:

    zone PergolaLounge is missing planned contents: LoungeSofa
    zone BotanicalAlley is missing planned contents: LoungeSofa

because the object standing in both zones was called **`Scene`**.  A glTF root carries
whatever the exporter wrote, and Blender writes "Scene"; the assembler handed that
straight to the zones.  Re-probing the recorded workspace with the naming in place
takes those two ERRORs to zero.
"""

from __future__ import annotations

import json

import pytest

from codeverse.contracts.plan import CameraPlan
from codeverse.languages.scene_threejs import render_scene_js
from codeverse.spatial.node import run_node
from tests.scene_runtime.conftest import needs_node

pytestmark = [pytest.mark.node, needs_node]

ZONE_JS = """
import * as THREE from 'three';
export function build(ctx) {
  const g = new THREE.Group();
  const sofa = ctx.assets.lounge_sofa;
  // what the zone sees is what every by-name check downstream sees
  g.userData.sofaName = sofa ? sofa.name : '<absent>';
  if (sofa) g.add(sofa.clone());
  return g;
}
"""

#: the glTF root arrives UNNAMED, exactly as three's GLTFLoader hands over a Blender
#: export whose scene node carries no name of its own
DRIVER_JS = """
import * as THREE from 'three';
import { createScene } from './src/scene.js';
const loaders = { gltf: { loadAsync: async () => ({ scene: new THREE.Group() }) } };
const { scene } = await createScene({ renderer: null, loaders });
console.log(JSON.stringify({
  zoneSees: scene.children.map((c) => c.userData.sofaName),
  cloneNames: scene.children.flatMap((c) => c.children.map((k) => k.name)),
}));
"""


@pytest.fixture
def loaded(tmp_path) -> dict:
    (tmp_path / "src" / "zones").mkdir(parents=True)
    (tmp_path / "src" / "zones" / "pergola_lounge.js").write_text(ZONE_JS)
    cam = CameraPlan(name="establishing", position=[5, 2, 5], look_at=[0, 1, 0], fov=45)
    (tmp_path / "src" / "scene.js").write_text(
        render_scene_js(["pergola_lounge"], [cam], ["lounge_sofa"], env_ok=False)
    )
    driver = tmp_path / "driver.mjs"
    driver.write_text(DRIVER_JS)
    res = run_node(driver, [], cwd=tmp_path, three_hook=True, timeout_s=60)
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_the_zone_sees_the_asset_under_its_planned_name(loaded):
    assert loaded["zoneSees"] == ["LoungeSofa"], "a glTF root is called 'Scene' until someone names it"


def test_the_clone_the_zone_places_carries_the_name_too(loaded):
    """`.clone()` copies the name, so the object actually standing in the zone — the
    one `scene_placement` matches against the plan — carries it as well."""
    assert loaded["cloneNames"] == ["LoungeSofa"]


def test_a_plan_without_glb_assets_emits_no_loader():
    src = render_scene_js(["z"], [], [], env_ok=False)
    assert "assetFiles" not in src and "loadAsync" not in src
