"""D112: ``SceneBlenderRuntime.check_env`` / ``lib_interface`` — the env module alone (argon loop
iteration 5: a recovered env.py that did not parse, one that raised on an unknown keyword, one with
no lamp and no world)."""

from __future__ import annotations

import pytest

from codeverse3d.config import get_settings
from codeverse3d.contracts.common import Language
from codeverse3d.languages import get_runtime
from codeverse3d.languages.scene_blender import LIB_DIR, lib_interface
from codeverse3d.workspace import Workspace
from tests.scene_blender.fixtures import ENV

needs_blender = pytest.mark.skipif(not get_settings().resolve_blender(), reason="no Blender binary")

VARIANTS = {
    "syntax": (ENV.replace("return 0.0", "noise_ centered = 1\n    return 0.0"), "SyntaxError"),
    "raises": (ENV.replace("return {}", "mineral_surface(roughness=0.5)\n    return {}"), "NameError"),
    "unlit": (ENV.replace('    ctx.scene.world = world\n', "").replace(
        '    ctx.collection.objects.link(bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN")))\n', ""),
        "renders black"),
}


def _ws(tmp_path, env: str) -> Workspace:
    ws = Workspace(tmp_path / "run").create()
    ws.src.mkdir(parents=True, exist_ok=True)
    (ws.src / "env.py").write_text(env)
    (ws.src / "scene.py").write_text("ZONES = {}\nASSETS = {}\nHEROES = {}\nCAMERAS = []\n")
    return ws


def test_the_lib_interface_lists_signatures_as_written(tmp_path):
    ws = _ws(tmp_path, ENV)
    (ws.src / "lib").mkdir()
    for p in LIB_DIR.glob("*.py"):
        (ws.src / "lib" / p.name).write_text(p.read_text())
    rows = lib_interface(ws).splitlines()
    assert rows and all(r.startswith("lib/") for r in rows)
    assert not any(r.startswith("lib/__init__.py") for r in rows)
    assert any(r.startswith("lib/sky.py (lib.sky): ") and "(" in r for r in rows)


@needs_blender
@pytest.mark.blender
def test_a_good_env_passes_and_each_broken_one_says_why(tmp_path):
    rt = get_runtime(Language.SCENE_BLENDER)
    assert rt.check_env(_ws(tmp_path / "good", ENV)) == ""
    for name, (env, says) in VARIANTS.items():
        error = rt.check_env(_ws(tmp_path / name, env))
        assert says in error, (name, error)
    assert not (tmp_path / "good" / "run" / "src" / "_c3d_env_check.py").exists(), "the check's entry is deleted"


def test_a_raise_inside_a_lib_helper_is_quoted_at_the_env_line_that_called_it():
    """D112 review: iteration 5 blender r1_04's recovered env was reported at ``src/lib/mesh.py:69`` — a file
    the repair single-shot never sees — while its own bad call (a tuple rotation) was ``env.py:287``."""
    from codeverse3d.languages.scene_blender import env_frame

    tb = ('Traceback (most recent call last):\n'
          '  File "/h/wrappers/run_bpy_scene.py", line 199, in run\n    out = mod.build_env(ctx)\n'
          '  File "/w/run/src/env.py", line 457, in build_env\n    _build_sky_backdrop(ctx)\n'
          '  File "/w/run/src/env.py", line 287, in _build_sky_backdrop\n    sky_batch.box(\n    ~~~~~~^\n'
          '  File "/w/run/src/lib/mesh.py", line 69, in box_geometry\n    c, s = math.cos(rotation), math.sin(rotation)\n'
          'TypeError: must be real number, not tuple\n')
    assert env_frame(tb) == "src/env.py:287 `sky_batch.box(`"
    assert env_frame(tb.replace("/src/env.py", "/src/lib/env.py")) == ""   # a lib module is not the env


@needs_blender
@pytest.mark.blender
def test_check_env_leads_with_the_env_line_when_a_lib_helper_raised(tmp_path):
    env = ENV.replace("import bpy\n", "import bpy\nfrom lib.turn import spin\n", 1).replace(
        "    return {}\n", "    spin((0.0, 0.0, 1.0))\n    return {}\n")
    ws = _ws(tmp_path, env)
    (ws.src / "lib").mkdir()
    (ws.src / "lib" / "__init__.py").write_text("")
    (ws.src / "lib" / "turn.py").write_text("import math\n\n\ndef spin(rotation):\n    return math.cos(rotation)\n")
    error = get_runtime(Language.SCENE_BLENDER).check_env(ws)
    line = next(i for i, text in enumerate(env.splitlines(), 1) if "spin((0.0" in text)
    assert error.startswith(f"src/env.py:{line} `spin((0.0, 0.0, 1.0))` (raised in src/lib/turn.py:5)"), error
    assert "TypeError" in error
