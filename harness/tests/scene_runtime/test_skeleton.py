"""Skeleton / starter files."""

from __future__ import annotations

from codeverse3d.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZonePlan
from codeverse3d.languages.scene_threejs import lint, write_skeleton
from codeverse3d.workspace import Workspace
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


@needs_node
def test_plan_skeleton_writes_interface_stubs_and_lints(ws):
    """The skeleton is the interface plus the plan's data: no landform, no positions, no example files (owner, 2026-10-15)."""
    plan = make_plan()
    paths = write_skeleton(ws, plan)
    rel = {p.relative_to(ws.src).as_posix() for p in paths}
    assert {"scene.js", "env.js", "zones/pier.js", "zones/fish_market.js", "assets/crate.js", "lib/grass.js"} <= rel
    assert "assets/crane.js" not in rel  # blender_glb assets are loaded, not stubbed
    assert not {r for r in rel if r.startswith("shaders/") or r in ("assets/pine_tree.js", "assets/windmill.js")}, rel
    scene = (ws.src / "scene.js").read_text()
    assert "export async function createScene" in scene
    assert "'/assets/crane.glb'" in scene
    assert "addZone(buildPier, 'Pier')" in scene and "addZone(buildFishMarket, 'FishMarket')" in scene
    assert "name: 'pier_low'" in scene
    pier = (ws.src / "zones" / "pier.js").read_text()
    assert "zone.name = 'Pier'" in pier and "import { buildCrate } from '../assets/crate.js';" in pier and "ctx.assets.crane" in pier
    # a TODO naming the plan's contents, no positions, no bbox wall
    assert "- Crate:" in pier and "place(" not in pier and "position.set" not in pier
    crate = (ws.src / "assets" / "crate.js").read_text()
    assert "export function buildCrate" in crate and "PLACEHOLDER" in crate and "pine_tree" not in crate and "windmill" not in crate
    env = (ws.src / "env.js").read_text()
    assert "GROUND_SIZE = 240" in env and "SHADOW_RADIUS" not in env   # 80 m span × 3; no light rig to size a frustum for
    code = "\n".join(ln for ln in env.splitlines() if not ln.lstrip().startswith("//"))   # the comments may NAME the lib's options
    for landform in ("CONTENT_RADIUS", "worldShell(", "makeOutskirts(", "roomShell(", "FLAT_RADIUS", "flat:",
                     "sunRig(", "MOOD", "scene.fog =", "scene.environment ="):   # no light, sky or fog decided either
        assert landform not in code, landform
    assert "export function heightAt" in env and "export function buildEnv" in env and "export const SUN_AZIMUTH_DEG" in env
    assert "ENV PLAN: calm sea" in env
    assert "export const INTERIOR = null;" in env
    assert (ws.public / "assets").is_dir()
    rep = lint(ws)
    assert not rep.errors, [(f.target, f.message) for f in rep.errors]
    # an interior plan hands the room box over as DATA; the enclosure is the env author's
    inside = Workspace(ws.root.parent / "inside")
    write_skeleton(inside, plan.model_copy(update={"interior": True, "glazed": True}))
    env = (inside.src / "env.js").read_text()
    assert "export const INTERIOR = { center: [0.0, 5.0, 0.0], extents: [80.0, 20.0, 60.0], openings: [], glazed: true };" in env
    code = "\n".join(ln for ln in env.splitlines() if not ln.lstrip().startswith("//"))
    assert "roomShell(" not in code   # not instantiated: one optional way, named in the comment


# ------------------------------------------------------- the effect library (D51)


def test_both_skeleton_paths_ship_the_effect_library(tmp_path):
    """The library is only usable if it is IN the workspace, and plan mode is the PRODUCTION path."""
    from codeverse3d.languages.scene_threejs import lib_files

    names = {p.name for p in lib_files()}
    assert len(names) >= 12 and "grass.js" in names and "shader.js" in names

    for label, plan in (("example", None), ("plan", make_plan())):
        ws = Workspace(tmp_path / label)
        written = write_skeleton(ws, plan)
        got = {p.name for p in written if p.parent == ws.src / "lib"}
        assert got == names, (label, sorted(names - got))
        assert (ws.src / "lib" / "grass.js").is_file(), label
        if plan is None:   # no plan: the complete example scene is the skeleton
            assert ws.src / "zones" / "meadow.js" in written and (ws.src / "scene.js").is_file()


def test_the_catalog_names_every_library_module_and_no_other():
    """The catalog is the agent's only index of the library."""
    import re

    from codeverse3d.languages.scene_threejs import lib_files
    from codeverse3d.prompts import PROMPTS_DIR

    catalog = (PROMPTS_DIR / "scene_threejs" / "effects_catalog.md").read_text()
    named = set(re.findall(r"`lib/([a-z_]+\.js)`", catalog))
    modules = {p.name for p in lib_files()}
    assert named == modules, (sorted(modules - named), sorted(named - modules))
