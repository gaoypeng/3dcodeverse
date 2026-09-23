"""The blender hero of the scene track climbs the same ladder as a threejs asset (offline fakes)."""

from __future__ import annotations

import json

import pytest

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.contracts.spec import Constraints
from codeverse3d.tracks import scene_assets as SA
from codeverse3d.tracks.planner import plan_example
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


def _crate_plan(asset):
    """A two-part plan the fake runtime builds at the crate's size (one box per part)."""
    from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan

    w, h, d = asset.approx_size_m
    return StaticPlan(object_name=asset.name, summary=asset.description, overall_bbox=BBox(center=(0, h / 2, 0), extents=(w, h, d)),
                      parts=[PartPlan(name="Body", role="body", description="slatted box", bbox=BBox(center=(0, (h - 0.02) / 2, 0), extents=(w, h - 0.02, d))),
                             PartPlan(name="Lid", role="lid", description="plank lid", bbox=BBox(center=(0, h - 0.01, 0), extents=(w, 0.02, d)), attach_to="Body")],
                      acceptance=[])


class _StormAgent(FakeAgent):
    """A CLI session that wrote partial files and died at the wall in a 503 streak (D68)."""

    def run(self, job):
        from codeverse3d.contracts.agent import AgentResult
        from codeverse3d.workspace import Workspace

        self.jobs.append(job)
        ws = Workspace(job.workspace)
        before = ws.head()
        for rel, content in (self.writer(job, ws) or {}).items():
            (ws.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws.root / rel).write_text(content)
        return AgentResult(ok=False, exit_reason="timeout", transient=True, files_changed=ws.changed_files(before),
                           errors=["killed by watchdog (hard_timeout) after 720s", "7 x 503 inside the CLI's own retry loop before the wall; nothing produced"])


def test_a_hero_session_that_died_in_the_storm_gets_one_more_cheap_repair(tmp_ws, settings, monkeypatch):
    """A storm-dead hero session gets ONE more single-shot repair with the check's feedback."""
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
    limit = SA.HERO_MAX_TRIS if kind == "blender_glb" else SA.ASSET_MAX_TRIS
    SA._soft_findings(chk, (0.6, 0.4, 0.3), max_tris=limit)
    assert not chk.ok and any("sinks" in e for e in chk.errors)
    assert any(f"{limit} budget" in e for e in chk.errors)


def test_a_fix_that_judges_worse_is_undone(tmp_ws, settings):
    """When the re-judge says the fix made the asset worse, the pre-fix GLB and src ship."""
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
    """A plan of <= HERO_THIN_PLAN_PARTS parts for a richer sheet is re-asked ONCE with its features as must_have."""

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


def test_a_threejs_fix_with_only_soft_findings_is_kept_and_judged_again(tmp_ws, settings, monkeypatch):
    """A fix with only soft findings still imports, so it is kept and judged; only a non-importing fix is undone."""
    from codeverse3d.contracts.artifacts import RenderSet

    boat = "src/assets/fishing_boat.js"
    ctx, _hero = _scene(tmp_ws, settings, services=CardedServices(judge=FakeJudge(scores=(0.5, 0.7))),
                        agent=FakeAgent(lambda job, ws: {boat: f"// {job.label}\n"}))
    asset = next(a for a in ctx.plan.assets if a.name == "FishingBoat")
    ctx.runtime.render_asset = lambda ws, name, out_dir: RenderSet(views=[])  # a verdict is possible
    checks = iter([SA.AssetCheck(ok=True, ran=True, tris=900, meshes=3, materials=2),      # the generated module
                   SA.AssetCheck(ok=False, ran=True, fatal=False,                          # the fix: soft finding only
                                 errors=["measured w=20.00 m but the plan says 8.00 m — rescale"])])
    monkeypatch.setattr(SA, "check_threejs_asset", lambda *a, **k: next(checks))
    monkeypatch.setattr(SA, "_judge_wanted", lambda *a, **k: True)
    res = SA.build_threejs_asset(ctx, asset, judge=True)
    assert res.ok and res.fixed and (res.score_before, res.score) == (0.5, 0.7)
    assert (tmp_ws.root / boat).read_text() == "// asset_fishing_boat_fix\n", "the fix that imports is kept"
