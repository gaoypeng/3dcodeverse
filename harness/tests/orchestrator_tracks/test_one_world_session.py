"""D70: one author for the whole world — `CV3D_ONE_WORLD_SESSION` sends every zone to ONE
session whose window scales with the zone count (2026-09-08).

Measured under one fixed judge with codex:gpt-6-astra@low on both sides: a bare one-file
scene scored 0.894 (clockmaker) and 0.82 (boat) while the harness's round 0, its zones
written by separate sessions of at most two small zones, sat at 0.17 and 0.30-0.57.
"""
from __future__ import annotations

from codeverse.config import one_world_session_enabled
from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import ScenePlan
from codeverse.orchestrator import RunState
from codeverse.proc import EventLog
from codeverse.tracks.planner import plan_example
from codeverse.tracks.scene import ZONE_TIMEOUT_S, SceneTrack, zone_file
from codeverse.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeRuntime, FakeServices


def test_the_switch_is_read_at_call_time_and_off_by_default(monkeypatch):
    monkeypatch.delenv("CV3D_ONE_WORLD_SESSION", raising=False)
    assert one_world_session_enabled() is False      # D70: the loop-17 gain did not survive the fixed judge
    monkeypatch.setenv("CV3D_ONE_WORLD_SESSION", "on")
    assert one_world_session_enabled() is True
    monkeypatch.setenv("CV3D_ONE_WORLD_SESSION", "garbage")
    assert one_world_session_enabled() is False      # a typo in a bench command is the control, never a crash


def test_a_whole_world_batch_owns_every_zone_file_and_gets_a_window_per_zone(tmp_path, settings):
    base = ScenePlan.model_validate(plan_example(Track.SCENE))
    # three zones: a pair is then NOT the whole world (the example plan has two)
    plan = base.model_copy(update={"zones": [*base.zones, base.zones[0].model_copy(update={"name": "Extra"})]})
    ws = Workspace(tmp_path / "runs" / "s").create()
    track = SceneTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.SCENE_THREEJS))
    ctx = track.build_context(make_spec(Track.SCENE, Language.SCENE_THREEJS, max_minutes=600.0), ws, EventLog(ws.events_path), RunState())
    ctx.plan = plan
    whole = track._zone_task(ctx, list(plan.zones))
    pair = track._zone_task(ctx, list(plan.zones[:2]))
    assert whole.files_hint == [zone_file(z) for z in plan.zones] and whole.edit_only
    assert whole.timeout_s == ZONE_TIMEOUT_S * len(plan.zones) and pair.timeout_s == ZONE_TIMEOUT_S * 2
    assert "you are its one author" in whole.prompt and "nothing floats" in whole.prompt
    assert f"window is {len(plan.zones)} zones' worth ({ZONE_TIMEOUT_S * len(plan.zones) // 60} min)" in whole.prompt
    assert "one author" not in pair.prompt and "small neighbouring zones" in pair.prompt
