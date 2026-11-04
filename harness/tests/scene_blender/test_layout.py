"""scene_blender without Blender: the runtime row, the skeleton and the offline census gate (D2 / D10)."""

from __future__ import annotations

import re

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.conventions import (
    DRAWS_WARN_SCENE,
    MAX_DRAWS_SCENE,
    MAX_TRIS_SCENE,
)
from codeverse3d.spatial.probes import _census_findings, merge_facts
from codeverse3d.tracks.planner import plan_example


def _plan() -> ScenePlan:
    return ScenePlan.model_validate(plan_example(Track.SCENE))


# --------------------------------------------------------------------------- the offline census gate
def _census(**bpy) -> dict:
    base = {"totals": {"meshes": 10, "instances": 10, "draws": MAX_DRAWS_SCENE + 1, "triangles": 10, "lights": 0},
            "groups": [], "fog": None, "background": None}
    facts = {"lights": 1, "fog": {"type": "VolumeBox"}, "background": "sky:MULTIPLE_SCATTERING", "unique_tris": 10,
             "instanced_tris": 10, "drivers_invalid": [], "handlers": [], **bpy}
    return merge_facts(base, facts)


def test_offline_draws_are_not_gated_unique_triangles_are() -> None:
    """D2: the census GLB's draw count describes the writer, not the scene — no draw finding
    offline however high; the unique-triangle ceiling is an ERROR instead."""
    kinds = {f.data.get("kind"): f.severity for f in _census_findings(_census(), offline=True)}
    assert "draw_calls" not in kinds and "unique_tris" not in kinds
    kinds = {f.data.get("kind"): f.severity for f in _census_findings(_census(unique_tris=MAX_TRIS_SCENE + 1), offline=True)}
    assert kinds.get("unique_tris") is Severity.ERROR
    # the same census through the three.js gate still fails on draws (D99 unchanged)
    online = _census_findings({"totals": {"meshes": 1, "lights": 1, "draws": MAX_DRAWS_SCENE + 1}, "groups": []})
    assert any(f.data.get("kind") == "draw_calls" and f.severity is Severity.ERROR for f in online)
    assert DRAWS_WARN_SCENE < MAX_DRAWS_SCENE


def test_python_drivers_and_handlers_fail_the_census() -> None:
    """D10: the build's own check, behind the lint (a driver added by an expression string the
    lint cannot read still fails here)."""
    bad = {"id": "object:Mill", "path": "rotation_euler", "index": 1, "expression": "noise.noise((frame,0,0))",
           "python": True, "valid": False}
    fs = _census_findings(_census(drivers_invalid=[bad], handlers=["frame_change_pre"]), offline=True)
    kinds = [(f.data.get("kind"), f.severity) for f in fs]
    assert ("python_driver", Severity.ERROR) in kinds and ("app_handler", Severity.ERROR) in kinds


def test_an_unlit_scene_fails_the_build_in_both_languages() -> None:
    """D112 (argon loop iteration 5, scene_blender r1_10): the placeholder env — no lamp, no world —
    passed the build as a WARN and every round-0 frame was black after the full render and judge spend.
    An ERROR fails the build, so the round's repair is handed the env first.  The three.js twin: no
    light, no background and no environment map."""
    dark = [f for f in _census_findings(_census(lights=0, background=None), offline=True) if f.data.get("kind") == "unlit"]
    assert len(dark) == 1 and dark[0].severity is Severity.ERROR and dark[0].target == "src/env.py"
    three = {"totals": {"meshes": 3, "lights": 0, "draws": 3}, "groups": [], "background": None, "environment": False}
    dark = [f for f in _census_findings(three) if f.data.get("kind") == "unlit"]
    assert len(dark) == 1 and dark[0].severity is Severity.ERROR and dark[0].target == "src/env.js"
    for lit in ({"background": "#88aacc"}, {"environment": True}, {"totals": {"meshes": 3, "lights": 2, "draws": 3}}):
        assert not [f for f in _census_findings({**three, **lit}) if f.severity is Severity.ERROR], lit
    # a sky but no light keeps its WARN (MeshStandardMaterial still renders black)
    assert [f.severity for f in _census_findings({**three, "background": "#88aacc"}) if "no lights" in f.message] == [Severity.WARN]


def test_the_adapter_and_the_host_agree_on_the_census_url() -> None:
    from codeverse3d.config import get_settings

    lib = get_settings().runtime_js_dir() / "lib"
    url = re.search(r"CENSUS_GLB_URL = '([^']+)'", (lib / "glb_scene.mjs").read_text()).group(1)
    assert f"'{url}': {{ body: fs.readFileSync(glb)" in (lib / "host_env.mjs").read_text()
