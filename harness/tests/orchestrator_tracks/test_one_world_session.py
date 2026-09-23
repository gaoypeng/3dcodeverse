"""D70: every zone goes to ONE session whose window scales with the zone count."""
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
    assert f"This session covers {len(plan.zones)} zones:" in whole.prompt and " min)" not in whole.prompt   # no time window
