"""D70: one author for the whole world — every zone goes to ONE session whose window
scales with the zone count (2026-09-08; the fan-out control arm was removed 2026-09-21).

Measured under one fixed judge with codex:gpt-6-astra@low on both sides: a bare one-file
scene scored 0.894 (clockmaker) and 0.82 (boat) while the harness's round 0, its zones
written by separate sessions of at most two small zones, sat at 0.17 and 0.30-0.57.
"""
from __future__ import annotations

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.languages.scene_threejs import zone_file
from codeverse3d.orchestrator import RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene import ZONE_TIMEOUT_S, SceneTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeRuntime, FakeServices


def test_a_whole_world_batch_owns_every_zone_file_and_gets_a_window_per_zone(tmp_path, settings):
    base = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan = base.model_copy(update={"zones": [*base.zones, base.zones[0].model_copy(update={"name": "Extra"})]})
    ws = Workspace(tmp_path / "runs" / "s").create()
    track = SceneTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.SCENE_THREEJS))
    ctx = track.build_context(make_spec(Track.SCENE, Language.SCENE_THREEJS, max_minutes=600.0), ws, EventLog(ws.events_path), RunState())
    ctx.plan = plan
    whole = track._zone_task(ctx, list(plan.zones))
    assert whole.files_hint == [zone_file(z.name) for z in plan.zones] and whole.edit_only
    assert whole.timeout_s == ZONE_TIMEOUT_S * len(plan.zones)
    assert "you are its one author" in whole.prompt and "nothing floats" in whole.prompt
    assert f"window is {len(plan.zones)} zones' worth ({ZONE_TIMEOUT_S * len(plan.zones) // 60} min)" in whole.prompt
