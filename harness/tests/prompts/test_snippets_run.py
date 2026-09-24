"""The cookbooks' promise: every snippet RUNS.

* cadquery blocks run in-process (cadquery is a plain library).
* bpy blocks run in real headless Blender (marker: blender).
* js blocks run in real node against runtime_js/node_modules (marker: node).
* GLSL factories compile in real headless-Chrome WebGL (marker: node).
"""

from __future__ import annotations

import pytest

from tests.prompts.conftest import (
    blocks,
    js_prelude,
    labelled_files,
    run_blender_script,
    run_node_module,
    strip_imports_exports,
)
from tests.scene_runtime.lib._probe import compile_scene


# --------------------------------------------------------------------- cadquery
def _exec_python_blocks(rel: str) -> dict:
    pytest.importorskip("cadquery")
    ns: dict = {}
    for i, body in enumerate(blocks(rel, "python")):
        try:
            exec(compile(body, f"{rel}:block{i}", "exec"), ns)  # noqa: S102 - our own docs
        except Exception as e:  # pragma: no cover - failure path
            pytest.fail(f"{rel} python block {i} failed: {type(e).__name__}: {e}")
    return ns


def test_cadquery_cookbook_runs() -> None:
    ns = _exec_python_blocks("cadquery/cookbook.md")
    assert "selfcheck" in ns and "result" in ns


def test_cadquery_contract_example_runs() -> None:
    pytest.importorskip("cadquery")  # the [cad] extra — absent on a [dev]-only install (CI)

    ns = _exec_python_blocks("cadquery/contract.md")
    result = ns["result"]
    bb = result.toCompound().BoundingBox()
    assert abs(bb.zmin) < 1e-6 and abs(bb.zmax - 0.45) < 1e-3
    assert [c.name for c in result.children] == ["Top", "Leg_0", "Leg_1", "Leg_2", "Leg_3"]


# --------------------------------------------------------------------- blender
@pytest.mark.blender
def test_blender_cookbook_runs(tmp_path) -> None:
    code = "\n".join(blocks("blender/cookbook.md", "python"))
    out = run_blender_script(tmp_path, code)
    assert "mesh objects" in out


@pytest.mark.blender
def test_blender_contract_example_builds_and_exports(tmp_path) -> None:
    code = "\n".join(blocks("blender/contract.md", "python"))
    glb = tmp_path / "object.glb"
    code += (
        "\n_selfcheck()\nimport bpy\n"
        f"bpy.ops.export_scene.gltf(filepath={str(glb)!r}, export_format='GLB', "
        "export_yup=True, export_apply=True)\n"
        "print('PARTS', sorted(o.name for o in bpy.data.objects))\n"
    )
    out = run_blender_script(tmp_path, code)
    assert "PARTS ['Leg_0', 'Leg_1', 'Leg_2', 'Seat']" in out
    assert glb.stat().st_size > 1000
    trimesh = pytest.importorskip("trimesh")
    scene = trimesh.load(str(glb))
    lo, hi = scene.bounds
    assert abs(lo[1]) < 1e-3 and abs(hi[1] - 0.45) < 1e-3  # Y-up GLB, on the ground


@pytest.mark.blender
def test_urdf_cookbook_runs(tmp_path) -> None:
    code = "\n".join(blocks("urdf/cookbook.md", "python"))
    out = run_blender_script(tmp_path, code)
    assert "[selfcheck] links" in out


def _build_urdf_example(tmp_path, tag: str, model_py: str, urdf: str):
    """Run a doc example through the real urdf_blender build and the round's joint_sweep gate."""
    from codeverse3d.config import get_settings
    from codeverse3d.languages.urdf import UrdfBlenderRuntime
    from codeverse3d.spatial.joints_sweep import sweep_gate
    from codeverse3d.workspace import Workspace

    if not get_settings().resolve_blender():
        pytest.skip("no Blender binary configured")
    ws = Workspace(tmp_path / tag).create()
    ws.src.mkdir(parents=True, exist_ok=True)
    (ws.src / "model.py").write_text(model_py)
    (ws.src / "robot.urdf").write_text(urdf)
    res = UrdfBlenderRuntime().build(ws)
    assert res.ok, f"{tag}: {res.error_type}: {res.error_message}"
    warns = [f["message"] for f in res.census["lint"] if f["severity"] != "info"]
    assert warns == [], f"{tag}: lint warnings {warns}"
    gate, _ = sweep_gate(ws)
    assert not gate.findings, f"{tag}: {[f.message for f in gate.findings]}"
    return ws, res


@pytest.mark.blender
def test_urdf_contract_example_builds_and_lid_opens_upward(tmp_path) -> None:
    """The contract's COMPLETE example must pass the harness build exactly as written
    (FK consistency, no lint warnings, clean sweep) and the lid must open upward."""
    from codeverse3d.spatial.joints_model import link_world_meshes, load_urdf

    ws, _ = _build_urdf_example(tmp_path, "pedalbin", "\n".join(blocks("urdf/contract.md", "python")),
                                blocks("urdf/contract.md", "xml")[0])
    r = load_urdf(ws.artifacts / "robot.urdf", ws.artifacts / "meshes")
    tops = {q: link_world_meshes(r, {"body_to_lid": q})["lid"].bounds[1][2] for q in (0.0, 0.75, 1.5)}
    assert tops[1.5] > tops[0.75] > tops[0.0] + 0.05, f"lid does not open upward: {tops}"


@pytest.mark.blender
@pytest.mark.parametrize("index,tag", [(0, "cabinet"), (1, "laptop"), (2, "cart")])
def test_urdf_cookbook_worked_examples_build(tmp_path, index: int, tag: str) -> None:
    """Each worked example (prelude + helpers + example block, paired with its URDF) must
    pass the harness build: world meshes + visual/collision origin = −pivot."""
    py = blocks("urdf/cookbook.md", "python")
    xml = blocks("urdf/cookbook.md", "xml")
    _build_urdf_example(tmp_path, tag, "\n".join(py[:2]) + "\n" + py[2 + index], xml[index])


# ------------------------------------------------------------------------ node
@pytest.mark.node
def test_threejs_cookbook_runs(tmp_path) -> None:
    body = "".join(
        f"\nconsole.log('--- block {i} ---');\n" + strip_imports_exports(b)
        for i, b in enumerate(blocks("threejs/cookbook.md", "js"))
    )
    out = run_node_module(tmp_path, js_prelude() + body + "\nconsole.log('JS_BLOCKS_OK');\n")
    assert "JS_BLOCKS_OK" in out and "[selfcheck]" in out


@pytest.mark.node
def test_threejs_contract_example_builds_glb(tmp_path) -> None:
    files = labelled_files("threejs/contract.md")
    assert set(files) == {"src/parts/seat.js", "src/parts/legs.js", "src/object.js"}
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    runner = """
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { writeFileSync } from 'node:fs';
import { build } from './src/object.js';
globalThis.FileReader = class {
  readAsArrayBuffer(b) { b.arrayBuffer().then((x) => { this.result = x; this.onloadend && this.onloadend(); }); }
};
const root = build(THREE);
let verts = 0; root.traverse((o) => { if (o.isMesh) verts += o.geometry.attributes.position.count; });
const box = new THREE.Box3().setFromObject(root);
if (verts < 100) throw new Error('too few vertices: ' + verts);
if (Math.abs(box.min.y) > 1e-3) throw new Error('not on ground: ' + box.min.y);
const buf = await new GLTFExporter().parseAsync(root, { binary: true });
writeFileSync('object.glb', Buffer.from(buf));
console.log('GLB_OK', verts, buf.byteLength);
"""
    out = run_node_module(tmp_path, runner)
    assert "GLB_OK" in out
    assert (tmp_path / "object.glb").stat().st_size > 1000


@pytest.mark.node
def test_scene_cookbook_runs(tmp_path) -> None:
    stub = (
        "function collectAnimatedMaterials(scene){const mats=[];scene.traverse(o=>{"
        "const l=Array.isArray(o.material)?o.material:(o.material?[o.material]:[]);"
        "for(const m of l) if(m.userData&&typeof m.userData.update==='function'&&!mats.includes(m))"
        "mats.push(m);});return mats;}\n"
    )
    body = "".join(
        f"\nconsole.log('--- block {i} ---');\n" + strip_imports_exports(b)
        for i, b in enumerate(blocks("scene_threejs/cookbook.md", "js"))
    )
    out = run_node_module(tmp_path, js_prelude(stub) + body + "\nconsole.log('JS_BLOCKS_OK');\n")
    assert "JS_BLOCKS_OK" in out and "[selfcheck]" in out


@pytest.mark.node
def test_scene_contract_example_shape(tmp_path) -> None:
    files = labelled_files("scene_threejs/contract.md")
    assert set(files) == {"src/env.js", "src/zones/grove.js", "src/scene.js"}
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    runner = """
import * as THREE from 'three';
import { createScene } from './src/scene.js';
const r = await createScene({ THREE, renderer: null, loaders: {} });
if (!r.scene || !r.scene.isScene) throw new Error('no scene');
if (!r.scene.fog) throw new Error('no fog');
if (!Array.isArray(r.cameras) || r.cameras.length < 2) throw new Error('cameras');
for (const c of r.cameras) {
  if (typeof c.name !== 'string' || !Array.isArray(c.position) || !Array.isArray(c.lookAt))
    throw new Error('camera shape: ' + JSON.stringify(c));
}
r.update(0, 0); r.update(1.5, 0.016);
console.log('SCENE_OK', r.cameras.map((c) => c.name).join(','));
"""
    out = run_node_module(tmp_path, runner)
    assert "SCENE_OK" in out


@pytest.mark.node
def test_scene_cookbook_sky_shader_compiles() -> None:
    """The inline sky-dome ShaderMaterial from the scene cookbook must also compile — as a Mesh
    (the dome) and as an InstancedMesh, through the production host (``check_shaders.mjs``)."""
    body = "".join("\n" + strip_imports_exports(b) for b in blocks("scene_threejs/cookbook.md", "js")[:2])
    scene = (
        "import * as THREE from 'three';\n" + body
        + "\nexport function createScene() {\n"
        "  const scene = new THREE.Scene();\n"
        "  const env = buildEnv({ THREE, scene });\n"
        "  const sky = scene.getObjectByName('SkyDome');\n"
        "  scene.add(new THREE.InstancedMesh(new THREE.PlaneGeometry(2, 2), sky.material, 4));\n"
        "  return { scene, cameras: [{ name: 'a', position: [3, 3, 5], lookAt: [0, 0, 0] }], update: env.update };\n"
        "}\n"
    )
    code, out = compile_scene(scene)
    assert code == 0, f"sky shader failed to compile:\n{out}"
