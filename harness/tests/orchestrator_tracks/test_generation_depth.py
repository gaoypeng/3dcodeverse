"""Complexity-aware depth budgets, scoped baselines, and the gated detail round."""

from __future__ import annotations

from codeverse.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    ImprovementItem,
    Judgment,
    Measurement,
    PartMeasure,
    Severity,
)
from codeverse.contracts.common import Language
from codeverse.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse.contracts.run import RoundRecord
from codeverse.conventions import MAX_TRIS_OBJECT, to_snake
from codeverse.orchestrator import (
    DETAIL_KIND,
    KIND_FOR_STRATEGY,
    REWRITE_KIND,
    RoundPolicy,
    StopPolicy,
    detail_blocked,
)
from codeverse.tracks.depth import (
    PartScope,
    depth_budget,
    interfaces_text,
    scope_groups,
    scoped_generation_enabled,
)
from codeverse.tracks.static_object import DRIFT_GATE, detail_instructions, drift_gate

from .conftest import make_spec
from .fakes import FakeAgent, FakeRuntime, FakeServices


def _files_for(name):
    return [f"src/parts/{to_snake(name)}.py"]


def _round(i: int, score: float | None, *, kind: str = "refine", gates=(), build_ok: bool = True) -> RoundRecord:
    j = Judgment(rubric="r", scores={}, overall=score, passed=bool(score and score >= 0.8)) if score is not None else None
    return RoundRecord(index=i, kind="baseline" if i == 0 else kind, judgment=j, gates=list(gates),
                       build=BuildResult(ok=build_ok, language="blender"), commit=f"c{i}")


def _gate_error() -> GateReport:
    return GateReport(gate="connectivity", passed=False, findings=[GateFinding(
        gate="connectivity", severity=Severity.ERROR, target="Seat", message="Seat floats 12 mm above FrontLeg")])


# ----------------------------------------------------------------------------- the triangle budget
def test_depth_budget_scales_with_the_plan_and_is_bounded(chair_plan):
    small = depth_budget(StaticPlan(object_name="Stool", summary="s", overall_bbox=chair_plan.overall_bbox,
                                    parts=chair_plan.parts[:1], acceptance=chair_plan.acceptance))
    big = depth_budget(chair_plan)
    assert big.n_units > small.n_units                       # 5 parts / 7 units vs 1
    assert big.target_tris > small.target_tris               # a bigger machine is allowed more
    assert small.target_tris >= 6_000 and big.max_tris <= MAX_TRIS_OBJECT
    assert small.min_tris < small.target_tris < small.max_tris
    assert 20 <= big.max_build_s <= 300 - 30
    one = PartPlan(name="Leg", role="leg", description="rod", bbox=BBox(center=(0, 0.2, 0), extents=(0.04, 0.4, 0.04)),
                   instances=4)
    plan = StaticPlan(object_name="X", summary="s", overall_bbox=BBox(center=(0, 0.2, 0), extents=(0.5, 0.4, 0.5)),
                      parts=[one], acceptance=[])
    assert depth_budget(plan).n_units == 4

    text = depth_budget(chair_plan).as_prompt()
    assert "DETAIL BUDGET" in text and "plan parts" in text
    assert f"{depth_budget(chair_plan).target_tris:,}" in text
    assert "not on new parts" in text


# ----------------------------------------------------------------------------- scoping
def test_scope_groups_partitions_along_the_attachment_tree(chair_plan):
    scopes = scope_groups(chair_plan, files_for=_files_for, max_groups=6, min_parts=3)
    assert len(scopes) >= 2
    names = [n for s in scopes for n in s.names]
    assert sorted(names) == sorted(p.name for p in chair_plan.parts)   # every part owned exactly once
    files = [f for s in scopes for f in s.files]
    assert len(files) == len(set(files))                               # file-disjoint: safe in parallel
    # Backrest and Armrest both hang off BackLeg → the subtree stays together
    by_part = {n: i for i, s in enumerate(scopes) for n in s.names}
    assert by_part["Backrest"] == by_part["BackLeg"] == by_part["Armrest"]
    assert scope_groups(chair_plan, files_for=_files_for, min_parts=99) == []       # small plan
    assert scope_groups(chair_plan, files_for=None, min_parts=3) == []              # no per-part files
    assert scope_groups(chair_plan, files_for=_files_for, max_groups=1, min_parts=3) == []


def test_interfaces_text_names_only_the_neighbours_outside_the_scope(chair_plan):
    scopes = scope_groups(chair_plan, files_for=_files_for, max_groups=6, min_parts=3)
    backleg = next(s for s in scopes if "BackLeg" in s.names)
    text = interfaces_text(chair_plan, backleg)
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "---" not in ln][1:]
    assert rows and all("Seat" in r for r in rows)     # BackLeg attaches to Seat, owned elsewhere
    inside = set(backleg.names)
    for row in rows:                                    # a neighbour is never a part of this scope
        assert row.split("|")[3].strip() not in inside
    empty = interfaces_text(chair_plan, PartScope(parts=tuple(chair_plan.parts), files=("x",)))
    assert "no parts outside this scope" in empty


def test_scoped_generation_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("CV3D_SCOPED_PARTS", "off")
    assert scoped_generation_enabled() is False
    monkeypatch.setenv("CV3D_SCOPED_PARTS", "on")
    assert scoped_generation_enabled() is True
    monkeypatch.delenv("CV3D_SCOPED_PARTS")
    assert scoped_generation_enabled() is True


# ----------------------------------------------------------------------------- the detail round
def test_kind_for_strategy_is_the_one_mapping():
    assert KIND_FOR_STRATEGY == {"same": "refine", "switch": REWRITE_KIND, "detail": DETAIL_KIND}


def test_detail_round_is_offered_only_on_a_clean_finished_structure():
    pol = RoundPolicy(detail_rounds=1, target=0.9, judge_model="gemini:gemini-3.1-pro-preview")
    clean = [_round(0, 0.60), _round(1, 0.61), _round(2, 0.615)]
    assert detail_blocked(clean, pol) == ""
    assert "gate error" in detail_blocked([_round(0, 0.6), _round(1, 0.61, gates=[_gate_error()])], pol)
    assert "did not build" in detail_blocked([_round(0, 0.6), _round(1, 0.6, build_ok=False)], pol)
    assert "detail floor" in detail_blocked([_round(0, 0.2), _round(1, 0.2)], pol)
    assert "not judged" in detail_blocked([_round(0, 0.6), _round(1, None)], pol)
    spent = [*clean, _round(3, 0.62, kind=DETAIL_KIND)]
    assert "already spent" in detail_blocked(spent, pol)
    assert "disabled" in detail_blocked(clean, RoundPolicy(detail_rounds=0))


def test_a_plateau_on_a_clean_object_becomes_one_detail_round():
    pol = RoundPolicy(max_rounds=6, target=0.9, plateau_window=2, min_delta=0.02, marginal_from_round=99,
                      detail_rounds=1, judge_model="gemini:gemini-3.1-pro-preview")
    flat = [_round(0, 0.60), _round(1, 0.605), _round(2, 0.61)]
    d = StopPolicy(pol).evaluate(flat)
    assert (d.reason, d.strategy, d.stop) == ("continue", "detail", False)
    # ...and exactly once: after the detail round the same history stops as it meant to
    after = [*flat, _round(3, 0.62, kind=DETAIL_KIND)]
    assert StopPolicy(pol).evaluate(after).reason == "plateau"
    # a regression is never converted: repair money is not detail money
    reg = [_round(0, 0.70), _round(1, 0.50), _round(2, 0.48, kind=REWRITE_KIND)]
    assert StopPolicy(pol).evaluate(reg).reason == "regression"
    # detail_rounds=0 (every track that cannot build one) leaves the money stops untouched
    assert StopPolicy(RoundPolicy(**{**pol.__dict__, "detail_rounds": 0})).evaluate(flat).reason == "plateau"


def test_detail_instructions_take_the_judges_detail_asks_then_the_standing_vocabulary():
    j = Judgment(rubric="r", scores={}, overall=0.6, passed=False, improvement_plan=[
        ImprovementItem(target="Seat", kind="material", instruction="give the seat an oiled-oak colour", priority=1),
        ImprovementItem(target="FrontLeg", kind="assembly", instruction="move the leg 12 mm in", priority=1),
    ])
    lines = detail_instructions(RoundRecord(index=1, kind="refine", judgment=j), max_lines=8)
    assert any("oiled-oak" in ln for ln in lines)
    assert not any("move the leg" in ln for ln in lines)     # assembly work is the repair loop's
    assert any("Bevel or chamfer" in ln for ln in lines)     # the standing vocabulary is always there


# ----------------------------------------------------------------------------- the drift gate
def _measurement(parts, *, extents=(1.0, 1.0, 1.0), tris=1000) -> Measurement:
    return Measurement(bbox_min=(0, 0, 0), bbox_max=extents, extents=extents, center=(0, 0, 0),
                       tri_count=tris, n_meshes=len(parts), n_islands=len(parts),
                       parts=[PartMeasure(name=n, bbox_min=lo, bbox_max=hi, tri_count=10, islands=1) for n, lo, hi in parts])


def test_drift_gate_allows_detail_only_changes():
    before = _measurement([("Seat", (0, 0, 0), (0.4, 0.04, 0.4))], tris=1000)
    after = _measurement([("Seat", (0, 0, 0), (0.4, 0.04, 0.4))], tris=4200)
    g = drift_gate(before, after, tol_m=0.005)
    assert g.passed and g.gate == DRIFT_GATE
    assert "+3,200 triangles" in g.findings[0].message

    after = _measurement([
        ("Seat", (0, 0, 0), (0.4, 0.04, 0.4)),
        ("Bolt", (0, 0, 0), (0.01, 0.01, 0.01)),
    ])
    g = drift_gate(before, after, tol_m=0.005)
    assert g.passed and any(f.severity is Severity.WARN and "new top-level part" in f.message for f in g.findings)
    assert drift_gate(None, _measurement([]), tol_m=0.005).passed


def test_drift_gate_fails_when_the_detail_round_moved_something():
    before = _measurement([("Seat", (0, 0, 0), (0.4, 0.04, 0.4))])
    cases = [
        ([("Seat", (0, 0, 0), (0.4, 0.04, 0.4))], (1.2, 1.0, 1.0), "overall x extent"),
        ([("Seat", (0, 0, 0), (0.4, 0.04, 0.4))], (1.0, 1.2, 1.0), "overall z extent"),
        ([("Seat", (0.02, 0, 0), (0.42, 0.04, 0.4))], (1.0, 1.0, 1.0), "moved 20.0 mm"),
        ([], (1.0, 1.0, 1.0), "disappeared"),
    ]
    for after_parts, extents, needle in cases:
        g = drift_gate(before, _measurement(after_parts, extents=extents), tol_m=0.005, language="blender")
        assert not g.passed and any(needle in f.message for f in g.findings)
        assert all(f.data.get("kind") == "detail_drift" for f in g.findings)


# ----------------------------------------------------------------------------- the track wiring
def big_plan(n: int = 11) -> StaticPlan:
    """A plan past the scoping threshold, with a real attachment tree (Body → N children)."""
    parts = [PartPlan(name="Body", role="main mass", description="the shell",
                      bbox=BBox(center=(0, 0.5, 0), extents=(0.6, 1.0, 0.4)))]
    for i in range(n - 1):
        parts.append(PartPlan(name=f"Fitting{i}", role=f"fitting {i}", description="a fitting with a bevel",
                              bbox=BBox(center=(0.05 * i, 0.2 + 0.05 * i, 0.1), extents=(0.05, 0.05, 0.05)),
                              attach_to="Body"))
    return StaticPlan(object_name="Machine", summary="A machine, 0.6 x 0.4 x 1.0 m.",
                      overall_bbox=BBox(center=(0, 0.5, 0), extents=(0.6, 1.0, 0.4)), parts=parts,
                      acceptance=[])


def _ctx(tmp_path, plan, settings, *, language=Language.THREEJS, agent_id="fake-agent:m"):
    from codeverse.orchestrator import RunState
    from codeverse.proc import EventLog
    from codeverse.tracks.static_object import StaticObjectTrack
    from codeverse.workspace import Workspace

    ws = Workspace(tmp_path / "runs" / "d").create()
    track = StaticObjectTrack(services=FakeServices(), agent=FakeAgent(lambda job: None), settings=settings,
                              runtime=FakeRuntime(language))
    spec = make_spec(language=language, generator=agent_id)
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState(slug="d"))
    ctx.plan = plan
    return track, ctx


def test_scoped_baseline_fans_out_and_the_assembly_session_owns_the_entry(tmp_path, settings):
    track, ctx = _ctx(tmp_path, big_plan(), settings)
    tasks = track.baseline_tasks(ctx)
    assert len(tasks) >= 3
    parts, assemble = tasks[:-1], tasks[-1]
    assert {t.phase for t in parts} == {0} and assemble.phase == 1     # assembly sees the part files
    assert assemble.label == "assemble" and assemble.files_hint == ["src/object.js"]
    owned = [f for t in parts for f in t.files_hint]
    assert len(owned) == len(set(owned)) and "src/object.js" not in owned   # file-disjoint, no entry
    assert all(t.kind == "baseline" and t.round == 0 for t in tasks)
    # every scoped prompt carries ITS parts, the interface table and the detail budget — not the plan
    one = parts[0]
    assert "Interfaces" in one.prompt and "DETAIL BUDGET" in one.prompt
    assert "FILES YOU MAY WRITE" in one.prompt


def test_scoping_off_single_shot_or_a_small_plan_falls_back_to_one_task(tmp_path, chair_plan, settings, monkeypatch):
    monkeypatch.setenv("CV3D_SCOPED_PARTS", "off")
    track, ctx = _ctx(tmp_path, big_plan(), settings)
    assert [t.label for t in track.baseline_tasks(ctx)] == ["baseline"]
    monkeypatch.delenv("CV3D_SCOPED_PARTS")
    t2, c2 = _ctx(tmp_path / "ss", big_plan(), settings, agent_id="single-shot:fake:m")
    assert [t.label for t in t2.baseline_tasks(c2)] == ["baseline"]      # one envelope, no sessions
    t3, c3 = _ctx(tmp_path / "small", chair_plan, settings)
    assert [t.label for t in t3.baseline_tasks(c3)] == ["baseline"]      # 5 parts: one session is fine


def test_detail_tasks_are_scoped_frozen_and_arm_the_drift_gate(tmp_path, settings):
    track, ctx = _ctx(tmp_path, big_plan(), settings)
    last = _round(2, 0.62)
    last.measurement = _measurement([("Seat", (0, 0, 0), (0.4, 0.04, 0.4))])
    tasks, lines = track.detail_tasks(ctx, last, 3)
    assert tasks and lines
    assert all(t.kind == DETAIL_KIND and t.round == 3 for t in tasks)
    assert ctx.extra["detail_round"] == 3 and ctx.extra["detail_baseline"] is last.measurement
    body = tasks[0].prompt
    assert "Do not move, resize, rename, add or delete any part" in body
    assert "DETAIL BUDGET" in body
    # the gate the prompt promises actually fires from ctx.extra
    pipe = track.make_pipeline()
    moved = _measurement([("Seat", (0.03, 0, 0), (0.43, 0.04, 0.4))])
    gates = pipe.gates(ctx, 3, BuildResult(ok=True, language="blender", glb_path=""), moved)
    drift = [g for g in gates if g.gate == DRIFT_GATE]
    assert drift and not drift[0].passed
    # ...and not on any other round
    assert [g for g in pipe.gates(ctx, 4, BuildResult(ok=True, language="blender", glb_path=""), moved)
            if g.gate == DRIFT_GATE] == []


def test_phases_run_in_order_and_a_failed_phase_does_not_kill_the_round(tmp_path, chair_plan, settings):
    """``run_generation_tasks`` is the only place that knows about phases."""
    from codeverse.tracks.generation import GenerationResult, GenerationTask
    from codeverse.tracks.steps import run_generation_tasks

    track, ctx = _ctx(tmp_path, chair_plan, settings)
    order: list[str] = []

    def fake_generate(ws, *, agent_id, task, **kw):
        order.append(task.label)
        return GenerationResult(ok=task.label != "b", label=task.label, notes="")

    import codeverse.tracks.common as common  # the one seam every stage generates through
    orig = common.generate
    common.generate = fake_generate
    try:
        tasks = [GenerationTask(label="a", prompt="p", phase=0), GenerationTask(label="b", prompt="p", phase=0),
                 GenerationTask(label="z", prompt="p", phase=1)]
        out = run_generation_tasks(ctx, tasks)
    finally:
        common.generate = orig
    assert order.index("z") == 2                       # phase 1 ran last
    assert [r.label for r in out] == ["a", "b", "z"]   # results keep the caller's order
    assert [r.ok for r in out] == [True, False, True]


def test_the_three_depth_templates_render_with_strict_undefined(tmp_path, settings):
    """StrictUndefined: a variable the template names and the track does not provide is a crash."""
    from codeverse.prompts import render
    from codeverse.tracks.prompting import base_prompt_context, judge_digest, scope_context

    track, ctx = _ctx(tmp_path, big_plan(), settings)
    scope = track.scopes(ctx)[0]
    part = render("tracks/generate_static_part.j2", **scope_context(ctx, scope, files=list(scope.files),
                                                                   expected_files=list(scope.files)))
    assert "Interfaces" in part and "DO NOT create or edit it" in part and scope.names[0] in part
    entry = track.entry_files(ctx)
    asm = render("tracks/assemble_static.j2", **base_prompt_context(ctx, files=entry, expected_files=entry))
    assert "you own placement, not geometry" in asm and "check_connectivity" in asm
    det = render("tracks/detail_object.j2", **base_prompt_context(
        ctx, round_index=3, tasks=["Body: bevel the shell"], files=entry,
        judge_summary=judge_digest(_round(2, 0.6)), current_files={}))
    assert "THE RULE" in det and "Do not move, resize, rename" in det


def test_a_full_run_spends_exactly_one_detail_round_after_the_plateau(tmp_path, chair_plan, settings):
    """End to end on fakes: flat scores → plateau → ONE round of kind 'detail', gated by
    detail_drift, and never a second one."""
    from codeverse.contracts.run import RunStatus
    from codeverse.tracks.static_object import DRIFT_GATE as _DRIFT
    from codeverse.tracks.static_object import StaticObjectTrack
    from codeverse.workspace import Workspace

    from .fakes import FakeJudge
    from .test_fix_batch2 import _planner, _writer

    spec = make_spec(max_rounds=5)
    ws = Workspace(tmp_path / "runs" / "detail")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,), targets=("Seat",)),
                              agent=FakeAgent(_writer), planner_model=_planner(chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    kinds = [r.kind for r in rec.rounds]
    assert kinds.count(DETAIL_KIND) == 1, kinds
    detail = next(r for r in rec.rounds if r.kind == DETAIL_KIND)
    assert any(g.gate == _DRIFT for g in detail.gates)      # the promise is checked in code
    assert rec.status in (RunStatus.PLATEAU, RunStatus.PASSED)
    events = [__import__("json").loads(line) for line in ws.events_path.read_text().splitlines()]
    assert any(e["event"] == "detail.planned" for e in events)
    assert any(e["event"] == "refine.planned" and e.get("strategy") == "detail" for e in events)
