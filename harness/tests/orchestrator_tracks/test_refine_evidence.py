"""Scene refinement carries gate evidence and rebuilds failed source files."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import BuildResult, GateReport
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.orchestrator import RunState, TaskGroup, build_refine_instructions
from codeverse3d.proc import EventLog
from codeverse3d.spatial.scene_placement import placement_findings
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene import SceneTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeRuntime, FakeServices
from tests.orchestrator_tracks.test_env_stage_honest import LANGUAGES, _ctx
from tests.scene_gates.test_placement import _row, _table


def test_the_refine_prompt_states_each_failed_row_its_gap_and_tolerance(tmp_path, settings):
    track, ctx = _ctx(tmp_path, settings, LANGUAGES[0])
    ctx.tool_cards = "(tools)"
    gate = placement_findings(_table(*[_row(f"Boulder{i}", zone="FernHollow", gap=g) for i, g in enumerate((0.048, 0.046, 0.041))]),
                              indoor=True)
    tasks = build_refine_instructions(None, [gate], [], ctx.plan, max_tasks=6)
    group = TaskGroup(label="all", tasks=tasks, targets=sorted({t.target for t in tasks}), files=[])
    prompt = track._refine_task(ctx, group, RoundRecord(index=0, kind="baseline"), 1, parallel=False).prompt
    for name, gap in (("FernHollow/Boulder0", "0.048"), ("FernHollow/Boulder1", "0.046"), ("FernHollow/Boulder2", "0.041")):
        assert f"{name} is floating {gap} m above Ground (touches nothing; indoor tolerance 0.02 m" in prompt, name
    assert "`artifacts/scene_probe.json` is the probe driver's raw dump" in prompt
    assert "`check_placement` returns the placement rows" in prompt


def test_scene_refine_emits_rebuild_task_when_build_failed(tmp_path, settings):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS)
    ws = Workspace(tmp_path / "runs" / "r")
    ws.create()
    track = SceneTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(Language.SCENE_THREEJS))
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState())
    ctx.plan = plan
    (ws.src / "zones").mkdir(parents=True, exist_ok=True)
    (ws.src / "zones" / "quay.js").write_text("// broken\n")
    last = RoundRecord(index=0, kind="baseline",
                       build=BuildResult(ok=False, language="scene_threejs", error_type="ProbeError",
                                         error_message="Cannot read properties of undefined (reading 'update')",
                                         error_file="src/zones/quay.js"),
                       gates=[GateReport(gate="lint:scene_threejs", passed=True)])
    tasks, instructions = track.refine_tasks(ctx, last)
    assert len(tasks) == 1 and tasks[0].kind == "rebuild"
    assert tasks[0].files_hint == ["src/zones/quay.js"]
    assert "ProbeError" in tasks[0].prompt and "Cannot read properties" in tasks[0].prompt
    assert instructions and "rebuild" in instructions[0]
