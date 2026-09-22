"""Complexity-aware depth budgets and scoped baselines."""

from __future__ import annotations

from codeverse3d.contracts.common import Language
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.conventions import MAX_TRIS_OBJECT, to_snake
from codeverse3d.tracks.depth import (
    PartScope,
    depth_budget,
    interfaces_text,
    scope_groups,
    scoped_generation_enabled,
)

from .conftest import make_spec
from .fakes import FakeAgent, FakeRuntime, FakeServices


def _files_for(name):
    return [f"src/parts/{to_snake(name)}.py"]


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


def test_scoped_generation_can_be_switched_off(switch):
    switch("C3D_SCOPED_PARTS", "off")
    assert scoped_generation_enabled() is False
    switch("C3D_SCOPED_PARTS", "on")
    assert scoped_generation_enabled() is True
    switch("C3D_SCOPED_PARTS", None)
    assert scoped_generation_enabled() is True


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
    from codeverse3d.orchestrator import RunState
    from codeverse3d.proc import EventLog
    from codeverse3d.tracks.static_object import StaticObjectTrack
    from codeverse3d.workspace import Workspace

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
    monkeypatch.setenv("C3D_SCOPED_PARTS", "off")
    track, ctx = _ctx(tmp_path, big_plan(), settings)
    assert [t.label for t in track.baseline_tasks(ctx)] == ["baseline"]
    monkeypatch.delenv("C3D_SCOPED_PARTS")
    t2, c2 = _ctx(tmp_path / "ss", big_plan(), settings, agent_id="single-shot:fake:m")
    assert [t.label for t in t2.baseline_tasks(c2)] == ["baseline"]      # one envelope, no sessions
    t3, c3 = _ctx(tmp_path / "small", chair_plan, settings)
    assert [t.label for t in t3.baseline_tasks(c3)] == ["baseline"]      # 5 parts: one session is fine


def test_phases_run_in_order_and_a_failed_phase_does_not_kill_the_round(tmp_path, chair_plan, settings):
    """``run_generation_tasks`` is the only place that knows about phases."""
    from codeverse3d.tracks.generation import GenerationResult, GenerationTask
    from codeverse3d.tracks.steps import run_generation_tasks

    track, ctx = _ctx(tmp_path, chair_plan, settings)
    order: list[str] = []

    def fake_generate(ws, *, agent_id, task, **kw):
        order.append(task.label)
        return GenerationResult(ok=task.label != "b", label=task.label, notes="")

    import codeverse3d.tracks.common as common  # the one seam every stage generates through
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


def test_the_depth_templates_render_with_strict_undefined(tmp_path, settings):
    """StrictUndefined: a variable the template names and the track does not provide is a crash."""
    from codeverse3d.prompts import render
    from codeverse3d.tracks.prompting import base_prompt_context, scope_context

    track, ctx = _ctx(tmp_path, big_plan(), settings)
    scope = track.scopes(ctx)[0]
    part = render("tracks/generate_static_part.j2", **scope_context(ctx, scope, files=list(scope.files),
                                                                   expected_files=list(scope.files)))
    assert "Interfaces" in part and "DO NOT create or edit it" in part and scope.names[0] in part
    entry = ctx.runtime.expected_files(ctx.plan)[:1]
    asm = render("tracks/assemble_static.j2", **base_prompt_context(ctx, files=entry, expected_files=entry))
    assert "you own placement, not geometry" in asm and "check_connectivity" in asm
