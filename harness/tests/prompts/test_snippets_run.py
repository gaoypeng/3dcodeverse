"""The cookbooks' promise: every snippet RUNS.

* cadquery blocks run in-process (cadquery is a plain library).
* bpy blocks run in real headless Blender (marker: blender).
* js blocks run in real node against runtime_js/node_modules (marker: node).
* GLSL factories compile in real headless-Chrome WebGL (marker: node).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tests.prompts.conftest import (
    HELPERS,
    blocks,
    js_prelude,
    labelled_files,
    run_blender_script,
    run_node_module,
    strip_imports_exports,
)


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
    import cadquery as cq  # noqa: F401

    ns = _exec_python_blocks("cadquery/contract.md")
    result = ns["result"]
    bb = result.toCompound().BoundingBox()
    assert abs(bb.zmin) < 1e-6 and abs(bb.zmax - 0.45) < 1e-3
    assert [c.name for c in result.children] == ["Top", "Leg1", "Leg2", "Leg3", "Leg4"]


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
    assert "PARTS ['Leg1', 'Leg2', 'Leg3', 'Seat']" in out
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


@pytest.mark.blender
def test_urdf_contract_example_fk_and_clearance(tmp_path) -> None:
    """Full pipeline: build in Blender, export per-link meshes in link frames,
    load the URDF with yourdfpy, sweep the lid — it must open upward, no penetration."""
    yourdfpy = pytest.importorskip("yourdfpy")
    trimesh = pytest.importorskip("trimesh")
    pytest.importorskip("fcl")
    code = "\n".join(blocks("urdf/contract.md", "python"))
    meshes = tmp_path / "meshes"
    meshes.mkdir()
    code += (
        "\nimport mathutils, bpy\n"
        "for _o in [o for o in bpy.data.objects if o.type == 'MESH']:\n"
        "    bpy.ops.object.select_all(action='DESELECT'); _o.select_set(True)\n"
        "    bpy.context.view_layer.objects.active = _o\n"
        "    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)\n"
        "    _o.data.transform(mathutils.Matrix.Translation(-PIVOT[_o.name]))\n"
        f"    bpy.ops.export_scene.gltf(filepath={str(meshes)!r} + '/' + _o.name + '.glb',\n"
        "        export_format='GLB', use_selection=True, export_yup=False, export_apply=True)\n"
    )
    run_blender_script(tmp_path, code)
    urdf_text = blocks("urdf/contract.md", "xml")[0]
    (tmp_path / "robot.urdf").write_text(urdf_text)
    u = yourdfpy.URDF.load(
        str(tmp_path / "robot.urdf"), build_scene_graph=True, load_meshes=True, mesh_dir=str(tmp_path)
    )
    tops = {}
    for q in (0.0, 0.75, 1.5):
        u.update_cfg({"BodyToLid": q})
        s = u.scene
        cm = trimesh.collision.CollisionManager()
        world = {}
        for name, geom in s.geometry.items():
            node = next(n for n in s.graph.nodes_geometry if s.graph[n][1] == name)
            m = geom.copy()
            m.apply_transform(s.graph[node][0])
            world[node] = m
            cm.add_object(node, m)
        _, pairs = cm.in_collision_internal(return_names=True)
        # only the intended weld (Knob into Lid, fixed joint) may touch
        unexpected = {p for p in pairs if set(x.split(".glb")[0] for x in p) != {"Knob", "Lid"}}
        assert not unexpected, f"unexpected penetration at q={q}: {unexpected}"
        tops[q] = world[next(k for k in world if k.startswith("Lid"))].bounds[1][2]
    assert tops[1.5] > tops[0.75] > tops[0.0] + 0.05, f"lid does not open upward: {tops}"


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
def test_glsl_cookbook_compiles_in_chrome(tmp_path) -> None:
    """Every make*Material factory compiles in a real WebGL context (GPU or SwiftShader),
    on a Mesh and on an InstancedMesh, with fog + log-depth renderer settings."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    mod = "import * as THREE from 'three';\n" + "".join(
        "\n" + strip_imports_exports(b) for b in blocks("scene_threejs/glsl_cookbook.md", "js")
    )
    names = sorted(set(re.findall(r"function (make\w*Material)\(", mod)))
    assert len(names) >= 8, f"expected the GLSL factories, found {names}"
    mod += "\nexport { " + ", ".join(names) + " };\n"
    link = tmp_path / "node_modules"
    if not link.exists():
        from tests.prompts.conftest import RUNTIME_JS

        link.symlink_to(RUNTIME_JS / "node_modules")
    (tmp_path / "glsl_mod.mjs").write_text(mod)
    proc = subprocess.run(
        [node, str(HELPERS / "glsl_compile.mjs"), str(tmp_path), "glsl_mod.mjs"],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.stdout.strip(), f"no output: {proc.stderr[-2000:]}"
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report.get("ok"), f"GLSL compile failures: {json.dumps(report, indent=2)[:4000]}"
    checked = {r["name"] for r in report["results"]}
    assert set(names) <= checked


@pytest.mark.node
def test_scene_cookbook_sky_shader_compiles(tmp_path) -> None:
    """The inline sky-dome ShaderMaterial from the scene cookbook must also compile."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    body = "".join("\n" + strip_imports_exports(b) for b in blocks("scene_threejs/cookbook.md", "js")[:2])
    mod = (
        "import * as THREE from 'three';\n" + body
        + "\nexport function makeSkyMaterial(THREE_) {\n"
        "  const s = new THREE_.Scene();\n"
        "  buildEnv(THREE_, s);\n"
        "  return s.getObjectByName('SkyDome').material;\n"
        "}\n"
    )
    link = tmp_path / "node_modules"
    if not link.exists():
        from tests.prompts.conftest import RUNTIME_JS

        link.symlink_to(RUNTIME_JS / "node_modules")
    (tmp_path / "sky_mod.mjs").write_text(mod)
    proc = subprocess.run(
        [node, str(HELPERS / "glsl_compile.mjs"), str(tmp_path), "sky_mod.mjs"],
        capture_output=True, text=True, timeout=300,
    )
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report.get("ok"), f"sky shader failed to compile: {json.dumps(report)[:3000]}"
