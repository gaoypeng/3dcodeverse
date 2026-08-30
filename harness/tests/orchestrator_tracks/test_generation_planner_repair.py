"""generation (envelope parsing, single-shot, agent path), planner, repair loop."""

from __future__ import annotations

import json

import pytest

from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import ScenePlan, StaticPlan
from codeverse.orchestrator import BudgetGuard, RoundPolicy, RunState
from codeverse.proc import EventLog
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import (
    SINGLE_SHOT_FORMAT,
    GenerationError,
    GenerationTask,
    MultiFileParseError,
    generate,
    parse_multifile,
    safe_relpath,
    write_files,
)
from codeverse.tracks.planner import (
    PlanningError,
    build_system_prompt,
    ensure_acceptance,
    plan,
    plan_example,
)
from codeverse.tracks.repair import (
    build_with_repair,
    format_error_report,
    relevant_cookbook_section,
)
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FAIL_MARK,
    FakeAgent,
    FakeChatModel,
    FakeRuntime,
    FakeServices,
)


# ----------------------------------------------------------------------------- envelope
def test_parse_multifile_blocks_and_fences():
    text = """Here you go.
=== FILE: src/object.js ===
```js
export function build(THREE) { return new THREE.Group(); }
```
=== END FILE ===
=== FILE: ./src/parts/seat.js ===
export function buildSeat(THREE) {}
=== END FILE ===
"""
    files = parse_multifile(text)
    assert set(files) == {"src/object.js", "src/parts/seat.js"}
    assert files["src/object.js"].startswith("export function build") and "```" not in files["src/object.js"]


def test_parse_multifile_single_fence_fallback_and_errors():
    assert parse_multifile("```python\nimport bpy\nprint(1)\n```", expected_files=["src/model.py"]) == {"src/model.py": "import bpy\nprint(1)"}
    hinted = "**src/env.js**\n```js\nexport const A=1;\n```\nand `src/scene.js`:\n```js\nexport const B=2;\n```"
    files = parse_multifile(hinted)
    assert files == {"src/env.js": "export const A=1;", "src/scene.js": "export const B=2;"}
    with pytest.raises(MultiFileParseError):
        parse_multifile("Sorry, I cannot do that.", expected_files=["src/a.js", "src/b.js"])
    with pytest.raises(MultiFileParseError):
        parse_multifile("", expected_files=["src/a.js"])


def test_write_files_filters_paths_and_os_errors(tmp_ws):
    assert safe_relpath("./src/x.js") == "src/x.js"
    for bad in ("../etc/passwd", "/abs/src/x.js", "docs/readme.md", "src/../../x"):
        with pytest.raises(GenerationError):
            safe_relpath(bad)
    changes = write_files(tmp_ws, {"src/a.js": "x", "public/b.txt": "y\n"})
    assert [c.path for c in changes] == ["src/a.js", "public/b.txt"] and (tmp_ws.src / "a.js").read_text() == "x\n"
    assert "=== FILE:" in SINGLE_SHOT_FORMAT

    skipped: list[str] = []
    changes = write_files(
        tmp_ws,
        {"src/parts/": "Here are the parts.\n", "src/object.js": "export const x = 1;\n"},
        on_skip=lambda path, why: skipped.append(path),
    )
    assert [c.path for c in changes] == ["src/object.js"]
    assert skipped == ["src/parts/"]
    assert not (tmp_ws.src / "parts").is_file()

    (tmp_ws.src / "blocked.js").mkdir(parents=True)
    skipped.clear()
    changes = write_files(tmp_ws, {"src/blocked.js": "x", "src/c.js": "y"},
                          on_skip=lambda path, why: skipped.append(path))
    assert [c.path for c in changes] == ["src/c.js"] and skipped == ["src/blocked.js"]


# ----------------------------------------------------------------------------- strategies
def test_generate_single_shot_writes_files_and_charges(tmp_ws):
    model = FakeChatModel(lambda req: "=== FILE: src/model.py ===\nimport bpy\n=== END FILE ===")
    budget = BudgetGuard(make_spec().budget)
    events = EventLog(tmp_ws.events_path)
    task = GenerationTask(label="baseline", prompt="build it", system="sys", files_hint=["src/model.py"])
    res = generate(tmp_ws, agent_id="single-shot:gemini:x", task=task, model=model, budget=budget, events=events)
    assert res.ok and [c.path for c in res.files_changed] == ["src/model.py"] and (tmp_ws.src / "model.py").read_text() == "import bpy\n"
    assert budget.spent.cost_usd == pytest.approx(0.002)
    # the label carries the round so the cost ledger can attribute a single-shot call
    assert "=== FILE:" in model.requests[0].system and model.requests[0].label == "baseline:r00"
    assert (tmp_ws.trajectories / "baseline_r00" / "response.md").is_file()
    # parse failure → ok=False, no raise
    bad = FakeChatModel(lambda req: "nope")
    res2 = generate(tmp_ws, agent_id="single-shot:gemini:x", task=GenerationTask(label="b", prompt="p", files_hint=["src/a.js", "src/b.js"]), model=bad)
    assert not res2.ok and "parse failed" in res2.notes


def test_generate_agent_path_silent_bail_retry(tmp_ws):
    calls = []

    def writer(job, ws):
        calls.append(job.prompt[:40])
        return None if len(calls) == 1 else {"src/model.py": "import bpy\n"}

    agent = FakeAgent(writer)
    res = generate(tmp_ws, agent_id="fake:fake-model", task=GenerationTask(label="baseline", prompt="build it"), agent=agent,
                   events=EventLog(tmp_ws.events_path))
    assert res.ok and len(calls) == 2 and calls[1].startswith("Your previous attempt ended WITHOUT")
    assert res.usage.cost_usd == pytest.approx(0.02)
    assert agent.jobs[0].write_roots == ["src", "public"] and agent.jobs[0].label == "baseline"


# ----------------------------------------------------------------------------- planner
def _valid_plan_dict() -> dict:
    return plan_example(Track.STATIC_OBJECT)


def test_planner_validates_retries_and_writes(tmp_ws):
    spec = make_spec()
    answers = [{"object_name": "X"}, _valid_plan_dict()]
    model = FakeChatModel(lambda req: answers.pop(0))
    events = EventLog(tmp_ws.events_path)
    budget = BudgetGuard(spec.budget)
    p = plan(spec, "fake:planner", StaticPlan, tmp_ws, model=model, events=events, budget=budget, runtime=FakeRuntime(Language.THREEJS))
    assert isinstance(p, StaticPlan) and p.object_name == "DiningChair" and tmp_ws.plan_path.is_file()
    assert len(model.requests) == 2 and "failed validation" in model.requests[1].messages[-1].text
    assert model.requests[0].response_schema is not None and "PascalCase" in model.requests[0].system
    from codeverse.tracks.planner import PLAN_MAX_WAIT_S, plan_wait_s

    assert all(PLAN_MAX_WAIT_S <= r.max_wait_s <= plan_wait_s(r.max_output_tokens) for r in model.requests)
    # deterministic acceptance items from constraints were appended
    texts = " ".join(a.text for a in p.acceptance)
    assert "height = 0.820" in texts and "Includes: armrests" in texts
    assert budget.spent.cost_usd == pytest.approx(0.004)
    kinds = [e["event"] for e in events.read()]
    assert "plan.invalid" in kinds and "plan.done" in kinds


def test_planner_validation_reasks_and_ceiling(tmp_ws):
    model = FakeChatModel(lambda req: {"bad": 1})
    with pytest.raises(PlanningError):
        plan(make_spec(), "fake:planner", StaticPlan, tmp_ws, model=model)
    from codeverse.tracks.planner import MAX_VALIDATION_REASKS

    assert len(model.requests) == 1 + MAX_VALIDATION_REASKS == 3

    answers = [{"object_name": "X"}, {"object_name": "Y"}, _valid_plan_dict()]
    model = FakeChatModel(lambda req: answers.pop(0))
    p = plan(make_spec(), "fake:planner", StaticPlan, tmp_ws, model=model, runtime=FakeRuntime(Language.THREEJS))
    assert p.object_name == "DiningChair" and len(model.requests) == 3
    assert all("failed validation" in r.messages[-1].text for r in model.requests[1:])


def test_planner_scene_example_validates_and_prompts_render():
    assert isinstance(ScenePlan.model_validate(plan_example(Track.SCENE)), ScenePlan)
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS)
    s = build_system_prompt(spec, ScenePlan)
    assert "zones" in s and "Y is UP" in s
    from codeverse.contracts.plan import ArticulatedPlan

    assert isinstance(ArticulatedPlan.model_validate(plan_example(Track.ARTICULATED_OBJECT)), ArticulatedPlan)
    assert "pivot" in build_system_prompt(make_spec(Track.ARTICULATED_OBJECT, Language.URDF_BLENDER), ArticulatedPlan)


def test_ensure_acceptance_is_idempotent():
    p = StaticPlan.model_validate(_valid_plan_dict())
    spec = make_spec()
    n1 = len(ensure_acceptance(p, spec).acceptance)
    n2 = len(ensure_acceptance(p, spec).acceptance)
    assert n1 == n2 and any(a.how == "measure" for a in p.acceptance)
    # an object plan's own items keep their priority: there IS a measurement pass to settle them
    assert all(a.priority == "must" for a in p.acceptance)


def test_scene_plan_items_are_advisory_and_only_the_spec_must_haves_gate():
    from codeverse.contracts.plan import AcceptanceItem

    p = ScenePlan.model_validate(plan_example(Track.SCENE))
    p.acceptance = [AcceptanceItem(id="a1", text="parapet is 1.05 m high", how="measure", priority="must"),
                    AcceptanceItem(id="a2", text="the water ripples", how="probe", priority="must")]
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS)
    spec = spec.model_copy(update={"constraints": spec.constraints.model_copy(
        update={"dimensions_m": None, "must_have": ["a koi pond", "gentle water ripple animation"]})})
    out = ensure_acceptance(p, spec)
    by = {a.id: a for a in out.acceptance}
    assert [a.id for a in out.acceptance] == ["a1", "a2", "must1", "must2"]
    assert [a.priority for a in out.acceptance] == ["should", "should", "must", "must"]
    assert by["must2"].text.endswith("gentle water ripple animation")


# ----------------------------------------------------------------------------- repair
def _ctx(tmp_ws, settings, agent_id: str, agent=None, model=None, cookbook: str = "") -> RunContext:
    spec = make_spec(generator=agent_id)
    rt = FakeRuntime(Language.THREEJS)
    return RunContext(spec=spec, ws=tmp_ws, events=EventLog(tmp_ws.events_path), settings=settings, budget=BudgetGuard(spec.budget),
                      runtime=rt, services=FakeServices(), state=RunState(), policy=RoundPolicy(), track=Track.STATIC_OBJECT,
                      rubric="r", agent_id=agent_id, agent=agent, model=model, cookbook_text=cookbook,
                      contract_text="CONTRACT")


def test_build_with_repair_agent_path_escalates_on_same_error(tmp_ws, settings):
    (tmp_ws.src / "model.py").write_text(f"import bpy\n# {FAIL_MARK}\nprint(1)\n")
    tmp_ws.write_json(tmp_ws.plan_path, {"parts": []})
    prompts = []

    def writer(job, ws):
        prompts.append(job.prompt)
        if len(prompts) == 1:
            return {"src/model.py": f"import bpy\n# still {FAIL_MARK}\n"}  # same error again
        return {"src/model.py": "import bpy\nprint('fixed')\n"}

    ctx = _ctx(tmp_ws, settings, "fake:x", agent=FakeAgent(writer), cookbook="## Intro\nhello\n## RuntimeError boom\nuse boom fix snippet\n")
    out = build_with_repair(ctx, round_index=0, label="r00")
    assert out.ok and out.repaired and len(out.attempts) == 2
    assert "BUILD FAILED" in prompts[0] and "src/model.py:3" in prompts[0] and "boom fix snippet" in prompts[0]
    assert "SAME ERROR AS THE PREVIOUS ATTEMPT" in prompts[1] and "SAME ERROR" not in prompts[0]
    kinds = [e["event"] for e in ctx.events.read()]
    assert kinds.count("repair.attempt") == 2 and kinds.count("build.done") == 3


def test_build_with_repair_single_shot_sends_file_contents_and_stops_at_max(tmp_ws, settings):
    (tmp_ws.src / "model.py").write_text(f"import bpy\n# {FAIL_MARK}\n")
    tmp_ws.write_json(tmp_ws.plan_path, {"parts": []})
    seen = []

    def responder(req):
        seen.append(req)
        return f"=== FILE: src/model.py ===\nimport bpy\n# {FAIL_MARK} again\n=== END FILE ==="

    ctx = _ctx(tmp_ws, settings, "single-shot:gemini:x", model=FakeChatModel(responder))
    out = build_with_repair(ctx, round_index=1, label="r01", files_hint=["src/model.py"])
    assert not out.ok and len(out.attempts) == 2  # max_repair_attempts=2 in make_spec
    assert "--- src/model.py ---" in seen[0].messages[0].text and "import bpy" in seen[0].messages[0].text
    assert seen[1].temperature > seen[0].temperature  # escalation on identical signature
    assert ctx.budget.spent.cost_usd == pytest.approx(0.004)


def test_format_error_report_and_cookbook_section():
    from codeverse.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity

    b = BuildResult(ok=False, language="threejs", error_type="TypeError", error_message="Cannot read properties of null (reading 'fillStyle')",
                    error_file="src/parts/base.js", error_line=12, stderr_tail="\n".join(f"line{i}" for i in range(60)))
    lint = GateReport(gate="lint:threejs", passed=False, findings=[GateFinding(gate="lint:threejs", severity=Severity.ERROR, target="src/parts/base.js",
                                                                                 message="document.createElement is forbidden", fix_hint="use vertex colours")])
    cb = "## Materials\nuse MeshStandardMaterial\n## Canvas textures are not available\nfillStyle getContext canvas null properties → procedural colours\n"
    rep = format_error_report(b, lint, cb)
    assert "src/parts/base.js:12" in rep and "line59" in rep and "line5\n" not in rep.split("traceback")[1][:20]
    assert "FIX: use vertex colours" in rep and "Canvas textures" in rep
    assert relevant_cookbook_section(cb, "totally unrelated words") == ""
    assert json.dumps(rep)  # serialisable
