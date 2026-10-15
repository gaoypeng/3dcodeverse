"""The cookbooks' and contracts' promise: every snippet RUNS.

* cadquery blocks run in-process (cadquery is a plain library).
* bpy blocks run in real headless Blender (marker: blender).
* js blocks run in real node against runtime_js/node_modules (marker: node).
* the scene languages ship no cookbook (owner, 2026-10-15): their contracts' COMPLETE examples
  run through the real runtimes below, and that is the whole of their runnable prompt material.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.config import get_settings
from tests.prompts.conftest import (
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
def test_scene_contract_env_example_is_the_interface_and_nothing_else(tmp_path) -> None:
    """The contract's one labelled file is ``src/env.js`` with the three exports the assembler reads and
    no scene inside it (the complete day scene it used to carry — a sun rig, a grove of cones, the
    +X+Z corner camera — was the most pasteable passage of the prompt corpus, 2026-10-15)."""
    files = labelled_files("scene_threejs/contract.md")
    assert set(files) == {"src/env.js"}
    body = files["src/env.js"]
    assert len(body.strip().splitlines()) <= 12
    for word in ("sunRig", "Fog(", "PlaneGeometry", "position: [", "Cone"):
        assert word not in body, word
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
    """The scratch workspace the scene_blender snippets run in: the skeleton of the bpy worked example
    (its two module assets build first → ``ctx.assets``), through the real runtime."""
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
def test_the_bpy_plan_skeleton_builds_in_blender_as_written(tmp_path) -> None:
    """The fresh skeleton (a stub env — a level plate and nothing that passes: no world, lamp or
    atmosphere — and a TODO stub per zone) builds in Blender 5.2.2 before an author touches it: every
    zone ok, no env error, a ground in the census and no fog or light until the author writes the
    brief's (2026-10-15)."""
    from codeverse3d.conventions import to_pascal

    ws, rt, plan = _bpy_workspace(tmp_path)
    zones = {rt.zone_file(z.name).rsplit("/", 1)[-1][:-3] for z in plan.zones}
    run = rt.run_wrapper(ws, ws.artifacts, zones={n: to_pascal(n) for n in zones}, blend=False, timeout_s=600)
    errors = [(e.get("stage"), e.get("name"), e.get("error_file"), e.get("error_line"), e.get("error_message"))
              for e in run.report.get("errors") or []]
    assert run.result.ok and not errors, (run.result.error_type, run.result.error_message, errors)
    assert sorted(run.report["zones_ok"]) == sorted(zones)
    assert run.facts.get("fog") is None and not run.facts.get("lights"), {k: run.facts.get(k) for k in ("fog", "lights")}


@pytest.mark.blender
@pytest.mark.node
def test_scene_blender_contract_examples_build_through_the_runtime(tmp_path) -> None:
    """The contract's two COMPLETE files (built on the shipped bpy library, D51) lint clean, assemble,
    build and seat what they place — through the real runtime, gates included.  The contract's python
    blocks are exactly these two labelled files: nothing else of the bpy prompt material is code."""
    assert len(blocks("scene_blender/contract.md", "python")) == len(labelled_files("scene_blender/contract.md", tag="python"))
    from codeverse3d.contracts.artifacts import Severity
    from codeverse3d.languages import scene_blender as sb
    from tests.scene_runtime.conftest import browser_available

    lib = Path(sb.__file__).resolve().parent / "starter" / "src" / "lib"
    if not lib.is_dir():
        pytest.skip("the scene_blender starter library (lane B2) is not in this tree yet")
    if not browser_available():
        pytest.skip("puppeteer/chrome required for the census probe")
    ws, rt, plan = _bpy_workspace(tmp_path)
    files = labelled_files("scene_blender/contract.md", tag="python")
    assert set(files) == {"src/env.py", "src/zones/quay.py"}
    for rel, body in files.items():
        (ws.root / rel).write_text(body)
    rep = rt.lint(ws)
    assert not [f for f in rep.findings if f.severity is not Severity.INFO], [(f.target, f.message) for f in rep.findings]
    asm = rt.assemble(ws, plan)
    assert "quay" in asm.zones_included and not asm.zones_failed and asm.env_ok, asm
    res = rt.build(ws)
    assert res.ok, res.stdout_tail
    rows = {r["name"]: r for r in res.census["placement"]["assets"]}
    assert rows["QuayDeck"]["supported"] and not rows["QuayDeck"]["floating"]
    assert all(rows[f"Bollard_{i}"]["supported"] for i in range(6)), {k: v for k, v in rows.items() if k.startswith("Bollard")}
