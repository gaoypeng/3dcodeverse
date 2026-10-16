"""D111: the scene env stage is ``ok`` only when the env module itself was written, else it is recovered.

Argon loop iteration 4 (scene_threejs, 2026-10-15): in 12 of 15 runs the env session wrote nothing,
was credited with the asset files written beside it and its stage read ``ok``; round 0 was unlit and
the single refine round wrote the whole environment from the skeleton.  The stage now applies the
zone stage's own test (the file in ``files_changed`` or rewritten since the stage began) and runs
ONE single-shot recovery before the zones — for either scene language's env file.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from codeverse3d.contracts.agent import FileChange
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.orchestrator import RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks import scene as scene_mod
from codeverse3d.tracks.generation import SINGLE_SHOT_PREFIX, GenerationResult
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene import SceneTrack, env_file
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeRuntime, FakeServices

LANGUAGES = [Language.SCENE_THREEJS, Language.SCENE_BLENDER]


def _ctx(tmp_path, settings, language: Language):
    ws = Workspace(tmp_path / "runs" / "s").create()
    track = SceneTrack(services=FakeServices(), settings=settings, runtime=FakeRuntime(language))
    ctx = track.build_context(make_spec(Track.SCENE, language, max_minutes=90.0), ws, EventLog(ws.events_path), RunState())
    ctx.plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    rel = env_file(ctx)
    (ws.root / rel).parent.mkdir(parents=True, exist_ok=True)
    (ws.root / rel).write_text("# PLACEHOLDER env\n")
    (ws.src / "assets").mkdir(parents=True, exist_ok=True)
    (ws.src / "assets" / "lantern.js").write_text("// stub\n")
    ws.commit("skeleton")
    # the fake services expose no chat model; a real run's planner is an API model
    ctx.extra["_single_shot_ctx"] = replace(ctx, agent_id=SINGLE_SHOT_PREFIX + "fake:planner", model=object(), agent=None)
    return track, ctx


def _events(ctx) -> list[str]:
    return [e["event"] for e in ctx.events.read()]


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_session_credited_only_with_other_stages_files_fails_and_is_recovered(tmp_path, settings, monkeypatch, language):
    track, ctx = _ctx(tmp_path, settings, language)
    rel = env_file(ctx)
    calls: list[tuple[str, bool, bool]] = []

    def fake_generate_for(gen: Any, task: Any) -> GenerationResult:
        calls.append((task.label, bool(gen.single_shot), task.shared_tree))
        if task.label.endswith(".recover"):
            (ctx.ws.root / rel).write_text("# the env, at last\n")
            return GenerationResult(ok=True, files_changed=[FileChange(path=rel, status="modified")], label=task.label)
        # the iteration-4 session: read, wrote nothing, and the asset single-shot's file came back as its own
        (ctx.ws.src / "assets" / "lantern.js").write_text("// written by the asset stage meanwhile\n")
        return GenerationResult(ok=True, files_changed=[FileChange(path="src/assets/lantern.js", status="modified")],
                                label=task.label, notes="exit=timeout")

    monkeypatch.setattr(scene_mod, "generate_for", fake_generate_for)
    out = track._env_stage(ctx)
    assert out["ok"] and out["files"] == [rel], out
    assert [c[0] for c in calls] == ["env", "env.recover"], calls
    assert calls[0][2] is True, "the env session shares its tree with the asset single-shots"
    assert calls[1][1] is True, "the recovery is single-shot: it cannot spend its window reading"
    assert {"env.recover", "env.recovered"} <= set(_events(ctx))


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_recovery_that_also_writes_nothing_leaves_the_stage_failed(tmp_path, settings, monkeypatch, language):
    track, ctx = _ctx(tmp_path, settings, language)

    def fake_generate_for(gen: Any, task: Any) -> GenerationResult:
        return GenerationResult(ok=False, label=task.label, notes="exit=timeout")

    monkeypatch.setattr(scene_mod, "generate_for", fake_generate_for)
    out = track._env_stage(ctx)
    assert out["ok"] is False and out["files"] == [] and "recover" in out["notes"], out


def test_a_session_that_wrote_the_env_triggers_no_recovery(tmp_path, settings, monkeypatch):
    track, ctx = _ctx(tmp_path, settings, Language.SCENE_THREEJS)
    rel = env_file(ctx)
    labels: list[str] = []

    def fake_generate_for(gen: Any, task: Any) -> GenerationResult:
        labels.append(task.label)
        (ctx.ws.root / rel).write_text("// lights, fog, sky\n")
        return GenerationResult(ok=True, files_changed=[FileChange(path=rel, status="modified")], label=task.label)

    monkeypatch.setattr(scene_mod, "generate_for", fake_generate_for)
    out = track._env_stage(ctx)
    assert out == {"ok": True, "files": [rel], "notes": ""} and labels == ["env"], (out, labels)
    assert "env.recover" not in _events(ctx)
