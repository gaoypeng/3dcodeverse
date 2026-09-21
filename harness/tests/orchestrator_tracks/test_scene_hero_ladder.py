"""The blender hero of the scene track climbs the same ladder as a threejs asset, with the
scene in its prompt and its GLB checked (2026-09-07 review of PR #7).

Offline: fake agent / chat model / judge / runtime (a trimesh box per plan part) / services.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import ScenePlan
from codeverse.contracts.spec import Constraints
from codeverse.tracks import scene_assets as SA
from codeverse.tracks.planner import plan_example
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FAIL_MARK,
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeServices,
)
from tests.orchestrator_tracks.test_prompts_assets import _ctx

SCENE_PROMPT = "Harbour at dusk: a weathered fishing quay with nets and crates"
GOOD_MODEL = "import bpy\n# AGENT_WROTE a crate\n"


class CardedServices(FakeServices):
    """Tool cards that say which (track, language) they were asked for."""

    def tool_cards(self, track: str, language: str) -> str:
        return f"CARDS({track}/{language})"


class SingleShotServices(CardedServices):
    """A chat model: a canned StaticPlan for plan requests, a file envelope for asset prompts."""

    def __init__(self, *, model_text: str = f"=== FILE: src/model.py ===\n{GOOD_MODEL}=== END FILE ===", **kw):
        super().__init__(**kw)
        self.model_text = model_text
        self.chat = FakeChatModel(self._answer)

    def _answer(self, req):
        if req.response_schema is not None:      # the planner
            return plan_example(Track.STATIC_OBJECT)
        return self.model_text

    def chat_model(self, model_id: str):
        return self.chat


def _scene(tmp_ws, settings, *, services, agent=None, agent_id="fake:x"):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, prompt=SCENE_PROMPT)
    spec = spec.model_copy(update={"constraints": Constraints(style="weathered, hand-painted", must_have=["a crate stack"])})
    ctx = _ctx(tmp_ws, settings, spec, plan, agent_id=agent_id, services=services, agent=agent)
    ctx.runtime.skeleton(tmp_ws, plan)
    tmp_ws.commit("skeleton")
    hero = next(a for a in plan.assets if a.kind == "blender_glb")
    return ctx, hero


def _writer(text: str):
    """Every session writes something NEW (a fix that changes nothing is not a fix)."""
    return lambda job, ws: {"src/model.py": f"{text}# session {job.label}\n"}


def test_hero_prompt_carries_the_scene_and_blenders_own_tools(tmp_ws, settings):
    agent = FakeAgent(_writer(GOOD_MODEL))
    ctx, hero = _scene(tmp_ws, settings, services=CardedServices(), agent=agent)
    res = SA.build_blender_asset(ctx, hero, judge=False)
    assert res.ok and res.strategy == "agent"
    job = agent.jobs[0]
    assert job.label == f"asset_{SA.to_snake(hero.name)}"
    # the SCENE, not the asset sheet, is the scene; its style reaches the prop
    assert f"Scene: {SCENE_PROMPT}" in job.prompt and "weathered, hand-painted" in job.prompt
    assert f"Scene: {hero.description}" not in job.prompt and "(none)" not in job.prompt.split("## The asset")[0]
    # the hero's tools are Blender's (check_contract, cross_section …), not the scene's
    assert "CARDS(static_object/blender)" in job.prompt and "CARDS(scene/" not in job.prompt
    # the plan's detail budget, not a flat number; the layout it must fill
    assert "40k" not in job.prompt and "DETAIL BUDGET" in job.prompt
    assert job.files_hint == ["src/model.py"] and job.timeout_s <= SA.asset_timeout_s(ctx, 180)
    # the sub-run's plan.json is the plan the prompt was built from
    sub = tmp_ws.root / "_assets" / SA.to_snake(hero.name)
    assert json.loads((sub / "plan.json").read_text())["object_name"] == hero.name
    events = [json.loads(line) for line in tmp_ws.events_path.read_text().splitlines()]
    gen = [e for e in events if e["event"] == "asset.generated" and e["asset"] == hero.name]
    assert gen and gen[0]["strategy"] == "agent" and gen[0]["ok"] is True


def _crate_plan(asset):
    """A two-part plan the fake runtime builds at the crate's size (one box per part)."""
    from codeverse.contracts.plan import BBox, PartPlan, StaticPlan

    w, h, d = asset.approx_size_m
    return StaticPlan(object_name=asset.name, summary=asset.description, overall_bbox=BBox(center=(0, h / 2, 0), extents=(w, h, d)),
                      parts=[PartPlan(name="Body", role="body", description="slatted box", bbox=BBox(center=(0, (h - 0.02) / 2, 0), extents=(w, h - 0.02, d))),
                             PartPlan(name="Lid", role="lid", description="plank lid", bbox=BBox(center=(0, h - 0.01, 0), extents=(w, 0.02, d)), attach_to="Body")],
                      acceptance=[])


def test_hero_tries_single_shot_before_an_agent_session(tmp_ws, settings, monkeypatch):
    services = SingleShotServices()
    agent = FakeAgent(_writer(GOOD_MODEL))
    monkeypatch.setattr(SA, "hero_plan", lambda sub, asset: _crate_plan(asset))   # two parts → two boxes → not "one low-poly box"
    ctx, hero = _scene(tmp_ws, settings, services=services, agent=agent)
    res = SA.build_blender_asset(ctx, hero, judge=False)
    assert res.ok and res.strategy == "single-shot" and res.path == f"public/assets/{SA.to_snake(hero.name)}.glb"
    assert agent.jobs == [] and len(services.chat.requests) == 1
    req = services.chat.requests[0]
    assert req.response_schema is None and "=== FILE:" in req.system + " ".join(p.text for m in req.messages for p in m.parts if getattr(p, "text", ""))
    assert (tmp_ws.public / "assets" / f"{SA.to_snake(hero.name)}.glb").is_file() and res.size_m is not None


def test_hero_plan_is_the_static_planners_part_list(tmp_ws, settings):
    services = SingleShotServices()
    ctx, hero = _scene(tmp_ws, settings, services=services, agent=FakeAgent(_writer(GOOD_MODEL)))
    res = SA.build_blender_asset(ctx, hero, judge=False)
    assert res.ok
    sub = tmp_ws.root / "_assets" / SA.to_snake(hero.name)
    plan = json.loads((sub / "plan.json").read_text())
    assert len(plan["parts"]) > 1, "a hero gets a real part list, not the one-part sheet"
    events = [json.loads(line) for line in tmp_ws.events_path.read_text().splitlines()]
    assert any(e["event"] == "asset.planned" and e["n_parts"] == len(plan["parts"]) for e in events)


def test_hero_glb_soft_findings_trigger_one_feedback_repair_then_escalate(tmp_ws, settings):
    # the one-part sheet (planner unavailable → fallback) builds ONE tiny box: "still a single
    # low-poly box" is exactly the finding a threejs module gets; single-shot gets one repair
    # with that feedback, then the agent session takes over
    class NoPlanner(SingleShotServices):
        def _answer(self, req):
            if req.response_schema is not None:
                raise RuntimeError("planner down")
            return self.model_text

    services = NoPlanner()
    agent = FakeAgent(_writer(GOOD_MODEL))
    ctx, hero = _scene(tmp_ws, settings, services=services, agent=agent)
    res = SA.build_blender_asset(ctx, hero, judge=False)
    assert res.strategy == "escalated" and res.ok
    texts = [" ".join(p.text for m in r.messages for p in m.parts if getattr(p, "text", "")) for r in services.chat.requests
             if r.response_schema is None]
    assert len(texts) == 2 and "did NOT pass the deterministic asset check" in texts[1] and "single low-poly box" in texts[1]
    assert "## Current file(s)" in texts[1] and [j.label for j in agent.jobs] == [f"asset_{SA.to_snake(hero.name)}"]
    events = [json.loads(line) for line in tmp_ws.events_path.read_text().splitlines()]
    assert any(e["event"] == "asset.escalated" for e in events) and any(e["event"] == "asset.plan_failed" for e in events)


class _StormAgent(FakeAgent):
    """A CLI session that wrote partial files and died at the wall in a 503 streak with no final
    answer (D68: 26 of the evening's 40 timed-out sessions)."""

    def run(self, job):
        from codeverse.contracts.agent import AgentResult
        from codeverse.workspace import Workspace

        self.jobs.append(job)
        ws = Workspace(job.workspace)
        before = ws.head()
        for rel, content in (self.writer(job, ws) or {}).items():
            (ws.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws.root / rel).write_text(content)
        return AgentResult(ok=False, exit_reason="timeout", transient=True, files_changed=ws.changed_files(before),
                           errors=["killed by watchdog (hard_timeout) after 720s", "7 x 503 inside the CLI's own retry loop before the wall; nothing produced"])


def test_a_hero_session_that_died_in_the_storm_gets_one_more_cheap_repair(tmp_ws, settings, monkeypatch):
    """Loops 10 and 13 (2026-09-07): the clockmaker's LongcaseClock escalated, the session wrote
    eleven part files and died at the wall after 7 x 503, the partial parts failed the check and
    the scene shipped without its hero.  The ladder now takes ONE more single-shot repair with
    that check's feedback (asset.storm_repair) before giving the hero up."""
    calls = {"n": 0}

    class FailTwiceThenGood(SingleShotServices):
        def _answer(self, req):
            if req.response_schema is not None:
                return plan_example(Track.STATIC_OBJECT)
            calls["n"] += 1
            body = GOOD_MODEL if calls["n"] >= 3 else f"import bpy\n{FAIL_MARK}\n"   # two shots fail, the rung answers
            return f"=== FILE: src/model.py ===\n{body}=== END FILE ==="

    services = FailTwiceThenGood()
    agent = _StormAgent(_writer(f"import bpy\n{FAIL_MARK}\n"))       # partial, broken parts
    monkeypatch.setattr(SA, "hero_plan", lambda sub, asset: _crate_plan(asset))
    ctx, hero = _scene(tmp_ws, settings, services=services, agent=agent)
    res = SA.build_blender_asset(ctx, hero, judge=False)
    assert res.ok and res.strategy == "escalated+repair", (res.strategy, res.notes)
    # ONE agent session: the partial files were checked on the cheap context (no agent repair) and the rung went single-shot
    assert calls["n"] == 3 and [j.label for j in agent.jobs] == [f"asset_{SA.to_snake(hero.name)}"]
    texts = [" ".join(p.text for m in r.messages for p in m.parts if getattr(p, "text", "")) for r in services.chat.requests
             if r.response_schema is None]
    assert "did NOT pass the deterministic asset check" in texts[2]
    events = [json.loads(line) for line in tmp_ws.events_path.read_text().splitlines()]
    assert [e["event"] for e in events if e["event"] in ("asset.escalated", "asset.storm_repair")] == ["asset.escalated", "asset.storm_repair"]


def test_hero_repair_sessions_are_clipped_to_the_asset_window_and_capped_at_one(tmp_ws, settings):
    agent = FakeAgent(_writer(f"import bpy\n# {FAIL_MARK}\n"))   # every session leaves a build that fails
    ctx, hero = _scene(tmp_ws, settings, services=CardedServices(), agent=agent)
    res = SA.build_blender_asset(ctx, hero, judge=False)
    assert not res.ok and "build failed" in res.notes
    labels = [j.label for j in agent.jobs]
    snake = SA.to_snake(hero.name)
    assert labels == [f"asset_{snake}", f"asset_{snake}_repair1"], labels   # max_repair_attempts is 2 in the spec
    window = SA.asset_timeout_s(ctx, 180)
    assert all(j.timeout_s <= window for j in agent.jobs), [(j.label, j.timeout_s) for j in agent.jobs]


def test_hero_fix_is_rejudged_committed_and_carries_a_system_prompt(tmp_ws, settings):
    judge = FakeJudge(scores=(0.5, 0.9))
    agent = FakeAgent(_writer(GOOD_MODEL))
    ctx, hero = _scene(tmp_ws, settings, services=CardedServices(judge=judge), agent=agent)
    res = SA.build_blender_asset(ctx, hero, judge=True)
    assert res.ok and res.fixed and res.judged
    assert res.score_before == 0.5 and res.score == 0.9 and len(judge.calls) == 2
    snake = SA.to_snake(hero.name)
    fix = next(j for j in agent.jobs if j.label == f"asset_{snake}_fix")
    assert fix.system_append and "PROP" in fix.system_append.upper()
    sub = tmp_ws.root / "_assets" / snake
    out = SA.Workspace(sub).renders_dir(0) / "assets"          # the hero is judged in ITS workspace
    assert (out / snake / "judge.json").is_file() and (out / f"{snake}_fix" / "judge.json").is_file()
    status = subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=sub, capture_output=True, text=True).stdout
    assert status.strip() == "", status


def test_hero_reentry_keeps_the_previous_sessions_src(tmp_ws, settings):
    agent = FakeAgent(_writer(GOOD_MODEL))
    ctx, hero = _scene(tmp_ws, settings, services=CardedServices(), agent=agent)
    assert SA.build_blender_asset(ctx, hero, judge=False).ok
    snake = SA.to_snake(hero.name)
    (tmp_ws.public / "assets" / f"{snake}.glb").unlink()          # the reuse guard cannot fire
    silent = FakeAgent(lambda job, ws: None)                       # this session writes nothing
    ctx2 = SA.replace(ctx, agent=silent)
    res = SA.build_blender_asset(ctx2, hero, judge=False)
    model = (tmp_ws.root / "_assets" / snake / "src" / "model.py").read_text()
    assert "AGENT_WROTE" in model, "the skeleton must not overwrite an earlier session's work"
    assert res.ok and (tmp_ws.public / "assets" / f"{snake}.glb").is_file()   # the kept src builds again


@pytest.mark.parametrize("kind", ["threejs", "blender_glb"])
def test_asset_check_soft_findings_share_one_rule_set(kind):
    chk = SA.AssetCheck(ok=True, ran=True, size_m=(0.6, 0.4, 0.3), min_y=-0.1, tris=50_000, meshes=1, materials=1)
    SA._soft_findings(chk, (0.6, 0.4, 0.3), max_tris=SA.HERO_MAX_TRIS if kind == "blender_glb" else SA.ASSET_MAX_TRIS)
    assert not chk.ok and any("sinks" in e for e in chk.errors)
    limit = SA.HERO_MAX_TRIS if kind == "blender_glb" else SA.ASSET_MAX_TRIS
    assert any(f"{limit} budget" in e for e in chk.errors)
    assert not Path("nonexistent").exists()


def test_a_fix_that_judges_worse_is_undone(tmp_ws, settings):
    """The judge's fix pass is one more model answer: when the re-judge says it made the
    asset worse, the pre-fix GLB and src ship (loop 1, 2026-09-07: BronzeCenser 0.526 → 0.43)."""
    judge = FakeJudge(scores=(0.6, 0.4))
    agent = FakeAgent(_writer(GOOD_MODEL))
    ctx, hero = _scene(tmp_ws, settings, services=CardedServices(judge=judge), agent=agent)
    res = SA.build_blender_asset(ctx, hero, judge=True)
    assert res.ok and res.judged and not res.fixed and res.score == 0.6 and res.score_before is None
    snake = SA.to_snake(hero.name)
    model = (tmp_ws.root / "_assets" / snake / "src" / "model.py").read_text()
    assert f"session asset_{snake}\n" in model and "_fix" not in model, "src is back at the pre-fix session"
    events = [json.loads(line) for line in tmp_ws.events_path.read_text().splitlines()]
    assert any(e["event"] == "asset.fix_reverted" and e["before"] == 0.6 and e["after"] == 0.4 for e in events)
    assert (tmp_ws.public / "assets" / f"{snake}.glb").is_file()


def test_a_fix_that_does_not_build_is_undone(tmp_ws, settings):
    """The restore only ran when the fix JUDGED worse; a fix that broke the build left the broken
    src in the hero's workspace next to the GLB of the version before it."""
    judge = FakeJudge(scores=(0.6,))
    snake = "crate"
    agent = FakeAgent(lambda job, ws: {"src/model.py": GOOD_MODEL + ("" if job.label == f"asset_{snake}" else FAIL_MARK)   # the fix AND its repair break
                                       + f"# session {job.label}\n"})
    ctx, hero = _scene(tmp_ws, settings, services=CardedServices(judge=judge), agent=agent)
    assert SA.to_snake(hero.name) == snake
    res = SA.build_blender_asset(ctx, hero, judge=True)
    assert res.ok and res.judged and not res.fixed and res.score == 0.6
    model = (tmp_ws.root / "_assets" / snake / "src" / "model.py").read_text()
    assert FAIL_MARK not in model and f"session asset_{snake}\n" in model, "src is back at the version that built"
    assert (tmp_ws.public / "assets" / f"{snake}.glb").is_file()


def test_the_largest_module_is_judged_and_the_small_ones_are_not(tmp_ws, settings):
    """`render_asset` makes a threejs verdict possible; the share rule alone judged nothing
    (no prop reaches 5 % of a scene's volume), so the plan's largest module keeps its
    verdict and the rest skip with the same event as before."""
    ctx, _hero = _scene(tmp_ws, settings, services=CardedServices(), agent=FakeAgent(_writer(GOOD_MODEL)))
    mods = [a for a in ctx.plan.assets if a.kind == "threejs"]
    big = max(mods, key=lambda a: a.approx_size_m[0] * a.approx_size_m[1] * a.approx_size_m[2])
    small = min(mods, key=lambda a: a.approx_size_m[0] * a.approx_size_m[1] * a.approx_size_m[2])
    ok = SA.AssetCheck(ok=True, ran=True, tris=500, meshes=3, materials=2)
    assert SA._judge_wanted(ctx, big, ok, judge=True) is True
    assert SA._judge_wanted(ctx, small, ok, judge=True) is False
    events = [json.loads(line) for line in tmp_ws.events_path.read_text().splitlines()]
    assert [e["asset"] for e in events if e["event"] == "asset.judge_skipped"] == [small.name]


def test_a_thin_hero_plan_is_asked_again_with_the_sheets_features_as_the_checklist(tmp_ws, settings):
    """The windmill got a one-part plan twice while every other hero got 7-12 parts: a plan
    of <= HERO_THIN_PLAN_PARTS parts for a sheet naming more features is re-asked ONCE with
    those features as must_have (what sizes the planner's part budget)."""

    calls = []

    class ThinUntilTheChecklist(SingleShotServices):
        """One part for every ask that carries no checklist — the planner's own in-context
        re-ask and restart included, which is what the windmill did — and the real list once
        the features are in must_have."""

        def _answer(self, req):
            if req.response_schema is not None:
                text = " ".join(p.text for m in req.messages for p in m.parts if getattr(p, "text", ""))
                # the description names the lid in EVERY ask; only the retry puts it on the checklist
                calls.append("MUST HAVE: a hinged lid" in text)
                if "MUST HAVE: a hinged lid" not in text:
                    ex = dict(plan_example(Track.STATIC_OBJECT))
                    ex["parts"] = [ex["parts"][0]]              # one part
                    return ex
                return plan_example(Track.STATIC_OBJECT)        # the real list
            return self.model_text

    services = ThinUntilTheChecklist()
    ctx, hero = _scene(tmp_ws, settings, services=services, agent=FakeAgent(_writer(GOOD_MODEL)))
    hero = hero.model_copy(update={"description": "a slatted crate with a hinged lid, rope handles, iron corner straps and a chalked label"})
    assert len(SA.hero_features(hero.description)) > SA.HERO_THIN_PLAN_PARTS
    res = SA.build_blender_asset(ctx, hero, judge=False)
    # the planner's own asks (no checklist) came first and stayed thin; the retry's asks carry
    # the checklist and are the last — the planner may re-ask once more INSIDE that one retry
    assert res.ok and calls[0] is False and calls[-1] is True, calls
    events = [json.loads(line) for line in tmp_ws.events_path.read_text().splitlines()]
    thin = [e for e in events if e["event"] == "asset.plan_thin"]
    assert len(thin) == 1, [e["event"] for e in events]                     # exactly one retry
    assert thin[0]["n_parts"] == 1 and "a hinged lid" in thin[0]["features"]
    plan = json.loads((tmp_ws.root / "_assets" / SA.to_snake(hero.name) / "plan.json").read_text())
    assert len(plan["parts"]) > 1
