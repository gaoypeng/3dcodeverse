"""A loaded GLB root is named after its asset, or nothing can find it.

Regression: a glTF root carries whatever the exporter wrote ("Scene" from Blender),
and the assembler handed that to the zones, so `scene_placement` reported a loaded
`LoungeSofa` as missing from both zones that held it.
"""

from __future__ import annotations

import json

import pytest

from codeverse3d.contracts.plan import CameraPlan
from codeverse3d.languages.scene_threejs import render_scene_js
from codeverse3d.spatial.node import run_node
from tests.scene_runtime.conftest import needs_browser, needs_node
from tests.scene_runtime.glb_fixture import write_box_glb

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
const loaders = { gltf: { loadAsync: async () => {
  // a Blender export whose one keyframed part became a clip: a 2 s swing on 'Lid'
  const root = new THREE.Group(); const lid = new THREE.Group(); lid.name = 'Lid'; root.add(lid);
  const track = new THREE.NumberKeyframeTrack('Lid.rotation[x]', [0, 1, 2], [-0.35, 0.35, -0.35]);
  return { scene: root, animations: [new THREE.AnimationClip('Swing', 2, [track])] };
} } };
const { scene, update } = await createScene({ renderer: null, loaders });
const lids = []; scene.traverse((o) => { if (o.name === 'Lid') lids.push(o); });
update(0, 0); const at0 = lids.map((l) => +l.rotation.x.toFixed(3));
update(1.0, 0.016); const at1 = lids.map((l) => +l.rotation.x.toFixed(3));
update(0, 0); const again0 = lids.map((l) => +l.rotation.x.toFixed(3));
console.log(JSON.stringify({
  zoneSees: scene.children.map((c) => c.userData.sofaName),
  cloneNames: scene.children.flatMap((c) => c.children.map((k) => k.name)),
  clipsOnClone: scene.children.flatMap((c) => c.children.map((k) => (k.animations || []).length)),
  at0, at1, again0,
}));
"""


def test_a_loaded_glb_carries_its_planned_name_and_plays_its_clip(tmp_path):
    (tmp_path / "src" / "zones").mkdir(parents=True)
    (tmp_path / "src" / "zones" / "pergola_lounge.js").write_text(ZONE_JS)
    cam = CameraPlan(name="establishing", position=[5, 2, 5], look_at=[0, 1, 0], fov=45)
    (tmp_path / "src" / "scene.js").write_text(
        render_scene_js(["pergola_lounge"], [cam], ["lounge_sofa"], env_ok=False)
    )
    driver = tmp_path / "driver.mjs"
    driver.write_text(DRIVER_JS)
    res = run_node(driver, [], cwd=tmp_path, three_hook=True, timeout_s=60)
    loaded = json.loads(res.stdout.strip().splitlines()[-1])
    assert loaded["zoneSees"] == ["LoungeSofa"], "a glTF root is called 'Scene' until someone names it"
    # `.clone()` copies the name, so the object actually standing in the zone — the one
    # `scene_placement` matches against the plan — carries it as well
    assert loaded["cloneNames"] == ["LoungeSofa"]
    # a keyframed Blender part arrives as a clip on the preloaded root, `.clone()` keeps it,
    # and the assembled scene plays it from `update(t)` with no zone code — the same t gives
    # the same pose (the harness samples t = 0 and 1.5 s)
    assert loaded["clipsOnClone"] == [1]
    assert loaded["at0"] == [-0.35] and loaded["at1"] == [0.35] and loaded["again0"] == loaded["at0"]


def test_a_procedural_asset_is_rendered_on_the_hero_rig(tmp_ws):
    """`render_asset` is the hook `scene_assets` judges a threejs asset through: the module is
    exported by the object track's exporter and rendered on the quick rig a hero's GLB gets.
    Nothing defined it until 2026-09-07, so 0 of 860 recorded procedural assets were judged."""
    from codeverse3d.languages.scene_threejs import SceneThreeJsRuntime

    (tmp_ws.src / "assets").mkdir(parents=True, exist_ok=True)
    (tmp_ws.src / "assets" / "crate.js").write_text(
        "import * as THREE from 'three';\n"
        "export function buildCrate(THREE, opts = {}) {\n"
        "  const g = new THREE.Group(); g.name = 'Crate';\n"
        "  const box = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.4, 0.3), new THREE.MeshStandardMaterial({ color: 0x886644 }));\n"
        "  box.position.y = 0.2; box.name = 'Body'; g.add(box); return g;\n}\n")
    out = tmp_ws.artifacts / "renders" / "assets" / "crate"
    rs = SceneThreeJsRuntime().render_asset(tmp_ws, "Crate", out)
    assert rs.contact_sheet and (out / "sheet.png").is_file() and len(rs.views) >= 4
    assert (tmp_ws.artifacts / "asset_export" / "crate" / "artifacts" / "object.glb").is_file()


HERO_CLONES = """
  const hero = (await loaders.gltf.loadAsync('/assets/hero_cube.glb')).scene;
  hero.name = 'HeroCube';
  const row = new THREE.Group(); row.name = 'HeroRow'; scene.add(row);
  for (const [x, z] of [[10, 14], [-12, 8], [6, -10]]) { const c = hero.clone(); c.position.set(x, 2, z); row.add(c); }
"""


@needs_browser
def test_a_hero_s_size_is_one_instance_not_the_union_of_its_clones(starter_ws):
    """`census.glb_assets[].size_m` is the largest SINGLE placed instance (2026-10-14): three clones
    of a 4 m cube 20 m apart read as "24 m across as placed" to the frames gate and the judge
    before, and `instances` says how many stand in the scene.  `.clone()` shares geometry, so the
    file's uuids are grouped per clone root, not per uuid."""
    from codeverse3d.spatial.probes import probe_scene

    write_box_glb(starter_ws.public / "assets" / "hero_cube.glb", size=(4.0, 4.0, 4.0))
    scene = starter_ws.src / "scene.js"
    text = scene.read_text()
    text = text.replace("export function createScene(", "export async function createScene(")
    assert "  ctx.env = env;\n" in text
    scene.write_text(text.replace("  ctx.env = env;\n", "  ctx.env = env;\n" + HERO_CLONES, 1))
    res = probe_scene(starter_ws)
    assert res.ok and res.census, res.errors
    row = next(r for r in res.census["glb_assets"] if r["url"] == "/assets/hero_cube.glb")
    assert row["in_scene"] and row["meshes_in_scene"] == 1 and row["instances"] == 3, row
    assert abs(row["size_m"] - 4.0) < 0.05, row
