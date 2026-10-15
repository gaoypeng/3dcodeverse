"""scene_blender's prompt corpus and the track plumbing behind it (offline): the files a bpy session
reads, the chapters the stages inline, the bpy templates over a bpy plan (Z-up numbers, D12), the
planner's template / example / schema per language, the asset stage's ``bpy`` dispatch and check,
the per-language asset API / shims / catalog rows, the env-file lookup, the stage transport retry and
the two manifest scenarios (unblessed here; the integrator blesses)."""

from __future__ import annotations

import ast
import json
from types import SimpleNamespace

import pytest

from codeverse3d.contracts.agent import AgentResult
from codeverse3d.contracts.common import Language, Track, Usage
from codeverse3d.contracts.plan import ScenePlan, ZoneLayout
from codeverse3d.languages.scene_blender import LIB_DIR
from codeverse3d.proc import EventLog
from codeverse3d.prompts import PROMPTS_DIR, render
from codeverse3d.prompts.catalog import language_text
from codeverse3d.prompts.sections import find_section, split_sections
from codeverse3d.skills import skills_dir
from codeverse3d.tracks import scene_assets as SA
from codeverse3d.tracks.planner import (
    build_system_prompt,
    plan,
    plan_example,
    plan_schema,
    plan_template,
)
from codeverse3d.tracks.prompting import cookbook_sections, language_system_prompt
from codeverse3d.tracks.scene import (
    ENV_FILES,
    ENV_RECIPES,
    SCENE_TEMPLATES,
    ZONE_RECIPES,
    SceneTrack,
    _guess_target,
    env_file,
    plan_bbox_line,
    refine_recipes,
    scene_templates,
)
from codeverse3d.tracks.scene_assets import (
    AssetCheck,
    asset_api_summary,
    library_rows,
    run_asset_stage,
    write_variant_shims,
)
from codeverse3d.tracks.zone_layout import layout_block
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
    small_scene,
)
from tests.orchestrator_tracks.test_prompts_assets import _ctx

BPY = Language.SCENE_BLENDER


@pytest.fixture
def settings(tmp_path):
    from codeverse3d.config import Settings

    return Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")


# --------------------------------------------------------------------------- the files
def test_the_language_ships_its_four_prompt_files_and_the_system_prompt_knows_about_tools() -> None:
    for name in ("system.md", "contract.md", "cookbook.md", "asset.md"):
        assert language_text(BPY, name).strip(), name
    with_tools, without = language_system_prompt(BPY, tools=True), language_system_prompt(BPY, tools=False)
    assert "check_placement" in with_tools and "single-shot" not in with_tools
    assert "single-shot" in without and "`build` →" not in without
    assert "Blender 5.2" in with_tools
    for text in (language_text(BPY, "contract.md"), language_text(BPY, "cookbook.md")):
        assert "use_nodes" in text and "MULTIPLE_SCATTERING" in text   # Blender 5.2 facts (D104), not 4.x ones
        assert "Action.fcurves" in text and "mod.properties.inputs" in text


def test_the_cookbook_has_every_chapter_the_stages_inline() -> None:
    """``cookbook_sections`` finds a chapter by title (exact, then substring): every ENV / ZONE recipe
    must resolve to ITS chapter, not to the word-overlap fallback."""
    md = language_text(BPY, "cookbook.md")
    sections = split_sections(md)
    for title in (*ENV_RECIPES, *ZONE_RECIPES):
        found = find_section(sections, title)
        assert found is not None and title.lower() in found.title.lower(), (title, found and found.title)
    ctx = SimpleNamespace(cookbook_text=md)
    env_text, zone_text = cookbook_sections(ctx, ENV_RECIPES), cookbook_sections(ctx, ZONE_RECIPES)
    assert "# role: env" in env_text and "MULTIPLE_SCATTERING" in env_text and "TIME_OF_DAY" in env_text
    for token in ("tree_mesh", "clustered_points", "camera_mask", "keyframe_linear", "along_seam", "rock_geometry"):
        assert token in zone_text, token
    assert len(zone_text) > 12_000 and "# role: env" not in zone_text


def _clear_of_loops(text: str) -> list[ast.For]:
    """Every ``for … in clear_of(…)`` header ``text`` shows (fenced or not), parsed as the code a model copies."""
    loops: list[ast.For] = []
    for line in text.splitlines():
        head = line.strip()
        if head.startswith("for ") and "clear_of(" in head:
            try:
                node = ast.parse(head + "\n    pass").body[0]
            except SyntaxError:
                pytest.fail(f"{head!r} is not a python loop header")
            assert isinstance(node, ast.For), head
            loops.append(node)
    return loops


def test_every_clear_of_loop_a_model_is_shown_unpacks_the_libs_three_names() -> None:
    """``lib.place.clear_of`` returns ``(name_a, name_b, fraction)`` rows — NAMES, not objects: a
    ``for a, b in clear_of(…)`` copied from a prompt raises on the first overlap it was meant to
    report and the wrapper drops the zone.  Every loop the prompt files, the bpy templates and the
    placement skill show unpacks three names, and the contract's example states the library's own
    overlap threshold (read off ``lib/place.py``, not restated)."""
    sources = (sorted((PROMPTS_DIR / "scene_blender").glob("*.md")) + sorted((PROMPTS_DIR / "tracks").glob("*_bpy.j2"))
               + sorted((skills_dir() / "c3d-bpy-scene-placement").rglob("*.md")))
    loops = {p.name: _clear_of_loops(p.read_text(encoding="utf-8")) for p in sources}
    assert loops["contract.md"] and loops["finding-to-fix.md"], loops   # the two worked examples are there to be checked
    for name, found in loops.items():
        for node in found:
            assert isinstance(node.target, ast.Tuple) and len(node.target.elts) == 3, \
                f"{name}: {ast.unparse(node).splitlines()[0]} must unpack (name_a, name_b, fraction)"
    place = ast.parse((LIB_DIR / "place.py").read_text(encoding="utf-8"))
    fn = next(n for n in place.body if isinstance(n, ast.FunctionDef) and n.name == "clear_of")
    overlap = dict(zip([a.arg for a in fn.args.kwonlyargs], fn.args.kw_defaults, strict=True))["overlap"]
    assert isinstance(overlap, ast.Constant)
    assert f"more than {round(overlap.value * 100)} % of the smaller box" in language_text(BPY, "contract.md")


# --------------------------------------------------------------------------- the templates over a bpy plan
def _bpy_ctx(tmp_ws, settings, *, agent=None, agent_id="fake:x", services=None):
    plan_obj = small_scene(BPY)
    spec = make_spec(Track.SCENE, BPY)
    ctx = _ctx(tmp_ws, settings, spec, plan_obj, agent_id=agent_id, services=services, agent=agent)
    ctx.cookbook_text = language_text(BPY, "cookbook.md")
    ctx.extra["asset_api"] = asset_api_summary(plan_obj, {}, language=BPY)
    return ctx, plan_obj


def test_the_bpy_templates_render_with_the_plan_in_the_z_up_frame(tmp_ws, settings) -> None:
    """The plan stays Y-up (D12); every number the bpy author reads is converted: the harbour's
    bounds centre (0, 5, 0) prints as (0, 0, 5), the Quay zone (-20, 1.2, 0) / 40 × 3 × 60 as
    (-20, 0, 1.2) / 40 × 60 × 3, a camera at (45, 18, 55) as (45, -55, 18)."""
    ctx, plan_obj = _bpy_ctx(tmp_ws, settings)
    st = SceneTrack(services=ctx.services)
    t = scene_templates(BPY)
    quay = plan_obj.zones[0]
    env = render(t.env, **st._ctx(ctx, recipes=cookbook_sections(ctx, ENV_RECIPES)))
    assert "write `src/env.py`" in env and "centre (0.000, 0.000, 5.000) m" in env and "build_env(ctx)" in env
    assert "# role: env" in env   # the recipes travel inside the brief
    zone = render(t.zone, **st._ctx(ctx, recipes="", zone_name=quay.name, zone_description=quay.description,
                                    zone_bbox=plan_bbox_line(quay.bbox, BPY), zone_contents=quay.contents, zone_file="src/zones/quay.py",
                                    neighbours=[], layout=layout_block({"zone": "Quay", "placements": [
                                        {"asset": "Bollard", "count": 2, "cluster": [3.0, -4.0], "spread_m": 0.5}]}, language=BPY)))
    assert "centre (-20.000, 0.000, 1.200) m, extents (40.000, 60.000, 3.000) m" in zone
    assert "position (45.0, -55.0, 18.0)" in zone and "2x Bollard around (3.0, 4.0)" in zone
    assert "ctx.assets['bollard']" in zone and "def build(ctx)" in zone and "import {" not in zone
    refine = render(t.refine, **st._ctx(ctx, recipes=refine_recipes(ctx, ["src/env.py"]), round_index=1, tasks=["t"], targets=["Quay"],
                                        files=["src/env.py"], edit_only_these=False, judge_summary="j", current_files={}))
    assert "src/env.py" in refine and "src/scene.py" in refine and "Horizon: the world must not end" in refine
    # the three.js templates still print the plan's own numbers (the identity conversion): byte-identical prompts
    assert "centre (0.000, 5.000, 0.000) m" in plan_bbox_line(plan_obj.bounds, Language.SCENE_THREEJS)
    assert SCENE_TEMPLATES[Language.SCENE_THREEJS].env == "tracks/scene_env.j2"


def test_layout_block_prints_ground_points_in_the_authoring_frame() -> None:
    lay = ZoneLayout(zone="Quay", placements=[{"asset": "Bollard", "count": 3, "cluster": (3.0, -4.0), "spread_m": 1.0},
                                              {"asset": "Crate", "count": 2, "cluster": (3.0, 0.0), "spread_m": 1.0}],
                     path_points=[(1.0, 2.0), (-5.0, 0.5), (2.0, 0.0)])
    js, py = layout_block(lay), layout_block(lay, language="scene_blender")
    assert "around (3.0, -4.0)" in js and "around (3.0, 0.0)" in js and "(1.0, 2.0) -> (-5.0, 0.5) -> (2.0, 0.0)" in js
    assert "around (3.0, 4.0)" in py and "around (3.0, 0.0)" in py and "(1.0, -2.0) -> (-5.0, -0.5) -> (2.0, 0.0)" in py
    assert "-0.0" not in py   # z = 0 is the director's default cluster line: the negated zero is folded, never printed
    assert layout_block(lay, language=Language.SCENE_THREEJS) == js


# --------------------------------------------------------------------------- the planner
def test_the_planner_picks_the_bpy_template_example_and_schema_per_language(tmp_ws) -> None:
    assert plan_template(Track.SCENE, BPY) == "tracks/plan_scene_bpy.j2"
    assert plan_template(Track.SCENE, Language.SCENE_THREEJS) == plan_template(Track.SCENE) == "tracks/plan_scene.j2"
    js, py = plan_example(Track.SCENE), plan_example(Track.SCENE, BPY)
    assert {a["kind"] for a in py["assets"]} == {"bpy", "blender_glb"}
    assert py["bounds"] == js["bounds"] and py["zones"] == js["zones"] and py["cameras"] == js["cameras"]   # the same Y-up numbers
    spec = make_spec(Track.SCENE, BPY)
    system = build_system_prompt(spec, ScenePlan)
    assert "SHIPPED BPY LIBRARY" in system and '"kind": "bpy"' in system and "Y-up GLB frame" in system
    schema = plan_schema(spec, ScenePlan)
    assert schema["$defs"]["AssetPlan"]["properties"]["kind"]["enum"] == ["bpy", "blender_glb"]
    # ...and the planner CALL carries that schema (a scene_threejs call carries the model's own)
    model = FakeChatModel(lambda req: py)
    out = plan(spec, "fake:planner", ScenePlan, tmp_ws, model=model)
    assert {a.kind for a in out.assets} == {"bpy", "blender_glb"}
    assert model.requests[-1].response_schema["$defs"]["AssetPlan"]["properties"]["kind"]["enum"] == ["bpy", "blender_glb"]
    model_js = FakeChatModel(lambda req: js)
    plan(make_spec(Track.SCENE, Language.SCENE_THREEJS), "fake:planner", ScenePlan, tmp_ws, model=model_js)
    assert model_js.requests[-1].response_schema == ScenePlan.model_json_schema()


# --------------------------------------------------------------------------- the asset stage
def _bpy_module(job, ws):
    rel = job.files_hint[0]
    return {rel: f"import bpy  # {job.label}\n\n\ndef build_{rel.rsplit('/', 1)[-1][:-3]}(ctx, variant=0):\n    return ctx.collection\n"}


def test_bpy_assets_climb_the_ladder_through_check_bpy_asset_and_are_reused(tmp_ws, settings, monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    def fake_check(ctx, rel, snake, *, expected_size_m=None, timeout_s=None):
        seen.append((rel, snake))
        ok = f"def build_{snake}(" in (ctx.ws.root / rel).read_text()
        return AssetCheck(ok=ok, ran=True, fatal=not ok, size_m=expected_size_m, tris=300, meshes=2, materials=2,
                          errors=[] if ok else ["no builder"])

    monkeypatch.setattr(SA, "check_bpy_asset", fake_check)
    agent = FakeAgent(_bpy_module)
    ctx, plan_obj = _bpy_ctx(tmp_ws, settings, agent=agent)
    ctx.runtime.skeleton(tmp_ws, plan_obj)
    tmp_ws.commit("skeleton")
    results = run_asset_stage(ctx)
    assert {n: (r.ok, r.path, r.strategy) for n, r in results.items()} == {
        "FishingBoat": (True, "src/assets/fishing_boat.py", "agent"), "Bollard": (True, "src/assets/bollard.py", "agent")}
    assert results["Bollard"].size_m == plan_obj.assets[1].approx_size_m
    assert set(seen) == {("src/assets/fishing_boat.py", "fishing_boat"), ("src/assets/bollard.py", "bollard")}
    for job in agent.jobs:   # the bpy asset sheet + the bpy asset system prompt, edit-only in the shared workspace
        assert "variant=0" in job.prompt and "ctx.collection" in job.prompt and "THREE" not in job.prompt
        assert "instanced by the Blender zones" in job.system_append and job.edit_only
    n_jobs = len(agent.jobs)
    again = run_asset_stage(ctx)   # S2 reuse: a committed module whose check passes is not re-paid
    assert len(agent.jobs) == n_jobs and {r.strategy for r in again.values()} == {"reused"}
    events = [e for e in EventLog(tmp_ws.events_path).read() if e["event"] == "asset.generated"]
    assert [e["strategy"] for e in events] == ["agent", "agent", "reused", "reused"]


def test_check_bpy_asset_without_a_blender_runtime_and_its_throw_away_workspace(tmp_ws, settings) -> None:
    ctx, plan_obj = _bpy_ctx(tmp_ws, settings)
    missing = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert missing.ran and missing.fatal and "was not written" in missing.errors[0]
    ctx.runtime.skeleton(tmp_ws, plan_obj)
    unavailable = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")   # FakeRuntime has no run_wrapper
    assert unavailable.ok and not unavailable.ran and "checker unavailable" in unavailable.warnings[0]
    (tmp_ws.src / "lib").mkdir(parents=True)
    (tmp_ws.src / "lib" / "place.py").write_text("def seat(obj, height_at):\n    return 0.0\n")
    scratch = SA._bpy_check_workspace(tmp_ws, "src/assets/bollard.py", "bollard")
    assert scratch.root == (tmp_ws.root / "_assets" / "bollard_check").resolve()
    entry = (scratch.root / "src" / "scene.py").read_text()
    assert 'ASSETS = {\n    "bollard": "Bollard",\n}' in entry and "ZONES = {}" in entry and "HEROES = {}" in entry
    assert (scratch.src / "assets" / "bollard.py").read_text() == (tmp_ws.src / "assets" / "bollard.py").read_text()
    assert (scratch.src / "lib" / "place.py").is_file()
    probe = (scratch.src / "zones" / f"{SA.BPY_CHECK_ZONE}.py").read_text()
    assert "ctx.assets['bollard']" in probe and 'instance_type = "COLLECTION"' in probe
    # the wrapper's isolated-stage report maps to the check: a raise is fatal with its line, a clean build is measured

    class _Run:
        def __init__(self, report, ok=True, *, error_type="Timeout", harness_failure=False):
            self.report, self.facts = report, {"unique_tris": 12}
            self.result = SimpleNamespace(ok=ok, error_type=error_type if not ok else "", error_message="killed" if not ok else "",
                                          harness_failure=harness_failure)

    class _Runtime(FakeRuntime):
        def __init__(self, report, ok=True, **result):
            super().__init__(BPY)
            self._report, self._ok, self._result = report, ok, result

        def run_wrapper(self, ws, out_dir, *, zones=None, blend=True, timeout_s=None):
            assert zones == {SA.BPY_CHECK_ZONE: "Check"} and blend is False and ws.root.name == "bollard_check"
            return _Run(self._report, self._ok, **self._result)

    ctx.runtime = _Runtime({"errors": [{"stage": "asset", "name": "bollard", "error_type": "KeyError", "error_message": "'x'",
                                        "error_file": "src/assets/bollard.py", "error_line": 7}]})
    raised = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert raised.fatal and raised.errors == ["src/assets/bollard.py:7: KeyError: 'x'"]
    ctx.runtime = _Runtime({"errors": []}, ok=False)
    killed = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert killed.fatal and "did not finish" in killed.errors[0] and "Timeout" in killed.errors[0]
    # the harness's own census-GLB writer failed AFTER the module built (run_bpy_scene's CensusGlbError →
    # BuildResult.harness_failure): not the module's doing, so the ladder spends no repair rung on it
    ctx.runtime = _Runtime({"errors": [], "error_type": "CensusGlbError"}, ok=False, error_type="CensusGlbError", harness_failure=True)
    census = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert census.ok and not census.ran and not census.fatal and census.warnings == ["checker unavailable: CensusGlbError"]
    module_error = {"stage": "asset", "name": "bollard", "error_type": "KeyError", "error_message": "'x'",
                    "error_file": "src/assets/bollard.py", "error_line": 7}
    ctx.runtime = _Runtime({"errors": [module_error], "error_type": "CensusGlbError"}, ok=False, error_type="CensusGlbError",
                           harness_failure=True)
    both = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")   # the module's own raise still wins
    assert both.fatal and both.errors == ["src/assets/bollard.py:7: KeyError: 'x'"]
    ctx.runtime = _Runtime({"errors": [{"stage": "zone", "name": SA.BPY_CHECK_ZONE, "error_type": "KeyError", "error_message": "'bollard'"}]})
    nothing = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert nothing.fatal and "returned no Collection" in nothing.errors[0]


def test_the_asset_api_speaks_each_scene_language(tmp_ws, settings) -> None:
    plan_obj = ScenePlan.model_validate(plan_example(Track.SCENE, BPY))
    boat, bollard, crate = plan_obj.assets
    results = {"FishingBoat": SA.AssetResult(name="FishingBoat", kind="bpy", ok=True, path="src/assets/fishing_boat.py", size_m=(8.0, 3.5, 3.0)),
               "Crate": SA.AssetResult(name="Crate", kind="blender_glb", ok=True, path="public/assets/crate.glb", clips=1)}
    api = asset_api_summary(plan_obj, results, {"Bollard": "FishingBoat"}, language=BPY)
    lines = api.splitlines()
    assert lines[0].startswith("Sizes are width × height × depth") and "lib.scatter.instance" in lines[0]
    assert "- FishingBoat: `ctx.assets['fishing_boat']` — built by the harness from `src/assets/fishing_boat.py` (`build_fishing_boat(ctx, variant=0)`), 8.00×3.50×3.00 m (w×h×d)" in api
    assert "- Bollard: `ctx.assets['bollard']` — variant fishing_boat builds" in api and "harness shim onto `build_fishing_boat`" in api
    assert "- Crate: `ctx.assets['crate']` — the hero GLB `public/assets/crate.glb` imported by the harness" in api
    assert "carries 1 keyframed motion clip(s)" in api and "NOT AVAILABLE" not in api
    assert "[NOT AVAILABLE — do not reference]" in asset_api_summary(plan_obj, {}, language=BPY)
    # the three.js wording is untouched (its tests pin it): no header line, import + clone instructions
    js = asset_api_summary(plan_example_scene(), {})
    assert js.startswith("- FishingBoat: `import { buildFishingBoat }") and "Sizes are" not in js


def plan_example_scene() -> ScenePlan:
    return ScenePlan.model_validate(plan_example(Track.SCENE))


def test_variant_shims_and_catalog_rows_per_language(tmp_ws, monkeypatch) -> None:
    alias = {"PondRock": "SteppingStone"}
    py = write_variant_shims(tmp_ws, alias, {"SteppingStone"}, language=BPY)
    assert py == ["src/assets/pond_rock.py"]
    shim = (tmp_ws.src / "assets" / "pond_rock.py").read_text()
    assert "from assets.stepping_stone import build_stepping_stone" in shim and "def build_pond_rock(ctx, variant=1):" in shim
    compile(shim, "pond_rock.py", "exec")
    js = write_variant_shims(tmp_ws, alias, {"SteppingStone"})
    assert js == ["src/assets/pond_rock.js"] and "buildSteppingStone(THREE, { variant: 1, ...opts })" in (tmp_ws.src / "assets" / "pond_rock.js").read_text()
    assert write_variant_shims(tmp_ws, alias, set(), language=BPY) == []   # a survivor that failed shims nothing
    catalog = ("| Want | Call |\n| --- | --- |\n"
               "| a tree | `tree(batch, loc, height, bark, foliage, *, seed=0, kind=\"broadleaf\")` — `lib/vegetation.py` |\n"
               "| a boulder | `boulder(batch, mat, center, scale, *, seed=0)` — `lib/terrain.py` |\n")
    monkeypatch.setattr(SA, "language_text", lambda lang, name: catalog if (lang, name) == (BPY, "effects_catalog.md") else "")
    assert "lib/vegetation.py" in library_rows("a shrine with a tree beside it", BPY)
    assert "lib/terrain.py" in library_rows("a well ringed by lib/terrain.py boulders", BPY)
    assert library_rows("a plain bench", BPY) == "" and library_rows("a shrine with a tree", Language.BLENDER) == ""


# --------------------------------------------------------------------------- track plumbing
def test_env_file_recipes_and_console_targets_follow_the_runtime(tmp_ws, settings) -> None:
    ctx, plan_obj = _bpy_ctx(tmp_ws, settings)
    assert env_file(ctx) == ENV_FILES[BPY] == "src/env.py" and ctx.runtime.env_file == "src/env.py"
    ctx.runtime = SimpleNamespace(zone_file=ctx.runtime.zone_file)   # a runtime without the attribute: the typed table
    assert env_file(ctx) == "src/env.py"
    assert _guess_target("Traceback: src/env.py:12 ValueError", ctx) == "env"
    assert _guess_target("KeyError in src/scene.py", ctx) == "composition"
    assert _guess_target("zone quay raised KeyError", ctx) == "Quay" and _guess_target("nothing named", ctx) == "overall"
    env_only = refine_recipes(ctx, ["src/env.py"])
    zones_only = refine_recipes(ctx, ["src/zones/quay.py"])
    assert "# role: env" in env_only and "tree_mesh" not in env_only
    assert "tree_mesh" in zones_only and "# role: env" not in zones_only
    assert refine_recipes(ctx, ["src/assets/bollard.py"]) == zones_only   # neither env nor a zone: the zone chapters


class _TransientOnceAgent(FakeAgent):
    """The first session with ``label`` dies of a provider failure (typed transient); the rest work."""

    def __init__(self, writer, label: str, *, transient: bool = True):
        super().__init__(writer)
        self.label, self.transient, self.died = label, transient, False

    def run(self, job):
        if job.label == self.label and not self.died:
            self.died = True
            self.jobs.append(job)
            return AgentResult(ok=False, exit_reason="error", errors=["rc=247; 503 UNAVAILABLE"], usage=Usage(cost_usd=0.01),
                               transient=self.transient)
        return super().run(job)


def _zone_writer(job, ws):
    return {rel: f"import bpy  # {job.label}\n\n\ndef build(ctx):\n    return ctx.collection\n" for rel in job.files_hint} or {
        "src/env.py": f"import bpy  # {job.label}\n\n\ndef height_at(x, y):\n    return 0.0\n\n\ndef build_env(ctx):\n    return {{}}\n"}


@pytest.mark.parametrize("transient", [True, False], ids=["transient", "declined"])
def test_an_env_session_killed_by_the_provider_is_retried_once(tmp_path, settings, transient: bool) -> None:
    """The round loop's transport-retry rule, applied to a stage session (env / zones): ONE re-run under
    the ``.a2`` label when the session died transient and the budget allows; a non-transient failure
    is the stage's verdict."""
    # no assets: the asset stage runs beside the env stage, and a file it writes during the env session would be
    # attributed to the env task (generation._attributed_fallback), which is a different story from this one
    plan_obj = small_scene(BPY).model_copy(update={"assets": []})
    for zone in plan_obj.zones:
        zone.contents = []
    agent = _TransientOnceAgent(_zone_writer, "env", transient=transient)
    ws = Workspace(tmp_path / "runs" / "retry")
    track = SceneTrack(services=FakeServices(assemble=True), judge=FakeJudge(scores=(0.6,)), agent=agent,
                       planner_model=_planner(plan_obj.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(BPY))
    track.run(make_spec(Track.SCENE, BPY, max_rounds=0), ws)
    labels = [j.label for j in agent.jobs if j.label.startswith("env")]
    events = [e for e in EventLog(ws.events_path).read() if e["event"] == "stage.transport_retry"]
    stage = json.loads((ws.root / "stages" / "env.json").read_text())["result"]   # the cached stage: what a resume serves
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    if transient:
        assert labels == ["env", "env.a2"] and [(e["stage"], e["label"]) for e in events] == [("env", "env")]
        assert stage["ok"] is True and "src/env.py" in stage["files"] and "generate.silent_bail" not in kinds
    else:
        # a session that merely declined gets the generation layer's own silent-bail nudge (same label), never
        # the transport retry: the stage's second session is that nudge
        assert labels == ["env", "env"] and events == [] and "generate.silent_bail" in kinds


@pytest.mark.blender
def test_check_bpy_asset_measures_a_module_in_blender(tmp_ws, settings) -> None:
    """The real check: the skeleton's one-box stub builds in its throw-away workspace, measures at the plan's
    size (Y-up w × h × d off the census GLB) and fails the placeholder rule; a two-part module passes."""
    from codeverse3d.config import get_settings
    from codeverse3d.languages import get_runtime

    if not get_settings().resolve_blender():
        pytest.skip("no Blender binary configured")
    ctx, plan_obj = _bpy_ctx(tmp_ws, settings)
    ctx.runtime = get_runtime(BPY)
    ctx.runtime.skeleton(tmp_ws, plan_obj)
    bollard = next(a for a in plan_obj.assets if a.name == "Bollard")
    stub = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard", expected_size_m=bollard.approx_size_m)
    assert stub.ran and not stub.fatal and not stub.ok and stub.size_m == pytest.approx(bollard.approx_size_m, abs=0.01)
    assert stub.tris == 12 and stub.meshes == 1 and "single low-poly box" in stub.errors[0]
    assert (tmp_ws.root / "_assets" / "bollard_check" / "artifacts" / "census.glb").is_file()
    (tmp_ws.src / "assets" / "bollard.py").write_text(
        "import bmesh\nimport bpy\n\n\ndef _part(ctx, name, size, z, rgb):\n"
        "    mat = bpy.data.materials.new(name + 'Mat')\n"
        "    mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (*rgb, 1.0)\n"
        "    me = bpy.data.meshes.new(name)\n    bm = bmesh.new()\n    bmesh.ops.create_cube(bm, size=1.0)\n"
        "    bmesh.ops.scale(bm, vec=size, verts=bm.verts)\n    bmesh.ops.translate(bm, vec=(0.0, 0.0, z), verts=bm.verts)\n"
        "    bm.to_mesh(me)\n    bm.free()\n    me.materials.append(mat)\n"
        "    ctx.collection.objects.link(bpy.data.objects.new(name, me))\n\n\n"
        "def build_bollard(ctx, variant=0):\n"
        "    _part(ctx, 'BollardBase', (0.3, 0.3, 0.1), 0.05, (0.2, 0.2, 0.22))\n"
        "    _part(ctx, 'BollardPost', (0.2, 0.2, 0.4), 0.3, (0.1, 0.1, 0.12))\n"
        "    return ctx.collection\n")
    good = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard", expected_size_m=bollard.approx_size_m)
    # (FakeServices.measure counts no materials; the real measure does — the placeholder rule reads meshes + tris here)
    assert good.ok and good.ran and good.meshes == 2 and good.tris == 24 and good.min_y == pytest.approx(0.0, abs=0.01), good
    (tmp_ws.src / "assets" / "bollard.py").write_text("import bpy\n\n\ndef build_bollard(ctx, variant=0):\n    raise KeyError('x')\n")
    raised = SA.check_bpy_asset(ctx, "src/assets/bollard.py", "bollard")
    assert raised.fatal and raised.errors[0].startswith("src/assets/bollard.py:5: KeyError")


def test_the_scene_blender_manifest_scenarios_run(tmp_path) -> None:
    """Both scenarios run end to end offline and capture the bpy payloads (their hashes are blessed by
    the integrator: tests/prompts/test_manifest.py is expected red until then)."""
    from tests.prompts.manifest import run_scenario

    agent = run_scenario("scene.scene_blender.agent.fresh", tmp_path / "agent").payloads
    assert "src/env.py" in agent["job:env@r00/prompt"] and "build_env(ctx)" in agent["job:env@r00/prompt"]
    assert "def build(ctx)" in agent["job:zones_quay_water@r00/prompt"]
    assert "bpy" in agent["chat:planner:planner/sampling"] and '"threejs"' not in agent["chat:planner:planner/sampling"]
    assert any(k.startswith("chat:model:asset_bollard") for k in agent), sorted(agent)[:20]   # the single-shot rung ran
    single = run_scenario("scene.scene_blender.single_shot.fresh", tmp_path / "single").payloads
    assert "src/env.py" in single["chat:model:env:r00/messages"] and "bpy" in single["chat:planner:planner/sampling"]
    systems = {k: v for k, v in single.items() if k.startswith("chat:model:") and k.endswith("/system")}
    assert systems and all("Blender bpy author" in v and "three.js + GLSL author" not in v for k, v in systems.items()
                           if k.startswith(("chat:model:env", "chat:model:zones")))
    assert all("bpy asset module" in v for k, v in systems.items() if k.startswith("chat:model:asset_"))
