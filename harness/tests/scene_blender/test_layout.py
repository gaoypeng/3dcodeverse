"""scene_blender without Blender: the runtime row, the file table, the frame, the skeleton's text
and the offline census gate (D2 / D10)."""

from __future__ import annotations

import ast
import re

import pytest

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.contracts.common import ENTRY_FILE, TRACK_LANGUAGES, Language, Track
from codeverse3d.contracts.plan import AssetPlan, ScenePlan
from codeverse3d.conventions import (
    DRAWS_WARN_SCENE,
    MAX_DRAWS_SCENE,
    MAX_TRIS_SCENE,
    glb_basis,
    to_authoring_frame,
)
from codeverse3d.languages import get_runtime
from codeverse3d.languages.base import SceneRuntime
from codeverse3d.languages.scene_blender import frame_of, render_scene_py, write_skeleton
from codeverse3d.spatial.probes import _census_findings, merge_facts
from codeverse3d.tracks.planner import plan_example


def _plan() -> ScenePlan:
    return ScenePlan.model_validate(plan_example(Track.SCENE))


def test_scene_blender_is_a_scene_language_with_a_runtime() -> None:
    assert Language.SCENE_BLENDER in TRACK_LANGUAGES[Track.SCENE]
    rt = get_runtime(Language.SCENE_BLENDER)
    assert isinstance(rt, SceneRuntime) and rt.language is Language.SCENE_BLENDER
    assert ENTRY_FILE[Language.SCENE_BLENDER] == "src/scene.py" == rt.entry_globs[0]


def test_render_scene_is_not_there_yet(tmp_ws) -> None:
    """Phase 2 (lane B) lands it; until then a clear error, never a silent empty RenderSet."""
    with pytest.raises(NotImplementedError, match="phase 2"):
        get_runtime(Language.SCENE_BLENDER).render_scene(tmp_ws, tmp_ws.artifacts / "renders")


def test_the_file_table() -> None:
    rt = get_runtime(Language.SCENE_BLENDER)
    plan = _plan()
    boat = next(a for a in plan.assets if a.name == "FishingBoat")
    assert rt.zone_file("Quay Front") == "src/zones/quay_front.py"
    assert rt.asset_file(boat.model_copy(update={"kind": "bpy"})) == "src/assets/fishing_boat.py"
    assert rt.asset_file(boat.model_copy(update={"kind": "blender_glb"})) == "public/assets/fishing_boat.glb"
    assert rt.files_for(plan, "Quay") == ["src/zones/quay.py"]
    assert rt.files_for(plan, "fog") == ["src/env.py"]
    assert rt.files_for(plan, "cameras") == ["src/scene.py"]
    assert rt.expected_files(plan) == ["src/scene.py", "src/env.py"]


def test_asset_kind_bpy_loads() -> None:
    """D5: ``AssetPlan.kind`` gains ``bpy``; the old kinds still load."""
    for kind in ("threejs", "bpy", "blender_glb"):
        assert AssetPlan(name="Crate", kind=kind, description="a crate", approx_size_m=(1, 1, 1)).kind == kind


def test_glb_basis_inverts_to_authoring_frame() -> None:
    """The census-GLB writer's basis (conventions, handed to Blender on argv) is the inverse of the
    plan → Blender conversion the skeleton and assembler print: one frame, stated once."""
    for v in ((1.0, 2.0, 3.0), (-4.5, 0.0, 7.25)):
        b = to_authoring_frame(v, "scene_blender")
        back = tuple(sum(r[i] * b[i] for i in range(3)) for r in glb_basis("scene_blender"))
        assert back == pytest.approx(v)
    assert glb_basis("scene_threejs") == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def test_the_frame_clock() -> None:
    assert (frame_of(0.0), frame_of(1.5)) == (1, 46)


def test_the_assembled_entry_is_data_in_the_blender_frame() -> None:
    plan = _plan()
    text = render_scene_py(["quay"], ["bollard"], ["windmill"], plan.cameras[:1])
    ns: dict = {}
    exec(compile(ast.parse(text), "scene.py", "exec"), ns)   # data only: nothing to call
    assert ns["ZONES"] == {"quay": "Quay"} and ns["ASSETS"] == {"bollard": "Bollard"} and ns["HEROES"] == {"windmill": "Windmill"}
    cam, src = ns["CAMERAS"][0], plan.cameras[0]
    assert cam["location"] == pytest.approx(to_authoring_frame(src.position, "scene_blender"), abs=1e-3)
    assert cam["look_at"] == pytest.approx(to_authoring_frame(src.look_at, "scene_blender"), abs=1e-3)
    assert cam["fov"] == src.fov


def test_the_skeleton_writes_the_layout(tmp_ws) -> None:
    plan = _plan()
    paths = {p.relative_to(tmp_ws.root).as_posix() for p in write_skeleton(tmp_ws, plan)}
    want = {"src/scene.py", "src/env.py"} | {f"src/zones/{z}.py" for z in ("quay", "water")}
    want |= {f"src/assets/{a}.py" for a in ("fishing_boat", "bollard")}
    assert want <= paths
    for p in paths:
        compile((tmp_ws.root / p).read_text(), p, "exec")
    # a Blender object name is global: two zones never place the same <Name>_<i>
    names = [n for z in ("quay", "water")
             for n in re.findall(r'place\(ctx, "\w+", "(\w+)"', (tmp_ws.src / "zones" / f"{z}.py").read_text())]
    assert names
    assert len(names) == len(set(names))


# --------------------------------------------------------------------------- the offline census gate
def _census(**bpy) -> dict:
    base = {"totals": {"meshes": 10, "instances": 10, "draws": MAX_DRAWS_SCENE + 1, "triangles": 10, "lights": 0},
            "groups": [], "fog": None, "background": None}
    facts = {"lights": 1, "fog": {"type": "VolumeBox"}, "background": "sky:MULTIPLE_SCATTERING", "unique_tris": 10,
             "instanced_tris": 10, "drivers_invalid": [], "handlers": [], **bpy}
    return merge_facts(base, facts)


def test_bpy_facts_replace_what_the_glb_cannot_carry() -> None:
    c = _census()
    assert c["totals"]["lights"] == 1 and c["fog"] == {"type": "VolumeBox"} and c["background"].startswith("sky:")
    assert c["bpy"]["unique_tris"] == 10 and "fog" not in c["bpy"]


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


def test_offline_lamp_check_counts_the_world() -> None:
    lit = _census_findings(_census(lights=0), offline=True)
    dark = _census_findings(_census(lights=0, background=None), offline=True)
    assert not [f for f in lit if "lamps" in f.message]
    assert [f for f in dark if "lamps" in f.message][0].target == "src/env.py"


def test_each_scene_language_plans_its_own_asset_kinds() -> None:
    """D5: ``bpy`` joins the kinds a plan may hold; the schema a planner is handed stays
    scene_threejs's (its prompts are byte-identical), a scene_blender planner swaps its own in."""
    from typing import get_args

    from codeverse3d.contracts.plan import SCENE_ASSET_KINDS

    kinds = set(get_args(AssetPlan.model_fields["kind"].annotation))
    assert {lang.value for lang in TRACK_LANGUAGES[Track.SCENE]} == set(SCENE_ASSET_KINDS)
    assert all(set(v) <= kinds for v in SCENE_ASSET_KINDS.values())
    enum = ScenePlan.model_json_schema()["$defs"]["AssetPlan"]["properties"]["kind"]["enum"]
    assert enum == list(SCENE_ASSET_KINDS["scene_threejs"])


def test_the_adapter_and_the_host_agree_on_the_census_url() -> None:
    from codeverse3d.config import get_settings

    lib = get_settings().runtime_js_dir() / "lib"
    url = re.search(r"CENSUS_GLB_URL = '([^']+)'", (lib / "glb_scene.mjs").read_text()).group(1)
    assert f"'{url}': {{ body: fs.readFileSync(glb)" in (lib / "host_env.mjs").read_text()
