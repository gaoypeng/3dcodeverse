"""generation (envelope parsing, single-shot, agent path), planner, repair loop."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan, StaticPlan
from codeverse3d.cost.instrument import metered_chat_model, run_ledger
from codeverse3d.cost.ledger import load_ledger
from codeverse3d.orchestrator import BudgetGuard, RoundPolicy, RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks.common import RunContext
from codeverse3d.tracks.generation import (
    SINGLE_SHOT_FORMAT,
    GenerationError,
    GenerationTask,
    MultiFileParseError,
    generate,
    parse_multifile,
    safe_relpath,
    write_files,
)
from codeverse3d.tracks.planner import (
    ensure_acceptance,
    plan,
    plan_example,
)
from codeverse3d.tracks.repair import (
    build_with_repair,
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


def test_parse_multifile_blocks_fences_fallback_and_errors():
    blocks = ("Here you go.\n=== FILE: src/object.js ===\n```js\nexport const A=1;\n```\n=== END FILE ===\n"
              "=== FILE: ./src/parts/seat.js ===\nexport const B=2;\n=== END FILE ===\n")
    assert parse_multifile(blocks) == {"src/object.js": "export const A=1;", "src/parts/seat.js": "export const B=2;"}
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
    assert res.usage.cost_usd == pytest.approx(0.002)
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
    model = metered_chat_model(FakeChatModel(lambda req: answers.pop(0)))
    events = EventLog(tmp_ws.events_path)
    budget = BudgetGuard(spec.budget)
    with run_ledger(tmp_ws.root):
        p = plan(spec, "fake:planner", StaticPlan, tmp_ws, model=model, events=events, budget=budget)
    assert isinstance(p, StaticPlan) and p.object_name == "DiningChair" and tmp_ws.plan_path.is_file()
    assert len(model.requests) == 2 and "failed validation" in model.requests[1].messages[-1].text
    assert model.requests[0].response_schema is not None and "PascalCase" in model.requests[0].system
    from codeverse3d.tracks.planner import PLAN_MAX_WAIT_S, plan_wait_s

    assert all(PLAN_MAX_WAIT_S <= r.max_wait_s <= plan_wait_s(r.max_output_tokens) for r in model.requests)
    # deterministic acceptance items from constraints were appended
    texts = " ".join(a.text for a in p.acceptance)
    assert "height = 0.820" in texts and "Includes: armrests" in texts
    assert sum(r.cost_usd for r in load_ledger(tmp_ws.root)) == pytest.approx(0.004)  # both calls, paid
    kinds = [e["event"] for e in events.read()]
    assert "plan.invalid" in kinds and "plan.done" in kinds


def test_ensure_acceptance_is_idempotent():
    p = StaticPlan.model_validate(_valid_plan_dict())
    spec = make_spec()
    n1 = len(ensure_acceptance(p, spec).acceptance)
    n2 = len(ensure_acceptance(p, spec).acceptance)
    assert n1 == n2 and any(a.how == "measure" for a in p.acceptance)
    # an object plan's own items keep their priority: there IS a measurement pass to settle them
    assert all(a.priority == "must" for a in p.acceptance)


def test_scene_plan_items_are_advisory_and_only_the_spec_must_haves_gate():
    from codeverse3d.contracts.plan import AcceptanceItem

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
    assert out.usage.cost_usd == pytest.approx(0.004)


def test_repair_cookbook_sections_are_the_prompt_sections():
    """Repair splits the cookbook with ``prompts.sections`` (its own ``^##+`` regex went,
    identical on all 7 shipped cookbooks): a ``## `` comment inside a fence is code, not
    a heading that cuts the recipe in half."""
    cb = ("## Hinges\nhinge pivot axis revolute\n```python\n## pivot axis note\nhinge = 1\n```\n"
          "## Materials\nroughness metalness\n")
    got = relevant_cookbook_section(cb, "hinge pivot axis wrong")
    assert got.startswith("## Hinges") and "hinge = 1" in got and "Materials" not in got

def test_repair_rel_strips_a_prefix_not_a_character_set(tmp_ws):
    """B14: ``lstrip("./")`` turned ``../x`` into ``x`` and ``.env.js`` into ``env.js``."""
    from types import SimpleNamespace

    from codeverse3d.tracks.repair import _rel

    ctx = SimpleNamespace(ws=tmp_ws)
    assert _rel(ctx, str(tmp_ws.root / "src" / "a.js")) == "src/a.js"
    assert _rel(ctx, "./src/.env.js") == "src/.env.js"
    assert _rel(ctx, "src/parts/seat.js") == "src/parts/seat.js"
    assert _rel(ctx, "../outside.js") == "" and _rel(ctx, "/etc/passwd") == ""
