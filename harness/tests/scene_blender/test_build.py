"""scene_blender build end to end: Blender builds the workspace, the harness writes the census GLB,
the unchanged JS census / placement code measures it (real Blender + headless Chrome)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.config import get_settings
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.conventions import to_snake
from codeverse3d.languages import get_runtime
from codeverse3d.languages.scene_blender import SceneBlenderRuntime
from codeverse3d.tracks.planner import plan_example
from tests.scene_blender.fixtures import BROKEN_LINE, write_fixture
from tests.scene_runtime.conftest import needs_browser
from tests.scene_runtime.glb_fixture import write_box_glb

needs_blender = pytest.mark.skipif(not get_settings().resolve_blender(), reason="no Blender binary")
pytestmark = [pytest.mark.blender, pytest.mark.node, needs_blender, needs_browser]


def _rt() -> SceneBlenderRuntime:
    return get_runtime(Language.SCENE_BLENDER)  # type: ignore[return-value]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One build of the fixture (every case in one Blender process, one probe)."""
    from codeverse3d.workspace import Workspace

    ws = write_fixture(Workspace(tmp_path_factory.mktemp("sb") / "run").create())
    return ws, _rt().build(ws)


def _rows(res) -> dict[str, dict]:
    return {r["name"]: r for r in res.census["placement"]["assets"]}


def test_triangle_parity_bpy_vs_the_census_glb(built) -> None:
    """Every evaluated triangle Blender renders reaches the JS census through the GLB — collection
    instances as clones, the geometry-nodes grass as an instance cloud (the official exporter drops
    those silently, DESIGN §1.4)."""
    _, res = built
    bpy, totals = res.census["bpy"], res.census["totals"]
    assert bpy["instanced_tris"] == totals["triangles"] > 0
    assert bpy["glb"]["clouds"] == 1 and bpy["glb"]["instances"] > 100
    assert totals["instances"] >= bpy["glb"]["instances"]
    assert bpy["unique_tris"] < bpy["instanced_tris"]   # an instance costs a matrix, not a mesh


def test_a_zone_that_raises_is_dropped_with_its_file_and_line(built) -> None:
    ws, res = built
    assert not res.ok
    assert (res.error_file, res.error_line, res.error_type) == ("src/zones/broken.py", BROKEN_LINE, "zone_error")
    groups = {g["name"] for g in res.census["groups"]}
    assert {"Yard", "Meadow"} <= groups and "Broken" not in groups   # the rest still built and was measured
    report = json.loads((ws.artifacts / "bpy_build.json").read_text())
    assert set(report["zones_failed"]) == {"broken"} and report["zones_ok"] == ["meadow", "yard"]
    # what it made before raising is gone with it — even the object it linked outside its zone
    assert not [r for r in res.census["placement"]["assets"] if r["name"].startswith(("Cube", "Stray"))]


def test_placement_rows_for_sunk_floating_and_free(built) -> None:
    _, res = built
    rows = _rows(res)
    assert rows["Crate_0"]["supported"] and not rows["Crate_0"]["floating"]
    assert rows["Crate_1"]["floating"] and rows["Crate_1"]["ground_gap_m"] == pytest.approx(1.5, abs=0.02)
    assert rows["Crate_2"]["sunk_m"] == pytest.approx(0.6, abs=0.02)
    assert rows["Crate_3"]["exempt"] == "free"          # obj["placement"] = "free" → glTF extras → userData
    assert rows["Cart_0"]["zone"] == "Yard"             # a parent mesh + child: ONE row
    assert "CartWheel_0" not in rows
    assert [r["name"] for r in rows.values() if r["name"].startswith("GrassPatch")] == ["GrassPatch"]


def _only_unlit(res) -> None:
    """The skeleton's build: everything built, and the one ERROR is the placeholder env's ``unlit``."""
    errors = [f for g in res.gates for f in g.errors]
    assert not res.ok and [(f.data.get("kind"), f.target) for f in errors] == [("unlit", "src/env.py")], res.stdout_tail
    assert res.error_file == "src/env.py"


def test_the_skeleton_builds_and_passes(tmp_ws) -> None:
    """plan → skeleton → assemble → build: the starter scene fails ONE gate — the placeholder env lights
    nothing (`unlit`, an ERROR since D112, so round 0's repair is handed the env) — and passes the rest;
    the plan's cameras reach the host."""
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    rt = _rt()
    rt.skeleton(tmp_ws, plan)
    assert rt.lint(tmp_ws).passed
    asm = rt.assemble(tmp_ws, plan)
    assert set(asm.zones_included) == {"quay", "water"} and not asm.zones_failed
    res = rt.build(tmp_ws)
    _only_unlit(res)
    c = res.census
    assert c["fog"] is None and c["background"] is None and c["light_types"] == {}
    assert {g["name"] for g in c["groups"]} >= {"Quay", "Water", "Ground"}
    placed = [r for r in c["placement"]["assets"] if r["zone"] in ("Quay", "Water") and not r["exempt"]]
    assert placed == [], placed   # D106: the zone stubs place nothing
    probe = json.loads((tmp_ws.artifacts / "scene_probe.json").read_text())
    # the plan's cameras, named as scene_threejs's assembler names them
    assert [cam["name"] for cam in probe["boot"]["cameras"]] == [to_snake(c.name) for c in plan.cameras[:6]]


def test_the_skeleton_renders_through_the_runtime(tmp_ws, switch) -> None:
    """plan → skeleton → build → ``runtime.render_scene``: Blender pictures for every plan camera at
    both times, measured by the JS host on the census GLB (lanes A + B joined, DESIGN §2)."""
    switch("C3D_RENDER__BLENDER_DEVICE", "cpu")
    switch("C3D_RENDER__BLENDER_SAMPLES", "4")
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    rt = _rt()
    rt.skeleton(tmp_ws, plan)
    rt.assemble(tmp_ws, plan)
    _only_unlit(rt.build(tmp_ws))   # the placeholder env is unlit (D112); the scene still built and renders
    rs = rt.render_scene(tmp_ws, tmp_ws.root / "renders", times=(0.0, 1.5), width=96, height=54, sheet=False)
    names = [to_snake(c.name) for c in plan.cameras[:6]]
    authored = {(v.name, v.time_s) for v in rs.views if v.name in names}
    assert authored == {(n, t) for n in names for t in (0.0, 1.5)}, rs.views
    assert all((tmp_ws.root / "renders" / Path(v.path).name).is_file() or Path(v.path).is_file() for v in rs.views)
    metrics = json.loads((tmp_ws.root / "renders" / "metrics.json").read_text())
    assert metrics["language"] == "scene_blender" and {c["name"] for c in metrics["camera_checks"]} >= set(names)


def test_a_hero_glb_is_imported_into_ctx_assets(tmp_ws) -> None:
    write_fixture(tmp_ws, broken=False)
    write_box_glb(tmp_ws.public / "assets" / "windmill.glb", size=(2.0, 4.0, 2.0))
    scene = tmp_ws.src / "scene.py"
    scene.write_text(scene.read_text().replace("HEROES = {}", 'HEROES = {"windmill": "Windmill"}'))
    yard = tmp_ws.src / "zones" / "yard.py"
    yard.write_text(yard.read_text().replace(
        "    return ctx.collection\n",
        "    mill = bpy.data.objects.new('Windmill_0', None)\n"
        "    mill.instance_type = 'COLLECTION'\n"
        "    mill.instance_collection = ctx.assets['windmill']\n"
        "    mill.location = (-10.0, 6.0, 2.0)\n"
        "    ctx.collection.objects.link(mill)\n"
        "    return ctx.collection\n"))
    res = _rt().build(tmp_ws)
    assert res.ok, res.stdout_tail
    row = _rows(res)["Windmill_0"]
    assert row["zone"] == "Yard" and row["supported"]   # a 4 m box centred on z = 2 stands on the ground



def test_the_example_scene_builds_and_seats_every_root(tmp_ws) -> None:
    """The plan-less starter scene (``write_example``: env, two assets, three zones in plain bpy) lints
    clean, builds through the wrapper, seats every placed root (no floating, no burial past the 10 cm
    WARN, nothing interpenetrating), keeps every driver simple and leaves the render settings alone."""
    from codeverse3d.languages.scene_blender import write_example

    rt = _rt()
    write_example(tmp_ws)
    assert rt.lint(tmp_ws).passed, [(f.target, f.message) for f in rt.lint(tmp_ws).findings]
    res = rt.build(tmp_ws)
    assert res.ok, res.stdout_tail
    report = json.loads((tmp_ws.artifacts / "bpy_build.json").read_text())
    assert report["errors"] == [] and sorted(report["zones_ok"]) == ["hamlet", "lakeside", "orchard"], report
    assert res.census["bpy"]["drivers_invalid"] == [] and res.census["bpy"]["handlers"] == []
    rows = _rows(res)
    assert {"House_0", "Signpost_0", "LanternPost_2", "Crate_2", "Rock_9", "AppleTree_11", "HarvestCrate_2"} <= set(rows), sorted(rows)
    for r in rows.values():
        if not r["exempt"]:
            assert r["supported"] and not r["floating"] and r["sunk_m"] <= 0.10, r
    assert res.census["placement"]["interpenetrations"] == []
    assert res.census["fog"] is not None and res.census["light_types"]
