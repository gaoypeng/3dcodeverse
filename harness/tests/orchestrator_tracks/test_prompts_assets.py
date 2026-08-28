"""Every tracks/*.j2 renders with the context the tracks build; scene asset stage incl. blender_glb."""

from __future__ import annotations

import re

import pytest

from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import ArticulatedPlan, ScenePlan
from codeverse.orchestrator import BudgetGuard, RoundPolicy, RunState
from codeverse.proc import EventLog
from codeverse.prompts import list_prompts, render
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import SINGLE_SHOT_FORMAT
from codeverse.tracks.planner import plan_example
from codeverse.tracks.prompting import base_prompt_context
from codeverse.tracks.scene_assets import asset_api_summary, asset_plan, run_asset_stage
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeAgent, FakeJudge, FakeRuntime, FakeServices

TEMPLATES = {"plan_static", "plan_articulated", "plan_scene", "generate_static", "generate_articulated", "refine_object", "repair",
             "generate_static_part", "assemble_static", "detail_object",
             "scene_asset", "scene_env", "scene_zone", "scene_compose", "scene_refine"}


def _asset_module(job) -> str:
    """A three.js asset module that passes the deterministic asset check (js), else a stub."""
    rel = job.prompt.split("write `")[1].split("`")[0] if "write `" in job.prompt else ""
    if not rel.endswith(".js"):
        return f"# {job.label}\n"
    pascal = "".join(w.capitalize() for w in rel.rsplit("/", 1)[-1][:-3].split("_"))
    m = re.search(r"meters\): ([\d.]+) × ([\d.]+) × ([\d.]+)", job.prompt)
    w, h, d = (float(x) for x in m.groups()) if m else (1.0, 1.0, 1.0)
    return (f"// {job.label}\nimport * as THREE from 'three';\n"
            f"export function build{pascal}(T = THREE, opts = {{}}) {{\n"
            "  const g = new T.Group();\n"
            f"  const body = new T.Mesh(new T.BoxGeometry({w}, {h * 0.8}, {d}), new T.MeshStandardMaterial({{ color: 0x886644 }}));\n"
            f"  body.position.y = {h * 0.4};\n  g.add(body);\n"
            f"  const top = new T.Mesh(new T.SphereGeometry({min(w, d) * 0.2}, 12, 8), new T.MeshStandardMaterial({{ color: 0x224466 }}));\n"
            f"  top.position.y = {h * 0.8};\n  g.add(top);\n"
            "  return g;\n}\n")


def _ctx(tmp_ws, settings, spec, plan, *, agent_id="single-shot:gemini:x", services=None, agent=None) -> RunContext:
    lang = spec.language
    return RunContext(spec=spec, ws=tmp_ws, events=EventLog(tmp_ws.events_path), settings=settings, budget=BudgetGuard(spec.budget),
                      runtime=FakeRuntime(lang), services=services or FakeServices(), state=RunState(), policy=RoundPolicy(),
                      track=spec.track, rubric="r", agent_id=agent_id, plan=plan, agent=agent, contract_text="CONTRACT TEXT",
                      cookbook_rel=f"{lang.value}/cookbook.md", tool_cards="- `build`: builds")


def test_all_track_templates_exist_and_render(tmp_ws, settings, chair_plan):
    names = {p.split("/")[-1][:-3] for p in list_prompts() if p.startswith("tracks/") and p.endswith(".j2")}
    assert names >= TEMPLATES
    spec = make_spec()
    ctx = _ctx(tmp_ws, settings, spec, chair_plan)
    base = base_prompt_context(ctx)
    assert base["output_format"] == SINGLE_SHOT_FORMAT and "| Seat |" in base["parts_table"]
    g = render("tracks/generate_static.j2", **base_prompt_context(ctx, expected_files=["src/object.js"], skeleton_files={"src/object.js": "// s"}, previous_error=""))
    assert "Y is UP" in g and "0.430" in g and "[a1]" in g and "=== FILE:" in g and "--- src/object.js ---" in g
    r = render("tracks/refine_object.j2", **base_prompt_context(ctx, round_index=1, tasks=["[gate/contract] Seat: off"], targets=["Seat"],
                                                               files=["src/parts/seat.js"], edit_only_these=True, judge_summary="js", measurement_notes="mn",
                                                               current_files={"src/parts/seat.js": "x"}))
    assert "EDIT ONLY THESE FILES" in r and "[gate/contract] Seat" in r
    rp = render("tracks/repair.j2", **base_prompt_context(ctx, error_report="BUILD FAILED", files={}, repeats=1, attempt=2, failing_files=["src/model.py"]))
    assert "SAME ERROR" in rp
    art = ArticulatedPlan.model_validate(plan_example(Track.ARTICULATED_OBJECT))
    actx = _ctx(tmp_ws, settings, make_spec(Track.ARTICULATED_OBJECT, Language.URDF_BLENDER), art, agent_id="fake:x")
    a = render("tracks/generate_articulated.j2", **base_prompt_context(actx, expected_files=["src/model.py", "src/robot.urdf"], skeleton_files={}, previous_error=""))
    assert "| DrawerSlide |" in a and "rooted at `Cabinet`" in a and "HOW TO FINISH (agent mode)" in a and "Tools available" in a
    # agent mode does not carry the single-shot format
    assert "=== FILE:" not in a


@pytest.mark.blender
def test_scene_templates_render_and_asset_stage_with_blender(tmp_ws, settings):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS)
    judge = FakeJudge(scores=(0.5, 0.9))
    services = FakeServices(judge=judge)
    agent = FakeAgent(lambda job, ws: {job.prompt.split("write `")[1].split("`")[0] if "write `" in job.prompt else "src/x.js": _asset_module(job)})
    ctx = _ctx(tmp_ws, settings, spec, plan, agent_id="fake:x", services=services, agent=agent)
    ctx.runtime.skeleton(tmp_ws, plan)
    tmp_ws.commit("skeleton")
    results = run_asset_stage(ctx)
    assert set(results) == {"FishingBoat", "Bollard", "Crate"}
    crate = results["Crate"]
    assert crate.ok and crate.path == "public/assets/crate.glb" and (tmp_ws.public / "assets" / "crate.glb").is_file()
    assert crate.size_m is not None and crate.score is not None
    # the blender asset was judged (0.5 < 0.8) and got exactly one fix pass
    crate_jobs = [j.label for j in agent.jobs if j.label.startswith("asset_crate")]
    assert crate_jobs == ["asset_crate", "asset_crate_fix"] and crate.fixed
    assert (tmp_ws.root / "_assets" / "crate" / "src" / "model.py").is_file() and "_assets/" in (tmp_ws.root / ".gitignore").read_text()
    assert results["FishingBoat"].ok and results["FishingBoat"].path == "src/assets/fishing_boat.js"
    api = asset_api_summary(plan, results)
    assert "buildFishingBoat" in api and "public/assets/crate.glb" in api
    sub = asset_plan(plan.assets[2])
    assert sub.parts[0].name == "Crate" and sub.overall_bbox.extents == (0.6, 0.4, 0.3)  # Y-up w×h×d → Z-up (w, d, h)
    ctx.extra["asset_api"] = api
    from codeverse.tracks.scene import SceneTrack

    st = SceneTrack(services=services)
    for tpl, extra in (("scene_env", {}), ("scene_compose", {}),
                       ("scene_zone", dict(zone_name="Quay", zone_description="d", zone_bbox="b", zone_contents=["Bollard"], zone_file="src/zones/quay.js", neighbours=["Water: x"])),
                       ("scene_refine", dict(round_index=1, tasks=["t"], targets=["Quay"], files=["src/zones/quay.js"], edit_only_these=False, judge_summary="j", current_files={}))):
        out = render(f"tracks/{tpl}.j2", **st._ctx(ctx, **extra))
        assert "Harbour at dusk" in out and "DOM" in out
    sa = render("tracks/scene_asset.j2", **base_prompt_context(ctx, asset_name="Bollard", asset_kind="threejs", asset_description="d", asset_size=(0.3, 0.5, 0.3),
                                                              asset_file="src/assets/bollard.js", asset_language="scene_threejs", fix_instructions=["- x"], current_code=""))
    assert "buildBollard" in sa and "FIX PASS" in sa


def test_a_model_outage_is_not_an_escalation_signal() -> None:
    """A 503 reaches the asset stage only after models.retry spent its whole storm budget
    waiting; escalating to a full agent session then costs 10× and hits the same wall."""
    from codeverse.models.base import ModelError
    from codeverse.tracks.scene_assets import is_model_outage

    assert is_model_outage(ModelError("high demand", retryable=True, status=503))
    assert is_model_outage(ModelError("overloaded", status=529))
    assert not is_model_outage(ModelError("bad request", status=400))
    assert not is_model_outage(ValueError("the module does not import"))
