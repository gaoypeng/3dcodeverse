"""The scene_blender lint table: the object track's bpy traps + the scene policy (DESIGN §8, D10)."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.languages._ast_lint import BASE_FORBIDDEN_IMPORTS
from codeverse3d.languages.scene_blender import (
    FORBIDDEN_IMPORTS,
    lint,
    lint_scene_source,
    simple_driver,
    write_skeleton,
)
from codeverse3d.tracks.planner import plan_example

HEAD = "import bpy\n\n\ndef build(ctx):\n"


def _zone(body: str) -> str:
    return HEAD + "".join(f"    {line}\n" for line in body.splitlines()) + "    return ctx.collection\n"


def _hits(source: str, *, role: str = "zone", target: str = "src/zones/yard.py") -> dict[str, Severity]:
    return {f.message: f.severity for f in lint_scene_source(source, target=target, role=role)}


def _worst(source: str, **kw) -> Severity | None:
    sev = set(_hits(source, **kw).values())
    for s in (Severity.ERROR, Severity.WARN, Severity.INFO):
        if s in sev:
            return s
    return None


# (body inside a zone's build(ctx), the worst severity the lint must give it)
TABLE = [
    # D10: a Python driver never evaluates under --factory-startup (it reads 0) -> ERROR
    ('d = ob.driver_add("location", 2).driver\nd.expression = "noise.noise((frame, 0, 0))"', Severity.ERROR),
    ('d = ob.driver_add("location", 2).driver\nd.expression = "bpy.data.objects[0].location.x"', Severity.ERROR),
    ('d = ob.driver_add("location", 2).driver\nd.expression = "frame ** 2"', Severity.ERROR),
    ('d = ob.driver_add("location", 2).driver\nd.use_self = True', Severity.ERROR),
    ('d = ob.driver_add("location", 2).driver\nd.expression = "sin(frame * 0.2) * 2 if frame > 3 else 0"', None),
    ("bpy.app.handlers.frame_change_pre.append(lambda s: None)", Severity.ERROR),
    # harness-owned: rendering, files, cameras, frames, scene render settings
    ("bpy.ops.render.render(write_still=True)", Severity.ERROR),
    ('bpy.ops.wm.save_as_mainfile(filepath="x.blend")', Severity.ERROR),
    ('bpy.ops.import_scene.gltf(filepath="x.glb")', Severity.ERROR),
    ('cam = bpy.data.cameras.new("C")', Severity.ERROR),
    ('ctx.scene.render.engine = "CYCLES"', Severity.ERROR),
    ("ctx.scene.cycles.samples = 64", Severity.ERROR),
    ('ctx.scene.view_settings.view_transform = "AgX"', Severity.ERROR),
    ("ctx.scene.frame_end = 100", Severity.ERROR),
    ("ctx.scene.frame_set(10)", Severity.ERROR),
    ("ctx.scene.camera = None", Severity.ERROR),
    ('open("x.txt", "w")', Severity.ERROR),
    # the world is env.py's
    ('ctx.scene.world = bpy.data.worlds.new("W")', Severity.ERROR),
    # renamed / removed API
    ('sky.sky_type = "NISHITA"', Severity.ERROR),
    ('bsdf.inputs["Specular"].default_value = 0.5', Severity.ERROR),
    ("me.calc_normals()", Severity.ERROR),
    # determinism: ctx.rng(seed) is the only randomness
    ("import random\nx = random.uniform(0, 1)", Severity.ERROR),
    ("import random\nr = random.Random()", Severity.ERROR),
    ("import numpy as np\nx = np.random.rand(3)", Severity.ERROR),
    ("rng = ctx.rng(7)\nx = rng.uniform(0, 1)", None),
    ("import random\nr = random.Random(3)\nx = r.uniform(0, 1)", None),
    ("import numpy as np\ng = np.random.default_rng(3)", None),
    # build time and zone ownership
    ("for i in range(50):\n    bpy.ops.mesh.primitive_cube_add(size=1, location=(i, 0, 0))", Severity.WARN),
    ("ctx.scene.collection.objects.link(ob)", Severity.WARN),
    # the agent's own settings on its own data are NOT harness-owned
    ("mod.render_levels = 2", None),
    ('mat.cycles.displacement_method = "BOTH"', None),
    ("ctx.collection.objects.link(ob)", None),
    ('ob["placement"] = "free"', None),
]


@pytest.mark.parametrize(("body", "worst"), TABLE, ids=[t[0].splitlines()[-1][:48] for t in TABLE])
def test_the_scene_lint_table(body: str, worst: Severity | None) -> None:
    assert _worst(_zone(body)) is worst, _hits(_zone(body))


@pytest.mark.parametrize("mod", ["os", "sys", "time", "datetime", "pathlib", "subprocess", "socket"])
def test_clock_file_and_process_imports_are_forbidden(mod: str) -> None:
    assert _worst(f"import {mod}\n" + _zone("pass")) is Severity.ERROR
    assert BASE_FORBIDDEN_IMPORTS <= FORBIDDEN_IMPORTS


def test_the_world_and_lamps_belong_to_env() -> None:
    env = ("import bpy\n\n\ndef height_at(x, y):\n    return 0.0\n\n\ndef build_env(ctx):\n"
           '    ctx.scene.world = bpy.data.worlds.new("Sky")\n'
           '    ctx.collection.objects.link(bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN")))\n'
           "    return {}\n")
    assert _worst(env, role="env", target="src/env.py") is None
    assert _worst(env.replace("def build_env", "def make_env"), role="env", target="src/env.py") is Severity.ERROR


def test_module_exports_are_the_layout() -> None:
    assert _worst("import bpy\n\n\ndef make(ctx):\n    return ctx.collection\n") is Severity.ERROR   # zone: build
    asset = "import bpy\n\n\ndef build_crate(ctx, variant=0):\n    return ctx.collection\n"
    assert _worst(asset, role="asset", target="src/assets/crate.py") is None
    assert _worst(asset, role="asset", target="src/assets/barrel.py") is Severity.ERROR


def test_simple_driver_is_blenders_subset() -> None:
    """Verified against Blender 5.0.1 ``driver.is_simple_expression`` (DESIGN probes/drv.py)."""
    for ok in ("sin(frame*0.2)*2", "frame*2 if frame > 3 else 1", "clamp(frame / 40, 0, 1)", "lerp(0, 2, 0.5)", "pi", "not frame"):
        assert simple_driver(ok), ok
    for bad in ("frame ** 2", "frame % 3", "noise.noise((frame, 0, 0))", "hypot(frame, 1)", "'a'", "[frame]", "sin(x=1)"):
        assert not simple_driver(bad), bad


def test_the_library_is_not_the_agents_code(tmp_ws) -> None:
    """``src/lib/`` is harness-owned (D51): syntax only, never the policy."""
    (tmp_ws.src / "lib").mkdir(parents=True)
    (tmp_ws.src / "lib" / "sky.py").write_text("import os\nX = os.getcwd()\n")
    (tmp_ws.src / "scene.py").write_text("ZONES = {}\nASSETS = {}\nHEROES = {}\nCAMERAS = []\n")
    assert lint(tmp_ws).passed
    (tmp_ws.src / "lib" / "sky.py").write_text("def broken(:\n")
    assert not lint(tmp_ws).passed


def test_the_skeleton_lints_clean(tmp_ws) -> None:
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    write_skeleton(tmp_ws, plan)
    rep = lint(tmp_ws)
    assert rep.passed and not [f for f in rep.findings if f.severity is not Severity.INFO], rep.findings


def test_a_missing_entry_is_a_lint_error(tmp_ws) -> None:
    rep = lint(tmp_ws)
    assert not rep.passed and rep.errors[0].target == "src/scene.py"
