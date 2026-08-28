"""Skeleton / starter files."""

from __future__ import annotations

from codeverse.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZonePlan
from codeverse.languages.scene_threejs import STARTER_DIR, lint, write_example, write_skeleton
from tests.scene_runtime.conftest import needs_node


def make_plan() -> ScenePlan:
    return ScenePlan(
        title="Harbour at dusk", summary="a small fishing harbour", setting="coast, dusk, clear",
        bounds=BBox(center=(0, 5, 0), extents=(80, 20, 60)),
        environment="calm sea, low sun, light haze",
        zones=[
            ZonePlan(name="Pier", description="wooden pier with crates", bbox=BBox(center=(0, 1, 10), extents=(20, 4, 8)), contents=["Crate", "Crane"]),
            ZonePlan(name="Fish Market", description="stalls", bbox=BBox(center=(-20, 1, -5), extents=(15, 4, 15)), contents=["Crate"]),
        ],
        assets=[
            AssetPlan(name="Crate", kind="threejs", description="wooden crate", approx_size_m=(1, 1, 1), instances_hint=12),
            AssetPlan(name="Crane", kind="blender_glb", description="dockside crane", approx_size_m=(4, 9, 4)),
        ],
        cameras=[CameraPlan(name="pier_low", position=(8, 2, 22), look_at=(0, 1, 10), fov=45, purpose="pier")],
    )


def test_example_files_exist_and_are_complete():
    names = {p.relative_to(STARTER_DIR).as_posix() for p in STARTER_DIR.rglob("*.js")}
    assert {"scene.js", "env.js", "zones/meadow.js", "zones/pondside.js", "assets/pine_tree.js", "assets/windmill.js", "shaders/water.js", "shaders/sky.js"} <= names
    scene = (STARTER_DIR / "scene.js").read_text()
    assert "export function createScene" in scene and "cameras" in scene and "update(t, dt)" in scene
    assert "ShaderMaterial" in (STARTER_DIR / "shaders" / "water.js").read_text()
    assert "InstancedMesh" in (STARTER_DIR / "zones" / "meadow.js").read_text()


def test_write_example_copies_all(ws):
    paths = write_example(ws)
    assert len(paths) >= 8
    assert (ws.src / "scene.js").is_file()


@needs_node
def test_plan_skeleton_writes_zone_and_asset_stubs_and_lints(ws):
    plan = make_plan()
    paths = write_skeleton(ws, plan)
    rel = {p.relative_to(ws.src).as_posix() for p in paths}
    assert {"scene.js", "env.js", "zones/pier.js", "zones/fish_market.js", "assets/crate.js", "shaders/water.js"} <= rel
    assert "assets/crane.js" not in rel  # blender_glb assets are loaded, not stubbed
    scene = (ws.src / "scene.js").read_text()
    assert "export async function createScene" in scene
    assert "'/assets/crane.glb'" in scene
    assert "buildPier(ctx)" in scene and "buildFishMarket(ctx)" in scene
    assert "name: 'pier_low'" in scene
    pier = (ws.src / "zones" / "pier.js").read_text()
    assert "zone.name = 'Pier'" in pier and "buildCrate" in pier and "ctx.assets.crane" in pier
    crate = (ws.src / "assets" / "crate.js").read_text()
    assert "export function buildCrate" in crate
    env = (ws.src / "env.js").read_text()
    assert "GROUND_SIZE = 200" in env  # 80 m span * 2.5
    assert (ws.public / "assets").is_dir()
    rep = lint(ws)
    assert rep.passed, [(f.target, f.message) for f in rep.errors]


def test_non_scene_plan_falls_back_to_example(ws):
    paths = write_skeleton(ws, None)
    assert (ws.src / "zones" / "meadow.js") in paths
