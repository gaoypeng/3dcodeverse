"""A routed bundle only exists if a GENERATING session is told about it.

The read loop measured c3d-scene-composition / -lighting / -motion at 0 opens out of 3
listings each and read it as a wording problem.  It was not: the scene track did all of
its baseline generation in ``prepare()`` — the env and zones stages called
``tracks.generation.generate`` directly — while ``skills_hook.attach_for_round`` was only
reached from ``steps.run_round``.  ``SceneTrack.baseline_tasks`` returns ``[]``, so round 0
listed the bundles to a round that had no generation task to consume them.  That was
exactly "listed 3, opened 0", and no rewrite of a SKILL.md could have fixed it.

CLOSED 2026-08-25 (curate wave): ``SceneTrack._env_stage`` / ``_zones_stage`` now go
through the skill hook + ``_record_skills`` (``scene.js`` is assembled, no session).  These tests
keep every agent-driving module on the hook, so the gap cannot reopen quietly.  The scene
bundles' read rate is UNMEASURED against this delivery — that is the next wave's first
experiment, and until it runs their ledger rows stay ``mixed``/``inherited``, not
``measured``.
"""

from __future__ import annotations

from collections import Counter

import pytest

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.proc import EventLog, read_jsonl_lenient
from codeverse3d.workspace import Workspace


@pytest.fixture
def scene_run(tmp_path, monkeypatch):
    from codeverse3d.config import Settings
    from codeverse3d.tracks.planner import plan_example
    from codeverse3d.tracks.scene import SceneTrack
    from tests.orchestrator_tracks.conftest import make_spec
    from tests.orchestrator_tracks.fakes import FakeAgent, FakeJudge, FakeRuntime, FakeServices
    from tests.orchestrator_tracks.test_tracks import _planner, _scene_writer

    monkeypatch.setenv("C3D_SKILLS", "1")
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan.assets = [a for a in plan.assets if a.kind == "threejs"]
    for z in plan.zones:
        z.contents = [c for c in z.contents if c in {a.name for a in plan.assets}]
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=0,
                     prompt="a small harbour at dusk")
    ws = Workspace(tmp_path / "runs" / "harbour_skills")
    agent = FakeAgent(_scene_writer)
    track = SceneTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)),
                       agent=agent, planner_model=_planner(plan.model_dump(mode="json")),
                       settings=Settings(), runtime=FakeRuntime(Language.SCENE_THREEJS))
    rec = track.run(spec, ws)
    return rec, agent, ws


def test_every_scene_generation_stage_gets_skills_before_its_sessions(scene_run) -> None:
    """One real run proves routing, ordering and telemetry together."""
    rec, agent, ws = scene_run
    events = EventLog(ws.events_path).read()
    attachments = [e for e in events if e.get("event") == "skills.attached"]
    by_kind = {e["kind"]: e for e in attachments if e.get("kind") in {"env", "zone"}}

    assert Counter(e.get("kind") for e in attachments) >= Counter({"env": 1, "zone": 1})
    assert all(sum(e.get("kind") == kind for e in attachments) == 1 for kind in by_kind)
    assert {"c3d-scene-composition", "c3d-scene-lighting"} <= set(by_kind["env"]["skills"])
    assert {"c3d-scene-composition", "c3d-scene-motion"} <= set(by_kind["zone"]["skills"])
    # R3 is object-only: the bbox sheet's measure / check_contract / isolate loop has no scene tools
    assert "c3d-bbox-contract" not in by_kind["zone"]["skills"]

    def belongs(kind: str, event: dict) -> bool:
        label = str(event.get("label", ""))
        return event.get("event") == "generate.done" and (
            label == kind or (kind == "zone" and label.startswith(("zone_", "zones_")))
        )

    for kind in by_kind:
        attach_at = events.index(by_kind[kind])
        generated_at = [i for i, event in enumerate(events) if belongs(kind, event)]
        assert generated_at and all(attach_at < i for i in generated_at), kind
    assert sum(belongs("zone", event) for event in events) == 1  # D70: one session owns every zone file
    assert Counter(job.kind for job in agent.jobs) >= Counter({"env": 1, "zone": 1})

    telemetry_path = ws.root / "telemetry" / "skills.jsonl"
    assert telemetry_path.is_file()
    telemetry = read_jsonl_lenient(telemetry_path, dicts_only=True)
    assert all(sum(row.get("kind") == kind for row in telemetry) == 1 for kind in by_kind)

    baseline_at = next(i for i, event in enumerate(events)
                       if event.get("event") == "round.start" and event.get("kind") == "baseline")
    build_at = next(i for i, event in enumerate(events[baseline_at:], baseline_at)
                    if event.get("event") == "build.done")
    assert events[baseline_at]["n_tasks"] == 0
    assert not any(e.get("event") == "generate.done" for e in events[baseline_at:build_at])
    assert len(rec.rounds) == 1
