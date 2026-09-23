"""The scene asset stage: blender GLB assets, the scene templates, and reuse of committed assets."""

from __future__ import annotations

import re

import pytest

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.orchestrator import BudgetGuard, RoundPolicy, RunState
from codeverse3d.proc import EventLog
from codeverse3d.prompts import render
from codeverse3d.tracks.common import RunContext
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.prompting import base_prompt_context
from codeverse3d.tracks.scene_assets import asset_api_summary, asset_plan, run_asset_stage
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeAgent, FakeJudge, FakeRuntime, FakeServices


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
                      tool_cards="- `build`: builds")


@pytest.mark.blender
@pytest.mark.node   # its threejs sibling asset runs the node import check
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
    # S2 reuse: a committed GLB and a committed, import-clean module short-circuit the second
    # stage run without a model call ...
    n_jobs = len(agent.jobs)
    again = run_asset_stage(ctx)
    assert len(agent.jobs) == n_jobs, "a committed passing asset must not be re-paid"
    assert again["Crate"].strategy == "reused" and again["Crate"].ok
    boat = results["FishingBoat"]
    assert again["FishingBoat"].strategy == "reused" and again["FishingBoat"].ok
    assert again["FishingBoat"].path == boat.path and again["FishingBoat"].size_m is not None
    api = asset_api_summary(plan, results)
    assert "buildFishingBoat" in api and "public/assets/crate.glb" in api
    sub = asset_plan(plan.assets[2])
    assert sub.parts[0].name == "Crate" and sub.overall_bbox.extents == (0.6, 0.4, 0.3)  # Y-up w×h×d → Z-up (w, d, h)
    ctx.extra["asset_api"] = api
    from codeverse3d.tracks.scene import SceneTrack

    st = SceneTrack(services=services)
    for tpl, extra in (("scene_env", {}),
                       ("scene_zone", dict(zone_name="Quay", zone_description="d", zone_bbox="b", zone_contents=["Bollard"], zone_file="src/zones/quay.js", neighbours=["Water: x"])),
                       ("scene_refine", dict(round_index=1, tasks=["t"], targets=["Quay"], files=["src/zones/quay.js"], edit_only_these=False, judge_summary="j", current_files={}))):
        out = render(f"tracks/{tpl}.j2", **st._ctx(ctx, **extra))
        assert "Harbour at dusk" in out and "DOM" in out
    sa = render("tracks/scene_asset.j2", **base_prompt_context(ctx, asset_name="Bollard", asset_kind="threejs", asset_description="d", asset_size=(0.3, 0.5, 0.3),
                                                              asset_file="src/assets/bollard.js", asset_language="scene_threejs", fix_instructions=["- x"], current_code=""))
    assert "buildBollard" in sa and "FIX PASS" in sa
    # ... but a committed, import-broken module is NOT reused: it goes back through generation
    (tmp_ws.root / boat.path).write_text("export function nope() {}\n")
    third = run_asset_stage(ctx)
    assert len(agent.jobs) > n_jobs and third["FishingBoat"].strategy != "reused"

