"""D114: a NaN / inf the model's bpy code leaves in a transform or a vertex is the MODEL's build error (it once
reached the census GLB as ``NaN``, the probe's ``JSON.parse`` failed and the repair loop blamed the harness):
the census writer refuses to write one, a real Blender zone with ``location.x = nan`` fails naming object,
channel and file, and generated image pixels are packed into ``scene.blend``."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import GateReport
from codeverse3d.contracts.common import Language, Usage
from codeverse3d.languages import get_runtime
from codeverse3d.languages.blender import blender_env
from codeverse3d.proc import run_subprocess
from codeverse3d.tracks import common as C
from codeverse3d.tracks import repair as R
from codeverse3d.workspace import Workspace
from tests.scene_blender.fixtures import ENV

WRAPPERS = Path(__file__).resolve().parents[2] / "codeverse3d" / "languages" / "wrappers"
NAN, INF = float("nan"), float("inf")
needs_blender = pytest.mark.skipif(not get_settings().resolve_blender(), reason="no Blender binary")


def _load(name: str, monkeypatch) -> Any:
    """A wrapper module is executed BY Blender (it imports its siblings by bare name): load it by path."""
    monkeypatch.syspath_prepend(str(WRAPPERS))
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", WRAPPERS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _obj(name: str, *, location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), parent=None,
         mode: str = "XYZ", quaternion=(1.0, 0.0, 0.0, 0.0)) -> SimpleNamespace:
    """The transform channels of a bpy Object, as plain data."""
    return SimpleNamespace(name=name, location=location, rotation_euler=rotation, rotation_quaternion=quaternion,
                           rotation_axis_angle=(0.0, 0.0, 1.0, 0.0), rotation_mode=mode, scale=scale,
                           delta_location=(0.0, 0.0, 0.0), delta_rotation_euler=(0.0, 0.0, 0.0),
                           delta_rotation_quaternion=(1.0, 0.0, 0.0, 0.0), delta_scale=(1.0, 1.0, 1.0),
                           matrix_parent_inverse=np.eye(4).tolist(), parent=parent)


def test_the_census_writer_never_writes_a_nan(monkeypatch) -> None:
    g = _load("_census_glb", monkeypatch)
    w = g._Writer(np.eye(3))
    ok = np.eye(4)
    assert w.node_matrix(ok, _obj("Fine")) == ok.T.reshape(-1).tolist() and not w.bad
    broken = np.eye(4)
    broken[0, 3] = NAN
    assert w.node_matrix(broken, _obj("Pillar", location=(NAN, 0.0, 0.0))) is None
    assert [r["object"] for r in w.bad.report()["nonfinite"]] == ["Pillar"]
    # the last line of defence: the JSON chunk refuses NaN outright
    w.nodes.append({"name": "x", "matrix": [NAN] * 16})
    with pytest.raises(ValueError):
        w.glb({})


# --------------------------------------------------------------------------- real Blender
SCENE = '''ZONES = {"ruins": "Ruins", "meadow": "Meadow"}
ASSETS = {"rock": "Rock"}
HEROES = {}
CAMERAS = [{"name": "overview", "location": (20.0, -20.0, 12.0), "look_at": (0.0, 0.0, 0.0), "fov": 50}]
'''

ROCK = '''import bpy


def build_rock(ctx, variant=0):
    me = bpy.data.meshes.new("RockBody")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [], [(0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)])
    ctx.collection.objects.link(bpy.data.objects.new("RockBody", me))
    return ctx.collection
'''

RUINS = '''import bpy


def build(ctx):
    me = bpy.data.meshes.new("Block")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    pillar = bpy.data.objects.new("Pillar", me)
    pillar.location.x = float("nan")
    ctx.collection.objects.link(pillar)
    root = bpy.data.objects.new("ArchRoot", None)
    root.rotation_euler.z = float("nan")
    ctx.collection.objects.link(root)
    arch = bpy.data.objects.new("Arch", me.copy())
    arch.parent = root
    ctx.collection.objects.link(arch)
    return ctx.collection
'''

MEADOW = '''import bpy


def build(ctx):
    for i, x in enumerate((0.0, 3.0)):
        e = bpy.data.objects.new(f"Rock_{i}", None)
        e.instance_type = "COLLECTION"
        e.instance_collection = ctx.assets["rock"]
        e.location = (x, 5.0, 0.0)
        ctx.collection.objects.link(e)
    # a generated texture: pixels that exist only in memory until the .blend packs them
    img = bpy.data.images.new("Moss", 4, 4)
    img.pixels = [0.2, 0.6, 0.1, 1.0] * 16
    mat = bpy.data.materials.new("MossMat")
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    mat.node_tree.links.new(tex.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    me = bpy.data.meshes.new("Turf")
    me.from_pydata([(0, 0, 0), (2, 0, 0), (0, 2, 0)], [], [(0, 1, 2)])
    me.materials.append(mat)
    ctx.collection.objects.link(bpy.data.objects.new("Turf", me))
    return ctx.collection
'''


def _workspace(root: Path, *, ruins: str = RUINS, meadow: str = MEADOW, rock: str = ROCK) -> Workspace:
    ws = Workspace(root / "run").create()
    files = {"src/scene.py": SCENE, "src/env.py": ENV, "src/assets/rock.py": rock, "src/zones/ruins.py": ruins,
             "src/zones/meadow.py": meadow}
    for rel, text in files.items():
        p = ws.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return ws


def _image_pixels(blend: Path, image: str) -> list[float]:
    """The first pixel of ``image`` in ``blend`` as a fresh Blender process reopens it."""
    code = (f"import bpy, sys\nbpy.ops.wm.open_mainfile(filepath={str(blend)!r})\n"
            f"img = bpy.data.images[{image!r}]\nprint('PIXELS', list(img.pixels[:4]), img.packed_file is not None)\n")
    proc = run_subprocess([get_settings().resolve_blender(), "-b", "--factory-startup", "--python-expr", code],
                          cwd=blend.parent, env=blender_env(), timeout_s=120)
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("PIXELS"))
    vals, packed = line.removeprefix("PIXELS ").rsplit(" ", 1)
    assert packed == "True", line
    return [float(v) for v in vals.strip("[]").split(",")]


@pytest.mark.blender
@needs_blender
def test_a_nan_in_a_zone_is_the_models_build_error(tmp_path) -> None:
    rt = get_runtime(Language.SCENE_BLENDER)
    ws = _workspace(tmp_path)
    res = rt.build(ws)   # the wrapper fails, so no probe runs: no headless Chrome needed
    assert not res.ok and not res.harness_failure, (res.error_type, res.error_message)
    assert res.error_type == "NonFiniteTransform" and res.error_file == "src/zones/ruins.py"
    assert "'Pillar' [src/zones/ruins.py]: location=(nan, 0, 0)" in res.error_message
    assert "'ArchRoot' [src/zones/ruins.py]: rotation_euler=(0, 0, nan) (makes 'Arch' non-finite)" in res.error_message
    report = json.loads((ws.artifacts / "bpy_build.json").read_text())
    assert report["error_type"] == "NonFiniteTransform" and not report["ok"]
    assert {(r["object"], r["file"]) for r in report["nonfinite"]} == {("Pillar", "src/zones/ruins.py"),
                                                                     ("ArchRoot", "src/zones/ruins.py")}
    assert not (ws.artifacts / "census.glb").exists(), "nothing written: never a NaN in the GLB's JSON"
    assert set(report["zones_ok"]) == {"ruins", "meadow"}


@pytest.mark.blender
@needs_blender
def test_the_scene_blend_keeps_generated_pixels_for_the_judged_frames(tmp_path) -> None:
    """The render driver reopens ``scene.blend`` for every judged frame: a texture the model made in
    memory (``images.new`` + ``pixels``) must be packed into it, or it reopens blank (black)."""
    rt = get_runtime(Language.SCENE_BLENDER)
    ruins = RUINS.replace('    pillar.location.x = float("nan")\n', "").replace('    root.rotation_euler.z = float("nan")\n', "")
    ws = _workspace(tmp_path, ruins=ruins)
    run = rt.run_wrapper(ws, ws.artifacts)
    assert run.result.ok, (run.result.error_type, run.result.error_message)
    assert (ws.artifacts / "census.glb").stat().st_size > 0
    px = _image_pixels(ws.artifacts / "scene.blend", "Moss")
    assert px == pytest.approx([0.2, 0.6, 0.1, 1.0], abs=1 / 255), px
    assert all(math.isfinite(v) for v in px)


@pytest.mark.blender
@needs_blender
def test_a_nan_in_a_zone_is_the_models_build_error_and_reaches_a_repair(tmp_path, monkeypatch) -> None:
    rt = get_runtime(Language.SCENE_BLENDER)
    ws = _workspace(tmp_path)
    res = rt.build(ws)   # the wrapper fails, so no probe runs: no headless Chrome needed
    assert not res.ok and not res.harness_failure, (res.error_type, res.error_message)
    assert res.error_type == "NonFiniteTransform" and res.error_file == "src/zones/ruins.py"
    assert "'Pillar' [src/zones/ruins.py]: location=(nan, 0, 0)" in res.error_message
    assert "'ArchRoot' [src/zones/ruins.py]: rotation_euler=(0, 0, nan) (makes 'Arch' non-finite)" in res.error_message
    report = json.loads((ws.artifacts / "bpy_build.json").read_text())
    assert report["error_type"] == "NonFiniteTransform" and not report["ok"]
    assert {(r["object"], r["file"]) for r in report["nonfinite"]} == {("Pillar", "src/zones/ruins.py"),
                                                                     ("ArchRoot", "src/zones/ruins.py")}
    assert not (ws.artifacts / "census.glb").exists(), "nothing written: never a NaN in the GLB's JSON"
    assert set(report["zones_ok"]) == {"ruins", "meadow"}

    # the repair loop: a model repair, never a harness rebuild — and the report names the culprit
    events: list[str] = []
    tasks: list[str] = []
    builds = iter([(res, GateReport(gate="lint", passed=True, findings=[]))])
    monkeypatch.setattr(R, "build_once", lambda ctx: next(builds, (res.model_copy(update={"error_message": "fixed?"}),
                                                                   GateReport(gate="lint", passed=True, findings=[]))))
    monkeypatch.setattr(R, "make_repair_task", lambda ctx, build, lint, **k: tasks.append(
        R.format_error_report(build, lint)) or SimpleNamespace(label=k["label"]))
    monkeypatch.setattr(C, "generate", lambda ws, **kw: SimpleNamespace(ok=True, files_changed=["src/zones/ruins.py"],
                                                                        usage=Usage(), notes="", transient=False))
    ctx = SimpleNamespace(spec=SimpleNamespace(budget=SimpleNamespace(max_repair_attempts=1)),
                          events=SimpleNamespace(emit=lambda name, **k: events.append(name)),
                          ws=ws, agent_id="x", agent=None, model=None, settings=None, budget=None)
    out = R.build_with_repair(ctx, round_index=2, label="r02", max_attempts=1)
    assert "build.harness_retry" not in events and "repair.attempt" in events and len(out.attempts) == 1
    assert tasks[0].startswith("BUILD FAILED — NonFiniteTransform at src/zones/ruins.py")
    assert "'Pillar'" in tasks[0] and "location=(nan" in tasks[0]
