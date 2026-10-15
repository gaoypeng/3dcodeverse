"""2026-10-15, iteration 2 of the argon loop: two Blender scenes shipped three EMPTY zones (0.18).

A runaway single-shot asset call held the asset stage for ~47 minutes; the zone stage then started
0.4 min before the scene track's soft cap and its agent session was clipped to the 180 s floor; it
read files until it was killed and wrote no zone; ``zones.done`` listed them failed and nothing acted;
refine spent its window on asset files.  Three fixes, pinned here:

* a single-shot call is bounded by its task's own window (never below ``SINGLE_SHOT_MIN_WAIT_S``);
* the zone window comes from the HARD budget minus round 0's reserve, not the soft cap;
* every zone the session left unwritten gets one single-shot recovery before assembly.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from codeverse3d.contracts.agent import FileChange
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.models.retry import RETRY_DEADLINE_S
from codeverse3d.orchestrator import RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks import scene as scene_mod
from codeverse3d.tracks.generation import (
    SINGLE_SHOT_MIN_WAIT_S,
    SINGLE_SHOT_PREFIX,
    GenerationResult,
    single_shot_wait_s,
)
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene import (
    ROUND0_RESERVE_S,
    ZONE_MIN_S,
    ZONE_TIMEOUT_S,
    SceneTrack,
    zone_window_s,
)
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeRuntime, FakeServices


def test_a_single_shot_call_waits_for_its_tasks_window_never_below_the_floor_never_past_the_ceiling():
    assert single_shot_wait_s(None, RETRY_DEADLINE_S) == RETRY_DEADLINE_S           # object tracks: unchanged
    assert single_shot_wait_s(420, RETRY_DEADLINE_S) == 420.0                       # an asset task: the runaway is cut
    assert single_shot_wait_s(120, RETRY_DEADLINE_S) == SINGLE_SHOT_MIN_WAIT_S      # a degraded stage's budget floor
    assert single_shot_wait_s(900, RETRY_DEADLINE_S) == 900.0                       # a refine window
    assert single_shot_wait_s(RETRY_DEADLINE_S * 3, RETRY_DEADLINE_S) == RETRY_DEADLINE_S


def _ctx(tmp_path, settings, *, max_minutes: float = 90.0):
    base = ScenePlan.model_validate(plan_example(Track.SCENE))
    ws = Workspace(tmp_path / "runs" / "s").create()
    track = SceneTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.SCENE_THREEJS))
    ctx = track.build_context(make_spec(Track.SCENE, Language.SCENE_THREEJS, max_minutes=max_minutes), ws,
                              EventLog(ws.events_path), RunState())
    ctx.plan = base
    return track, ctx


def test_the_zone_window_comes_from_the_hard_budget_minus_round_zeros_reserve(tmp_path, settings, monkeypatch):
    _, ctx = _ctx(tmp_path, settings)
    soft_cap = ctx.budget.soft_minutes()
    # the iteration-2 case: 0.4 min before the soft cap — the old window was the 180 s floor
    monkeypatch.setattr(ctx.budget, "elapsed_minutes", lambda: soft_cap - 0.4)
    assert zone_window_s(ctx, 3) == ZONE_TIMEOUT_S * 3, "41 minutes of hard budget left: the full window"
    # near the hard ceiling the window shrinks to what round 0 leaves, never below the floor
    monkeypatch.setattr(ctx.budget, "elapsed_minutes", lambda: ctx.budget.hard_minutes - 15.0)
    assert zone_window_s(ctx, 3) == int(15.0 * 60.0 - ROUND0_RESERVE_S)
    monkeypatch.setattr(ctx.budget, "elapsed_minutes", lambda: ctx.budget.hard_minutes - 1.0)
    assert zone_window_s(ctx, 3) == ZONE_MIN_S


def test_zones_the_session_left_unwritten_are_recovered_single_shot_each(tmp_path, settings, monkeypatch):
    track, ctx = _ctx(tmp_path, settings)
    plan: ScenePlan = ctx.plan  # type: ignore[assignment]
    ctx.runtime.skeleton(ctx.ws, plan)
    names = [z.name for z in plan.zones]
    first, rest = names[0], names[1:]
    calls: list[tuple[str, bool, float | None]] = []

    def fake_generate_for(gen: Any, task: Any) -> GenerationResult:
        calls.append((task.label, bool(gen.single_shot), task.timeout_s))
        if task.label.endswith(".recover"):
            rel = task.files_hint[0]
            (ctx.ws.root / rel).write_text("export function build(ctx) { return new ctx.THREE.Group(); }\n")
            return GenerationResult(ok=True, files_changed=[FileChange(path=rel, status="modified")], label=task.label)
        # the batched session: it wrote the FIRST zone only, then its window ran out
        rel = ctx.runtime.zone_file(first)
        (ctx.ws.root / rel).write_text("export function build(ctx) { return new ctx.THREE.Group(); }\n")
        return GenerationResult(ok=True, files_changed=[FileChange(path=rel, status="modified")], label=task.label,
                                notes="timeout")

    # a real run's planner is an API model, so its single-shot context exists; the fake services
    # expose no chat model (single_shot_ctx would fall back to the agent path), so hand one in
    ctx.extra["_single_shot_ctx"] = replace(ctx, agent_id=SINGLE_SHOT_PREFIX + "fake:planner", model=object(), agent=None)
    monkeypatch.setattr(scene_mod, "generate_for", fake_generate_for)
    out = track._zones_stage(ctx)
    assert all(out[n]["ok"] for n in names), out
    recovered = [c for c in calls if c[0].endswith(".recover")]
    assert len(recovered) == len(rest), calls
    assert all(single for _, single, _ in recovered), "a recovery is single-shot: it cannot spend its window reading"
    assert all(t is not None and t >= ZONE_MIN_S for _, _, t in recovered)
    kinds = [e.get("event") for e in ctx.events.read()] if hasattr(ctx.events, "read") else []
    if kinds:
        assert "zones.recover" in kinds and "zones.recovered" in kinds


def test_a_session_that_wrote_every_zone_triggers_no_recovery(tmp_path, settings, monkeypatch):
    track, ctx = _ctx(tmp_path, settings)
    plan: ScenePlan = ctx.plan  # type: ignore[assignment]
    ctx.runtime.skeleton(ctx.ws, plan)
    labels: list[str] = []

    def fake_generate_for(gen: Any, task: Any) -> GenerationResult:
        labels.append(task.label)
        changed = []
        for rel in task.files_hint:
            (ctx.ws.root / rel).write_text("export function build(ctx) { return new ctx.THREE.Group(); }\n")
            changed.append(FileChange(path=rel, status="modified"))
        return GenerationResult(ok=True, files_changed=changed, label=task.label)

    monkeypatch.setattr(scene_mod, "generate_for", fake_generate_for)
    out = track._zones_stage(ctx)
    assert all(v["ok"] for v in out.values()) and not any(lbl.endswith(".recover") for lbl in labels), labels
