"""D110 baseline-first: ONE session drafts the whole object, ONE session refines it in place per
round, gate ERRORs first — a part still the skeleton's placeholder before anything else."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.artifacts import (
    PLACEHOLDER_PART,
    BuildResult,
    GateFinding,
    GateReport,
    ImprovementItem,
    Judgment,
    Severity,
)
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ArticulatedPlan
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.orchestrator import RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks.articulated_object import ArticulatedObjectTrack
from codeverse3d.tracks.generation import session_effort
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace

from .conftest import make_spec
from .fakes import FakeAgent, FakeJudge, FakeRuntime, FakeServices, _planner
from .test_write_scope import _big_plan


def _ctx(tmp_path, settings, language: Language, plan):
    ws = Workspace(tmp_path / "runs" / "d").create()
    track = StaticObjectTrack(services=FakeServices(), agent=FakeAgent(lambda job, ws: None), settings=settings,
                              runtime=FakeRuntime(language))
    ctx = track.build_context(make_spec(language=language, generator="fake-agent:m"), ws,
                              EventLog(ws.events_path), RunState(slug="d"))
    ctx.plan = plan
    return track, ctx


@pytest.mark.parametrize("language", [Language.THREEJS, Language.BLENDER, Language.CADQUERY])
def test_round0_is_one_session_owning_every_part_file_and_the_entry(tmp_path, settings, language):
    """An 8-part plan was split into 3-5 serial scope sessions + an assemble session before D110."""
    track, ctx = _ctx(tmp_path, settings, language, _big_plan(10))
    (draft,) = track.baseline_tasks(ctx)
    files = ctx.runtime.expected_files(ctx.plan)
    assert draft.label == "baseline" and draft.kind == "baseline" and draft.round == 0
    assert draft.files_hint == files and draft.owns_entry and not draft.edit_only
    if language is not Language.CADQUERY:   # one file per part + the entry
        assert len(files) == 1 + len(ctx.plan.parts)
    # the object tasks ask for no effort: the session runs at the backend's default (agy: high)
    assert draft.thinking == "" and session_effort(draft) is None
    assert "The whole object first" in draft.prompt and "placeholder_part" in draft.prompt
    assert "targeted edits" in draft.prompt


def _last(plan) -> RoundRecord:
    body, fitting = plan.parts[0].name, plan.parts[3].name
    gates = [
        GateReport.of("connectivity", [GateFinding(gate="connectivity", severity=Severity.ERROR, target=body,
                                                   message=f"{body} floats 12 mm", data={"kind": "floating"})]),
        GateReport.of("contract", [GateFinding(gate="contract", severity=Severity.ERROR, target=fitting,
                                               message=f"plan part '{fitting}' is still the skeleton's placeholder box",
                                               data={"kind": PLACEHOLDER_PART})]),
    ]
    j = Judgment(rubric="r", scores={}, overall=0.55, passed=False, improvement_plan=[
        ImprovementItem(target=plan.parts[1].name, kind="geometry", instruction="add the bolt heads", priority=1),
        ImprovementItem(target=plan.parts[2].name, kind="material", instruction="darker steel", priority=2)])
    return RoundRecord(index=0, kind="baseline", judgment=j, gates=gates, commit="c0",
                       build=BuildResult(ok=True, language="threejs"))


def test_refine_round_is_one_whole_object_session_with_placeholder_errors_first(tmp_path, settings):
    """Four targets in four different part files used to fan out into four sessions."""
    track, ctx = _ctx(tmp_path, settings, Language.THREEJS, _big_plan(8))
    last = _last(ctx.plan)
    tasks, lines = track.refine_tasks(ctx, last)
    (refine,) = tasks
    assert refine.label == "refine" and refine.kind == "refine" and refine.round == 1
    assert refine.files_hint == ctx.runtime.expected_files(ctx.plan) and not refine.edit_only
    assert refine.owns_entry and session_effort(refine) is None
    # gate ERRORs (the placeholder before the floating part) → judge plan, in that order
    assert "placeholder" in lines[0] and "floats" in lines[1]
    assert "bolt heads" in lines[2] and "darker steel" in lines[3]
    body = refine.prompt
    assert body.index("placeholder box") < body.index("floats 12 mm") < body.index("bolt heads")
    assert "Edit in place" in body and "never rewrite a whole file" in body
    assert "Measured vs planned" in body


def test_articulated_runs_one_draft_and_one_refine_session_end_to_end(tmp_path, settings):
    """urdf_blender inherits the shape: one ``baseline`` session, then one ``refine`` session per round."""
    plan = ArticulatedPlan.model_validate(plan_example(Track.ARTICULATED_OBJECT))
    agent = FakeAgent(lambda job, ws: {"src/model.py": f"import bpy  # {job.label}\n",
                                       "src/robot.urdf": "<robot name='x'/>\n"})
    judge = FakeJudge(scores=(0.6, 0.8), targets=tuple(p.name for p in plan.parts[:2]))
    track = ArticulatedObjectTrack(services=FakeServices(), judge=judge, agent=agent,
                                   planner_model=_planner(plan.model_dump(mode="json")), settings=settings,
                                   runtime=FakeRuntime(Language.URDF_BLENDER))
    rec = track.run(make_spec(Track.ARTICULATED_OBJECT, Language.URDF_BLENDER, max_rounds=1),
                    Workspace(tmp_path / "runs" / "art"))
    assert [r.kind for r in rec.rounds] == ["baseline", "refine"]
    sessions = [j.label for j in agent.jobs]
    assert sessions == ["baseline", "refine"], sessions
    assert all(j.effort is None for j in agent.jobs)   # the backend's default effort (agy: high)


def test_single_shot_refine_is_fenced_to_the_files_it_is_shown(tmp_path, settings):
    """A single-shot call has no read tool and sees only the inlined files: told "the whole object"
    it could rewrite a part file it was never shown, so it keeps the files its tasks name."""
    track, ctx = _ctx(tmp_path, settings, Language.THREEJS, _big_plan(8))
    ctx.agent_id = "single-shot:gemini:x"
    (refine,), _ = track.refine_tasks(ctx, _last(ctx.plan))
    named = {f for p in ctx.plan.parts[:4] for f in ctx.runtime.files_for(ctx.plan, p.name)}
    assert set(refine.files_hint) == named and refine.edit_only and refine.owns_entry
    assert len(refine.files_hint) < len(ctx.runtime.expected_files(ctx.plan))
    assert "Change only these files" in refine.prompt and "the whole object;" not in refine.prompt
    assert refine.timeout_s is None   # a single-shot call's own wait applies


def test_refine_session_ends_before_the_run_clock_so_the_round_is_judged(tmp_path, settings):
    """The baseline-first smoke's blender refine started at 25.8 of 60 min with a 30-min window: the
    watchdog let the busy session run its 5-min grace on top, the run clock killed it at 60.0 min and
    the round was dropped unbuilt and unjudged ($4.63).  The window now ends reserve + grace early."""
    from codeverse3d.agents.cli_common import HARD_GRACE_S
    from codeverse3d.tracks.static_object import OBJECT_ROUND_RESERVE_S, REFINE_MIN_WINDOW_S

    track, ctx = _ctx(tmp_path, settings, Language.BLENDER, _big_plan(8))
    hard_s = ctx.budget.hard_minutes * 60.0
    (early,), _ = track.refine_tasks(ctx, _last(ctx.plan))
    assert early.timeout_s == min(ctx.settings.limits.agent_timeout_s,
                                  int(hard_s - OBJECT_ROUND_RESERVE_S - HARD_GRACE_S))
    ctx.budget._active_s = hard_s - 34.2 * 60          # 34.2 min left, as in the smoke
    (late,), _ = track.refine_tasks(ctx, _last(ctx.plan))
    left = ctx.budget.hard_minutes * 60.0 - ctx.budget.elapsed_minutes() * 60.0
    assert late.timeout_s + HARD_GRACE_S <= left - OBJECT_ROUND_RESERVE_S + 1
    assert late.timeout_s > 20 * 60                      # still a real session
    ctx.budget._active_s = hard_s - 60                   # one minute left: the floor, the run clock decides
    (last_minute,), _ = track.refine_tasks(ctx, _last(ctx.plan))
    assert last_minute.timeout_s == REFINE_MIN_WINDOW_S
