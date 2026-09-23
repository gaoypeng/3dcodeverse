"""Complexity-aware depth budgets and scoped baselines."""

from __future__ import annotations

from codeverse3d.contracts.common import Language
from codeverse3d.conventions import to_snake
from codeverse3d.tracks.depth import (
    PartScope,
    interfaces_text,
    scope_groups,
)

from .conftest import make_spec
from .fakes import FakeAgent, FakeRuntime, FakeServices


def _files_for(name):
    return [f"src/parts/{to_snake(name)}.py"]


# ----------------------------------------------------------------------------- scoping
def test_scope_groups_partition_the_attachment_tree_and_name_the_outside_neighbours(chair_plan):
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
    # the interface table names only the neighbours outside the scope
    backleg = next(s for s in scopes if "BackLeg" in s.names)
    text = interfaces_text(chair_plan, backleg)
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "---" not in ln][1:]
    assert rows and all("Seat" in r for r in rows)     # BackLeg attaches to Seat, owned elsewhere
    inside = set(backleg.names)
    for row in rows:                                    # a neighbour is never a part of this scope
        assert row.split("|")[3].strip() not in inside
    empty = interfaces_text(chair_plan, PartScope(parts=tuple(chair_plan.parts), files=("x",)))
    assert "no parts outside this scope" in empty


# ----------------------------------------------------------------------------- the track wiring
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
