"""The ablation instrument: does the scene's custom GLSL reach the frame?

Three fixture scenes, one instrument, one claim each:
  * a scene with ONE ShaderMaterial in shot  → contribution > 0
  * the same scene with the shader replaced by a stock material (the measured
    failure this exists to name — the retreat to a flat material) → exactly 0
  * two shaders, one in shot and one behind the camera → leave-one-out separates
    "the shader exists" from "the shader is in the picture"

Plus the pure pieces (frameDiff, the two stand-in materials) in node without a
browser, and the python reader / census merge with no node at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.config import get_settings
from codeverse3d.spatial.ablation import (
    CENSUS_FIELD,
    PRESENT_FLOOR,
    AblationReport,
    _report,
    ablate_scene,
    merge_into_census,
)
from codeverse3d.spatial.registry import get_tool
from codeverse3d.workspace import Workspace
from tests.scene_runtime.conftest import needs_browser, needs_node, run_node_json

pytestmark = pytest.mark.node

_HOST_ABLATION = f"{get_settings().runtime_js_dir()}/lib/host_ablation.mjs"


def _node(script: str) -> dict:
    """Run an inline probe against the real module (absolute import: `-e` has no file dir)."""
    return run_node_json(script.replace("./lib/host_ablation.mjs", _HOST_ABLATION))


# --------------------------------------------------------------------------- fixture scenes
# One unlit orange quad filling the shot.  No colour-ish uniform on purpose: the
# stand-in then falls back to neutral grey and the two frames differ by a lot,
# which is what makes the assertion about presence and not about tone mapping.
_SHADER_QUAD = """
function shaderQuad(THREE, z) {
  const m = new THREE.ShaderMaterial({
    vertexShader: 'void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: 'void main() { gl_FragColor = vec4(0.95, 0.25, 0.05, 1.0); }',
  });
  m.name = 'quad_shader_z' + z;
  const q = new THREE.Mesh(new THREE.PlaneGeometry(6, 4), m);
  q.position.set(0, 1.5, z);
  q.name = 'Quad' + z;
  return q;
}
"""

_SCENE_HEAD = """
import * as THREE from 'three';
""" + _SHADER_QUAD + """
function base(THREE) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x20242a);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 1.2));
  const dir = new THREE.DirectionalLight(0xffffff, 1.4);
  dir.position.set(4, 6, 5);
  scene.add(dir);
  const box = new THREE.Mesh(new THREE.BoxGeometry(1.2, 1.2, 1.2),
                             new THREE.MeshStandardMaterial({ color: 0x6a7f5a, roughness: 0.8 }));
  box.position.set(3.4, 0.6, 1.0);
  box.name = 'PlainBox';
  scene.add(box);
  return scene;
}
const CAMERAS = [{ name: 'front', position: [0, 1.6, 6], lookAt: [0, 1.5, 0], fov: 55 }];
"""

SCENE_ONE_SHADER = _SCENE_HEAD + """
export function createScene() {
  const scene = base(THREE);
  scene.add(shaderQuad(THREE, 0));
  return { scene, cameras: CAMERAS, update() {} };
}
"""

SCENE_NO_SHADER = _SCENE_HEAD + """
export function createScene() {
  const scene = base(THREE);
  const q = new THREE.Mesh(new THREE.PlaneGeometry(6, 4),
                           new THREE.MeshStandardMaterial({ color: 0xf2401a, roughness: 0.6 }));
  q.position.set(0, 1.5, 0);
  q.name = 'Quad0';
  scene.add(q);
  return { scene, cameras: CAMERAS, update() {} };
}
"""

# Two custom shaders: one in the shot, one 40 m BEHIND the camera.  Both compile,
# both are counted by the census, and only one of them is in the picture.
SCENE_TWO_SHADERS = _SCENE_HEAD + """
export function createScene() {
  const scene = base(THREE);
  scene.add(shaderQuad(THREE, 0));
  scene.add(shaderQuad(THREE, 40));
  return { scene, cameras: CAMERAS, update() {} };
}
"""


def _scene_ws(ws, source: str):
    ws.src.mkdir(parents=True, exist_ok=True)
    (ws.src / "scene.js").write_text(source)
    return ws


# --------------------------------------------------------------------------- browser
@needs_browser
def test_a_shader_in_shot_contributes(ws) -> None:
    r = ablate_scene(_scene_ws(ws, SCENE_ONE_SHADER), time_s=1.5)
    assert r.ok and not r.error, r.model_dump()
    assert r.custom_materials == 1
    assert r.cameras and r.cameras[0].camera == "front"
    assert r.max_changed_frac > 0.05, r.model_dump()
    assert r.present
    # the leave-one-out row IS the whole effect here
    assert r.per_material_measured and r.per_material_cameras == ["front"]
    row = r.materials[0]
    assert row.kind == "ShaderMaterial" and row.material == "quad_shader_z0"
    assert row.changed_frac == pytest.approx(r.max_changed_frac, abs=0.02)
    # the pair the agent looks at
    assert len(r.images) == 2 and all(Path(p).is_file() for p in r.images)
    a, b = (Path(p).read_bytes() for p in sorted(r.images))
    assert a != b, "authored and ablated PNGs are identical — nothing was ablated"


@needs_browser
def test_a_scene_with_no_custom_shader_contributes_exactly_nothing(ws) -> None:
    """The retreat to a flat material: same picture, no GLSL.  The instrument must
    say 0 without pretending it failed to measure."""
    r = ablate_scene(_scene_ws(ws, SCENE_NO_SHADER), time_s=1.5)
    assert r.ok and r.custom_materials == 0
    assert r.max_changed_frac == 0.0 and not r.present
    assert r.cameras[0].changed_frac == 0.0
    assert "no custom shader materials" in r.summary_lines()[0]


@needs_browser
def test_leave_one_out_separates_the_shader_in_shot_from_the_one_behind_the_camera(ws) -> None:
    r = ablate_scene(_scene_ws(ws, SCENE_TWO_SHADERS), time_s=1.5, frames=False)
    assert r.ok and r.custom_materials == 2 and r.per_material_measured
    rows = {m.material: m for m in r.materials}
    assert set(rows) == {"quad_shader_z0", "quad_shader_z40"}
    assert rows["quad_shader_z0"].changed_frac > PRESENT_FLOOR
    assert rows["quad_shader_z40"].changed_frac == 0.0, "a mesh behind the camera cannot change the frame"
    assert not r.images   # frames=False writes no PNGs


@needs_browser
def test_the_measurement_lands_in_the_census_where_the_gates_read_it(ws) -> None:
    _scene_ws(ws, SCENE_ONE_SHADER)
    census_path = ws.artifacts / "census.json"
    census_path.parent.mkdir(parents=True, exist_ok=True)
    census_path.write_text(json.dumps({"totals": {"meshes": 2}}))
    r = ablate_scene(ws, time_s=1.5, frames=False)
    assert merge_into_census(ws, r) is True
    field = json.loads(census_path.read_text())[CENSUS_FIELD]
    assert field["custom_materials"] == 1 and field["present"] is True
    assert field["max_changed_frac"] > 0.05 and field["per_camera"]["front"] > 0.05
    assert json.loads(census_path.read_text())["totals"]["meshes"] == 2   # nothing else disturbed


@needs_browser
def test_the_build_switch_puts_the_number_where_the_scene_gates_read_it(ws, monkeypatch) -> None:
    """C3D_ABLATION folds the measurement into the build census (a second browser
    boot, hence opt-in).  Off — the default — the build is untouched."""
    from codeverse3d.languages.scene_threejs import SceneThreeJsRuntime

    _scene_ws(ws, SCENE_ONE_SHADER)
    runtime = SceneThreeJsRuntime()
    monkeypatch.delenv("C3D_ABLATION", raising=False)
    assert CENSUS_FIELD not in (runtime.build(ws).census or {})
    monkeypatch.setenv("C3D_ABLATION", "1")
    field = (runtime.build(ws).census or {}).get(CENSUS_FIELD)
    assert field and field["present"] is True and field["custom_materials"] == 1
    assert json.loads((ws.artifacts / "census.json").read_text())[CENSUS_FIELD]["present"] is True


# --------------------------------------------------------------------------- node, no browser
@needs_node
def test_frame_diff_counts_only_pixels_past_the_threshold() -> None:
    out = _node("""
import { frameDiff, DIFF_THRESHOLD } from './lib/host_ablation.mjs';
const n = 100;
const mk = (fn) => { const a = new Uint8ClampedArray(n * 4); for (let i = 0; i < n; i++) { const v = fn(i); a[i*4] = v; a[i*4+1] = v; a[i*4+2] = v; a[i*4+3] = 255; } return a; };
const flat = mk(() => 100);
const same = frameDiff(flat, mk(() => 100));
const under = frameDiff(flat, mk(() => 108));          // exactly at the threshold: not changed
const quarter = frameDiff(flat, mk((i) => (i < 25 ? 200 : 100)));
let threw = '';
try { frameDiff(flat, new Uint8ClampedArray(8)); } catch (e) { threw = e.message; }
console.log(JSON.stringify({ same, under, quarter, threw, DIFF_THRESHOLD }));
""")
    assert out["DIFF_THRESHOLD"] == 8
    assert out["same"]["changed_frac"] == 0.0
    assert out["under"]["changed_frac"] == 0.0
    assert out["quarter"]["changed_frac"] == 0.25
    assert "differ in size" in out["threw"]


@needs_node
def test_the_stand_ins_keep_the_base_colour_and_drop_only_the_shader() -> None:
    """A ShaderMaterial becomes a neutral lit material of its own colour uniform;
    a patched builtin becomes ITSELF unpatched — same colour, same maps, no GLSL."""
    out = _node("""
import * as THREE from 'three';
import { ablationTargets, baseColor, neutralMaterial, unpatchedClone } from './lib/host_ablation.mjs';

const sm = new THREE.ShaderMaterial({
  uniforms: { uTime: { value: 0 }, uColor: { value: new THREE.Color(0.2, 0.4, 0.8) } },
  vertexShader: 'void main() { gl_Position = vec4(position, 1.0); }',
  fragmentShader: 'void main() { gl_FragColor = vec4(1.0); }',
  transparent: true, opacity: 0.5, side: THREE.DoubleSide, depthWrite: false,
});
sm.name = 'glow';
const neutral = neutralMaterial(sm, THREE);

const std = new THREE.MeshStandardMaterial({ color: 0x336699, roughness: 0.42 });
std.name = 'terrain';
std.userData.uniforms = { uTime: { value: 3 } };
std.onBeforeCompile = (s) => { s.fragmentShader = '#define PATCHED\\n' + s.fragmentShader; };
const plain = unpatchedClone(std, THREE);

const scene = new THREE.Scene();
const a = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), sm); a.name = 'Glow';
const b = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), std); b.name = 'Terrain';
const c = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial()); c.name = 'Plain';
scene.add(a, b, c);

console.log(JSON.stringify({
  neutral_type: neutral.type,
  neutral_color: [neutral.color.r, neutral.color.g, neutral.color.b].map((v) => +v.toFixed(3)),
  neutral_keeps: [neutral.transparent, neutral.opacity, neutral.side === THREE.DoubleSide, neutral.depthWrite],
  hdr_clamped: [baseColor(new THREE.ShaderMaterial({ uniforms: { uColor: { value: new THREE.Vector3(4, 0, 0) } } }), THREE).r],
  plain_color: plain.color.getHex(),
  plain_roughness: plain.roughness,
  plain_patched: plain.onBeforeCompile !== THREE.Material.prototype.onBeforeCompile,
  plain_userdata: Object.keys(plain.userData).length,
  source_still_patched: std.onBeforeCompile !== THREE.Material.prototype.onBeforeCompile,
  source_userdata: Object.keys(std.userData).length,
  targets: ablationTargets(scene, THREE).map((t) => [t.name, t.kind, t.on, t.meshes, t.backdrop]),
}));
""")
    assert out["neutral_type"] == "MeshStandardMaterial"
    assert out["neutral_color"] == [0.2, 0.4, 0.8]
    assert out["neutral_keeps"] == [True, 0.5, True, False]
    assert out["hdr_clamped"] == [1.0]                       # an HDR uniform cannot blow out the stand-in
    assert out["plain_color"] == 0x336699 and out["plain_roughness"] == 0.42
    assert out["plain_patched"] is False and out["plain_userdata"] == 0
    assert out["source_still_patched"] is True and out["source_userdata"] == 1   # the original is untouched
    assert out["targets"] == [["glow", "ShaderMaterial", "Glow", 1, "content"],
                              ["terrain", "onBeforeCompile", "Terrain", 1, "content"]]


@needs_node
def test_ablate_swaps_only_custom_materials_and_undoes_itself() -> None:
    out = _node("""
import * as THREE from 'three';
import { ablate } from './lib/host_ablation.mjs';
const mk = (mat, name) => { const m = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), mat); m.name = name; return m; };
const sm = new THREE.ShaderMaterial({ vertexShader: 'void main(){}', fragmentShader: 'void main(){}' });
sm.name = 'glow';
const std = new THREE.MeshStandardMaterial({ color: 0x112233 });
std.onBeforeCompile = (s) => s;
std.name = 'terrain';
const plain = new THREE.MeshStandardMaterial({ color: 0x445566 });
plain.name = 'plain';
const scene = new THREE.Scene();
scene.add(mk(sm, 'A'), mk(std, 'B'), mk(plain, 'C'));
const names = () => { const o = {}; scene.traverse((x) => { if (x.material) o[x.name] = x.material.name; }); return o; };
const before = names();
let undo = ablate(scene, THREE, {});
const all = names();
undo();
const restored = names();
undo = ablate(scene, THREE, { only: sm.uuid });
const one = names();
undo();
console.log(JSON.stringify({ before, all, restored, one, back: names() }));
""")
    assert out["before"] == {"A": "glow", "B": "terrain", "C": "plain"}
    assert out["all"] == {"A": "glow__ablated", "B": "terrain__unpatched", "C": "plain"}
    assert out["restored"] == out["before"] and out["back"] == out["before"]
    # leave-one-out touches exactly one material
    assert out["one"] == {"A": "glow__ablated", "B": "terrain", "C": "plain"}


# --------------------------------------------------------------------------- pure python
def test_the_reader_survives_a_scene_that_never_booted(tmp_path: Path) -> None:
    r = _report({"ok": False, "error": "scene did not boot", "custom_materials": 0}, tmp_path)
    assert not r.ok and not r.present and r.max_changed_frac == 0.0
    assert r.summary_lines() == ["ablation could not measure the scene: scene did not boot"]
    assert r.census_field()["present"] is False


def test_summary_names_the_shader_that_is_not_in_the_frame() -> None:
    r = AblationReport(
        ok=True, custom_materials=2, per_material_measured=True,
        max_changed_frac=0.21, content_changed_frac=0.21, per_material_cameras=["front"],
        cameras=[{"camera": "front", "changed_frac": 0.21}],
        materials=[{"material": "rain", "kind": "ShaderMaterial", "changed_frac": 0.21, "meshes": 1, "camera": "front"},
                   {"material": "glow", "kind": "onBeforeCompile", "changed_frac": 0.0, "meshes": 2}],
    )
    text = "\n".join(r.summary_lines())
    assert "21.0% of the frame" in text and "front=21.0%" in text
    assert "in no camera's frame" in text.split("glow")[-1] and "best in front" in text
    assert [m.material for m in r.top_materials()] == ["rain", "glow"]
    assert r.census_field()["top_materials"][0]["material"] == "rain"


def test_merge_into_census_declines_without_a_census(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "run").create()
    assert merge_into_census(ws, AblationReport(ok=True)) is False


def test_the_tool_is_registered_and_says_what_zero_means() -> None:
    card = get_tool("effect_ablation").card()
    assert "0%" in card and "time_s" in card and "neutral material" in card
