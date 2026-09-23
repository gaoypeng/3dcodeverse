"""Skeleton / starter files."""

from __future__ import annotations

from codeverse3d.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZonePlan
from codeverse3d.languages.scene_threejs import lint, write_example, write_skeleton
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
def test_plan_skeleton_writes_zone_and_asset_stubs_and_lints(ws):
    plan = make_plan()
    paths = write_skeleton(ws, plan)
    rel = {p.relative_to(ws.src).as_posix() for p in paths}
    assert {"scene.js", "env.js", "zones/pier.js", "zones/fish_market.js", "assets/crate.js", "shaders/water.js"} <= rel
    assert "assets/crane.js" not in rel  # blender_glb assets are loaded, not stubbed
    scene = (ws.src / "scene.js").read_text()
    assert "export async function createScene" in scene
    assert "'/assets/crane.glb'" in scene
    assert "addZone(buildPier, 'Pier')" in scene and "addZone(buildFishMarket, 'FishMarket')" in scene
    assert "name: 'pier_low'" in scene
    pier = (ws.src / "zones" / "pier.js").read_text()
    assert "zone.name = 'Pier'" in pier and "buildCrate" in pier and "ctx.assets.crane" in pier
    crate = (ws.src / "assets" / "crate.js").read_text()
    assert "export function buildCrate" in crate
    env = (ws.src / "env.js").read_text()
    assert "GROUND_SIZE = 200" in env  # 80 m span * 2.5
    assert (ws.public / "assets").is_dir()
    rep = lint(ws)
    assert not rep.errors, [(f.target, f.message) for f in rep.errors]


# ------------------------------------------------------- the effect library (D51)


def test_both_skeleton_paths_ship_the_effect_library(tmp_path):
    """The library is only usable if it is IN the workspace, and plan mode is the
    PRODUCTION path.  It shipped `PATTERN_FILES` only, so a planned run would have
    carried the effects catalog in its prompt with no `src/lib/` to import from —
    every `from './lib/grass.js'` an agent wrote would have been a build error."""
    from codeverse3d.languages.scene_threejs import lib_files

    names = {p.name for p in lib_files()}
    assert len(names) >= 40 and "grass.js" in names and "shader.js" in names

    for label, plan in (("example", None), ("plan", make_plan())):
        ws = Workspace(tmp_path / label)
        written = write_skeleton(ws, plan)
        got = {p.name for p in written if p.parent == ws.src / "lib"}
        assert got == names, (label, sorted(names - got))
        assert (ws.src / "lib" / "grass.js").is_file(), label
        if plan is None:   # no plan: the complete example scene is the skeleton
            assert ws.src / "zones" / "meadow.js" in written and (ws.src / "scene.js").is_file()


def test_the_library_is_harness_owned_so_an_agent_cannot_rewrite_it():
    """`src/lib/` is starter code the agent CALLS.  Registering it in
    HARNESS_OWNED_SRC is what makes `_enforce_scope` revert a session's write to
    it — the same protection `src/recipes.glsl` has had since 2026-08-26, when a
    seeded file the agent COULD rewrite was measured gone by the end of the run."""
    from codeverse3d.contracts.common import HARNESS_OWNED_SRC, Language, is_harness_owned

    owned = HARNESS_OWNED_SRC[Language.SCENE_THREEJS]
    assert owned == ("src/lib/",)
    assert is_harness_owned("src/lib/grass.js", owned)
    assert is_harness_owned("src/lib/nested/x.js", owned)
    # the agent's OWN files stay writable
    for rel in ("src/scene.js", "src/env.js", "src/zones/meadow.js", "src/libx.js"):
        assert not is_harness_owned(rel, owned), rel
    # a plain entry is still an exact match, not a prefix
    glsl = HARNESS_OWNED_SRC[Language.GLSL_SHADER]
    assert is_harness_owned("src/recipes.glsl", glsl)
    assert not is_harness_owned("src/recipes.glsl.bak", glsl)


def test_the_lint_does_not_judge_the_library_as_agent_code(tmp_path):
    """The workspace gate polices what the AGENT writes.  Once the library shipped
    inside `src/`, linting it as agent code was 3 hard ERRORs (signage.js's
    node-only font fallback imports `node:module` / `node:fs/promises` / `node:url`,
    which the browser half never reaches) plus 12 "large file" WARNs telling the
    agent to split files it does not own — a red gate on every scene run."""
    from codeverse3d.contracts.artifacts import Severity

    ws = Workspace(tmp_path / "ws")
    write_example(ws)
    report = lint(ws)
    assert report.gate == "lint:scene_threejs"
    assert report.passed, [f.message for f in report.findings if f.severity == Severity.ERROR]
    assert not [f for f in report.findings if f.target.startswith("src/lib/")], \
        [f.target for f in report.findings if f.target.startswith("src/lib/")]


def test_the_effects_catalog_reaches_the_scene_prompts_and_only_those():
    """A catalog the prompt never carries teaches nothing (the same failure the
    inlined cookbook chapters exist for).  It is keyed on LANGUAGE: a blender
    asset session inside a scene run must not be told to import three.js modules."""
    from codeverse3d.contracts.common import Language
    from codeverse3d.prompts import PROMPTS_DIR
    from codeverse3d.prompts.catalog import language_text

    text = language_text(Language.SCENE_THREEJS, "effects_catalog.md")
    assert "makeGrass" in text and "makeCanopy" in text and "lib/shader.js" in text
    assert language_text(Language.BLENDER, "effects_catalog.md") == ""
    assert language_text(Language.GLSL_SHADER, "effects_catalog.md") == ""

    tpl = PROMPTS_DIR / "tracks"
    for name in ("scene_env.j2", "scene_zone.j2", "scene_refine.j2"):
        assert "effects_catalog" in (tpl / name).read_text(), name
    # the asset session authors BLENDER bpy, so it must NOT carry the table
    assert "effects_catalog" not in (tpl / "scene_asset.j2").read_text()


def test_the_catalog_names_every_library_module_and_no_other():
    """The catalog is the agent's only index of the library.  That every call
    it names is a real export is checked at runtime in lib/test_library.py."""
    import re

    from codeverse3d.languages.scene_threejs import lib_files
    from codeverse3d.prompts import PROMPTS_DIR

    catalog = (PROMPTS_DIR / "scene_threejs" / "effects_catalog.md").read_text()
    named = set(re.findall(r"`lib/([a-z_]+\.js)`", catalog))
    modules = {p.name for p in lib_files()}
    assert named == modules, (sorted(modules - named), sorted(named - modules))
