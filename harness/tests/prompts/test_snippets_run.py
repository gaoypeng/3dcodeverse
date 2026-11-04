"""The contracts' promise (D107): each language's ONE interface example in contract.md RUNS - cadquery in-process,
bpy in headless Blender (marker: blender), js in node (marker: node).  The tests pin the INTERFACE the example
demonstrates, never its shape, part names or counts."""

from __future__ import annotations

import pytest

from codeverse3d.config import get_settings
from tests.prompts.conftest import (
    blocks,
    labelled_files,
    run_blender_script,
    run_node_module,
)


# --------------------------------------------------------------------- cadquery
def test_cadquery_contract_example_runs() -> None:
    """The example defines ``result`` (what the wrapper exports), an Assembly with at least one
    named child, lowest point on the ground."""
    pytest.importorskip("cadquery")  # the [cad] extra — absent on a [dev]-only install (CI)

    ns: dict = {}
    bodies = blocks("cadquery/contract.md", "python")
    assert bodies, "cadquery/contract.md keeps ONE executable interface example"
    for body in bodies:
        exec(compile(body, "cadquery/contract.md", "exec"), ns)  # noqa: S102 - our own docs
    result = ns["result"]
    assert result.children, "the example must add at least one named part"
    assert all(c.name for c in result.children)
    bb = result.toCompound().BoundingBox()
    assert abs(bb.zmin) < 1e-6, f"not on the ground: zmin {bb.zmin}"
    assert bb.zmax > 0.01

# --------------------------------------------------------------------- blender
@pytest.mark.blender
def test_blender_contract_example_builds_and_exports(tmp_path) -> None:
    """The example's labelled files (``src/model.py`` + any ``src/parts/*.py``) run in headless Blender the way..."""
    files = labelled_files("blender/contract.md", tag="python")
    assert "src/model.py" in files, f"blender/contract.md keeps ONE executable interface example: {sorted(files)}"
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    glb = tmp_path / "object.glb"
    code = f"import sys\nsys.path.insert(0, {str(tmp_path / 'src')!r})\n" + files["src/model.py"]
    code += (
        "\nimport bpy\n"
        f"bpy.ops.export_scene.gltf(filepath={str(glb)!r}, export_format='GLB', "
        "export_yup=True, export_apply=True)\n"
        "print('PARTS', sorted(o.name for o in bpy.data.objects if o.type == 'MESH'))\n"
    )
    out = run_blender_script(tmp_path, code)
    parts_line = next(line for line in out.splitlines() if line.startswith("PARTS "))
    assert parts_line != "PARTS []", "the example must leave at least one mesh object"
    assert glb.stat().st_size > 200
    trimesh = pytest.importorskip("trimesh")
    scene = trimesh.load(str(glb))
    lo, hi = scene.bounds
    assert abs(lo[1]) < 1e-3 and hi[1] > 0.01, (lo, hi)  # Y-up GLB, on the ground

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
def test_urdf_contract_example_builds_and_its_joint_moves_the_child(tmp_path) -> None:
    """The contract's example (one python block + one URDF) must pass the harness build exactly as written — FK..."""
    import numpy as np

    from codeverse3d.spatial.joints_model import link_world_meshes, load_urdf

    py, xml = blocks("urdf/contract.md", "python"), blocks("urdf/contract.md", "xml")
    assert py and xml, "urdf/contract.md keeps ONE python + ONE numeric xml example (the FK rule's executable proof)"
    ws, _ = _build_urdf_example(tmp_path, "example", "\n".join(py), xml[0])
    r = load_urdf(ws.artifacts / "robot.urdf", ws.artifacts / "meshes")
    joint = next(j for j in r.joints.values() if j.movable and j.lower is not None and j.upper is not None)
    at = {q: link_world_meshes(r, {joint.name: q})[joint.child].bounds for q in (joint.lower, joint.upper)}
    assert not np.allclose(at[joint.lower], at[joint.upper]), f"{joint.name} does not move {joint.child}"
    if joint.name == "body_to_lid":   # the pedal-bin example: the lid's top rises through the sweep
        tops = {q: link_world_meshes(r, {joint.name: q})["lid"].bounds[1][2] for q in (0.0, 0.75, 1.5)}
        assert tops[1.5] > tops[0.75] > tops[0.0] + 0.05, f"lid does not open upward: {tops}"

# ------------------------------------------------------------------------ node
@pytest.mark.node
def test_threejs_contract_example_builds_glb(tmp_path) -> None:
    """The labelled files of the example are the entry ``src/object.js`` (+ any ``src/parts/*``);
    ``build(THREE)`` returns an Object3D holding at least one mesh, on the ground, that exports."""
    files = labelled_files("threejs/contract.md")
    assert "src/object.js" in files, sorted(files)
    assert all(rel.startswith("src/") for rel in files), sorted(files)
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
let meshes = 0; root.traverse((o) => { if (o.isMesh) meshes += 1; });
const box = new THREE.Box3().setFromObject(root);
if (meshes < 1) throw new Error('no mesh in the built object');
if (Math.abs(box.min.y) > 1e-3) throw new Error('not on ground: ' + box.min.y);
const buf = await new GLTFExporter().parseAsync(root, { binary: true });
writeFileSync('object.glb', Buffer.from(buf));
console.log('GLB_OK', meshes, buf.byteLength);
"""
    out = run_node_module(tmp_path, runner)
    assert "GLB_OK" in out
    assert (tmp_path / "object.glb").stat().st_size > 200

@pytest.mark.node
def test_scene_contract_env_example_is_the_interface_and_nothing_else(tmp_path) -> None:
    """The contract's one labelled file is ``src/env.js`` with the three exports the assembler reads and no scene..."""
    files = labelled_files("scene_threejs/contract.md")
    assert set(files) == {"src/env.js"}
    body = files["src/env.js"]
    p = tmp_path / "src" / "env.js"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    runner = """
import * as THREE from 'three';
import * as env from './src/env.js';
if (typeof env.heightAt !== 'function' || typeof env.heightAt(3, 4) !== 'number') throw new Error('heightAt');
if (typeof env.SUN_AZIMUTH_DEG !== 'number') throw new Error('SUN_AZIMUTH_DEG');
const scene = new THREE.Scene();
const out = env.buildEnv({ THREE, scene });
if (!out || typeof out.update !== 'function') throw new Error('buildEnv must return { update }');
out.update(0, 0); out.update(1.5, 0.016);
console.log('ENV_OK');
"""
    out = run_node_module(tmp_path, runner)
    assert "ENV_OK" in out

# --------------------------------------------------------------------- scene_blender
def _bpy_workspace(tmp_path):
    """The scratch workspace the scene_blender snippets run in."""
    from codeverse3d.contracts.common import Language, Track
    from codeverse3d.contracts.plan import ScenePlan
    from codeverse3d.languages import get_runtime
    from codeverse3d.tracks.planner import plan_example
    from codeverse3d.workspace import Workspace

    if not get_settings().resolve_blender():
        pytest.skip("no Blender binary configured")
    plan = ScenePlan.model_validate(plan_example(Track.SCENE, Language.SCENE_BLENDER))
    ws = Workspace(tmp_path / "ws")
    rt = get_runtime(Language.SCENE_BLENDER)
    rt.skeleton(ws, plan)
    return ws, rt, plan

@pytest.mark.blender
@pytest.mark.node
def test_scene_blender_contract_examples_build_through_the_runtime(tmp_path) -> None:
    """The contract's two labelled files (src/env.py + src/zones/quay.py) lint clean, assemble, build and seat
    what they place through the real runtime, gates included."""
    from codeverse3d.contracts.artifacts import Severity
    from tests.scene_runtime.conftest import browser_available

    rel = "scene_blender/contract.md"
    files = labelled_files(rel, tag="python")
    assert len(blocks(rel, "python")) == len(files) and set(files) == {"src/env.py", "src/zones/quay.py"}
    if not browser_available():
        pytest.skip("puppeteer/chrome required for the census probe")
    ws, rt, plan = _bpy_workspace(tmp_path)
    for path, body in files.items():
        (ws.root / path).write_text(body)
    rep = rt.lint(ws)
    assert not [f for f in rep.findings if f.severity is not Severity.INFO], [(f.target, f.message) for f in rep.findings]
    asm = rt.assemble(ws, plan)
    assert "quay" in asm.zones_included and not asm.zones_failed and asm.env_ok, asm
    res = rt.build(ws)
    assert res.ok, res.stdout_tail
    rows = {r["name"]: r for r in res.census["placement"]["assets"]}
    assert rows["QuayDeck"]["supported"] and not rows["QuayDeck"]["floating"]
    assert all(rows[f"Bollard_{i}"]["supported"] for i in range(6)), {k: v for k, v in rows.items() if k.startswith("Bollard")}

# --------------------------------------------------------------------- glsl_shader
def test_glsl_shader_contract_example_builds_and_moves(tmp_ws) -> None:
    """The contract's ONE complete ``mainImage`` (the block that defines it) is ``src/shader.frag`` as written."""
    pytest.importorskip("moderngl")
    import json

    from codeverse3d.contracts.artifacts import Severity
    from codeverse3d.contracts.plan import GraphicsPlan, PassPlan
    from codeverse3d.languages.glsl_shader import GlslShaderRuntime
    from codeverse3d.spatial.gl_render import GlHost, GlHostError

    complete = [b for b in blocks("glsl_shader/contract.md", "glsl") if "void mainImage(" in b and "..." not in b]
    assert len(complete) == 1, "glsl_shader/contract.md keeps exactly ONE complete mainImage example"
    plan = GraphicsPlan(title="Example", summary="s", style="any", passes=[PassPlan(name="Image", description="d")],
                        motion="m", key_visuals=["k"], duration_s=6.0, resolution=(256, 144))
    tmp_ws.write_json(tmp_ws.plan_path, plan)
    tmp_ws.src.mkdir(parents=True, exist_ok=True)
    (tmp_ws.src / "shader.frag").write_text(complete[0])
    host = GlHost(timeout_s=120)
    rt = GlslShaderRuntime(host=host)
    rep = rt.lint(tmp_ws)
    assert rep.passed and not [f for f in rep.findings if f.severity is not Severity.INFO], [f.message for f in rep.findings]
    try:
        br = rt.build(tmp_ws, width=256, height=144, preview=False)
    except GlHostError as e:  # pragma: no cover - machine without GL
        pytest.skip(f"no OpenGL context: {e}")
    assert br.ok, br.error_message
    stats = json.loads((tmp_ws.artifacts / "metrics.json").read_text())["stats"]
    assert not stats["static"] and not stats["any_nan"], stats
