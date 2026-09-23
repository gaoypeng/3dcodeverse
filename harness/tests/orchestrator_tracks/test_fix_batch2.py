"""Lifecycle regressions: aborted rounds, degraded verdicts, the skeleton guard, finalise rebuilds."""

from __future__ import annotations

import json

import pytest

from codeverse3d.contracts.artifacts import BuildResult, GateReport, Judgment
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import BBox, PartPlan, ScenePlan, StaticPlan
from codeverse3d.contracts.run import RoundRecord, RunStatus
from codeverse3d.orchestrator import RunState
from codeverse3d.proc import EventLog
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene import SceneTrack
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import fake_clock, make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
    bill_verdict,
)


def _writer(job, ws):
    return {"src/object.js": f"// {job.label} r{job.round}\nexport function build(THREE) {{ return new THREE.Group(); }}\n"}


def degraded_verdict(cost: float = 0.003, round_index: int = 0) -> Judgment:
    return Judgment(rubric="fake_v1", judge_backend="fake", scores={}, overall=0.0, passed=False,
                    summary="judge_error: 503 UNAVAILABLE", usage=bill_verdict(cost, round_index),  # paid all the same
                    raw=json.dumps({"status": "degraded"}))


class ScriptedJudge(FakeJudge):
    """``script[i]`` is either a float (good verdict) or "degraded"."""

    def __init__(self, script):
        super().__init__(scores=tuple(s for s in script if not isinstance(s, str)) or (0.5,), targets=("Seat",))
        self.script = list(script)
        self.all_calls = []

    def judge(self, inp):
        i = min(len(self.all_calls), len(self.script) - 1)
        self.all_calls.append(inp)
        if self.script[i] == "degraded":
            return degraded_verdict(round_index=inp.round_index)
        self.calls = self.all_calls[: i]  # FakeJudge indexes by len(calls)
        out = super().judge(inp)
        out = out.model_copy(update={"overall": float(self.script[i]), "passed": float(self.script[i]) >= 0.8,
                                     "scores": {"geometry": float(self.script[i])}})
        return out


# --------------------------------------------------------------------- finding: finalise with a dirty tree at the last commit
def test_finalise_restores_the_last_round_when_an_aborted_round_dirtied_src(tmp_path, chair_plan, settings):
    """A refine round that died after writing files leaves src/ back on the last judged round."""
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "r")
    # the baseline session's 6 minutes fit the 10-minute clock; the refine session's 6 more
    # cross it AFTER its files hit the disk.
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.55, 0.7), targets=("Seat",)), agent=FakeAgent(_writer, minutes=6.0),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    with fake_clock():
        rec = track.run(spec, ws)
    assert rec.status is RunStatus.BUDGET and len(rec.rounds) == 1
    text = (ws.src / "object.js").read_text()
    assert "baseline r0" in text and "refine" not in text  # restored, not the aborted round's edits
    assert ws.changed_files() == []  # tree clean at the delivered commit
    # finding lifecycle.py:256 — the aborted round's money survives for resume: it is on the
    # ledger (planner 0.002 + baseline agent 0.01 + judge 0.003 + the aborted refine agent 0.01)
    assert rec.total_usage.cost_usd == pytest.approx(0.025, abs=1e-6)
    assert rec.extra["aborted_rounds"][0]["cost_usd"] == pytest.approx(0.01, abs=1e-6)


class _RebuildFails(FakeRuntime):
    """Fails the finalise rebuild (the build right after a "back to the last round" commit)
    ``fails`` times, invalidating the canonical GLB first as the real runtimes do."""

    def __init__(self, language, *, fails: int = 1_000):
        super().__init__(language)
        self.fails = fails

    def build(self, ws, *, timeout_s=None):
        if ws._git("log", "-1", "--format=%s").stdout.startswith("back to the last round") and self.fails:  # noqa: SLF001
            self.fails -= 1
            ws.stage_artifacts("object.glb").invalidate()
            return BuildResult(ok=False, language=self.language.value, error_type="Timeout",
                               error_message="runtime crashed on rebuild", error_file="src/object.js")
        return super().build(ws, timeout_s=timeout_s)


# --------------------------------------------------------------------- finding: degraded judge verdicts are glitches, not scores
def test_degraded_verdict_never_scores_and_run_stops_as_judge_unavailable(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "r")
    judge = ScriptedJudge([0.65, "degraded", "degraded"])  # r0 good; r1 degraded, re-judge degraded too
    track = StaticObjectTrack(services=FakeServices(), judge=judge, agent=FakeAgent(_writer),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    assert rec.rounds[1].judgment is None and rec.rounds[1].score is None  # never 0.0
    assert rec.rounds[0].score == pytest.approx(0.65)
    assert rec.extra["stop_reason"] == "judge_unavailable" and rec.status is RunStatus.JUDGE_UNAVAILABLE
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "judge.degraded" in kinds and "judge.retry" in kinds
    assert len(judge.all_calls) == 3
    # both degraded verdicts were PAID, and money and note reach the persisted round
    saved = json.loads((ws.root / "rounds" / "r01.json").read_text())
    assert "judge degraded" in saved["notes"] and saved["usage"]["cost_usd"] == pytest.approx(0.016, abs=1e-6)


def test_degraded_verdict_recovers_via_rejudge_of_same_commit(tmp_path, chair_plan, settings):
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "r")
    judge = ScriptedJudge([0.55, "degraded", 0.85])  # re-judge of r1 succeeds and passes
    agent = FakeAgent(_writer)
    track = StaticObjectTrack(services=FakeServices(), judge=judge, agent=agent,
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.THREEJS))
    rec = track.run(spec, ws)
    assert rec.status is RunStatus.MAX_ROUNDS
    assert [r.score for r in rec.rounds] == pytest.approx([0.55, 0.85, 0.85, 0.85])
    # the recovery re-judged the SAME commit: every round was generated exactly once
    assert [j.round for j in agent.jobs] == [0, 1, 2, 3]
    saved = RoundRecord.model_validate(json.loads((ws.root / "rounds" / "r01.json").read_text()))
    assert saved.judgment is not None and saved.judgment.overall == pytest.approx(0.85)
    # audit f1: every paid verdict is on the round's usage
    assert rec.rounds[1].usage.cost_usd == pytest.approx(0.016, abs=1e-6)  # agent + degraded + rejudge
    assert "judge degraded" in rec.rounds[1].notes and "judge degraded" in saved.notes


# --------------------------------------------------------------------- finding: plan stage hash covers the whole Spec
def test_skeleton_never_reruns_over_existing_rounds(tmp_path, chair_plan, settings):
    class CountingRuntime(FakeRuntime):
        def __init__(self, language):
            super().__init__(language)
            self.skeletons = 0

        def skeleton(self, ws, plan):
            self.skeletons += 1
            return super().skeleton(ws, plan)

    spec = make_spec(max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "r")
    rt = CountingRuntime(Language.THREEJS)
    planner = _planner(chair_plan.model_dump(mode="json"))
    mk = lambda: StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.5, 0.6)), agent=FakeAgent(_writer),  # noqa: E731
                                   planner_model=planner, settings=settings, runtime=rt)
    mk().run(spec, ws)
    assert rt.skeletons == 1
    src_before = (ws.src / "object.js").read_text()
    # simulate a skeleton-stage hash change on resume (the failure mode the guard closes)
    state = json.loads(ws.state_path.read_text())
    state["stages"].pop("skeleton")
    ws.state_path.write_text(json.dumps(state))
    mk().run(spec, ws, resume=True)
    assert rt.skeletons == 1, "skeleton must not overwrite agent-authored src/ once rounds exist"
    assert (ws.src / "object.js").read_text() == src_before
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "stage.skipped" in kinds


# --------------------------------------------------------------------- finding: scene track rebuild path
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


# --------------------------------------------------------------------- finding: plan digest frame order
def test_plan_digest_is_labelled_w_h_d_in_the_measurement_frame():
    from codeverse3d.judges.base import plan_summary

    plan = StaticPlan(object_name="Cabinet", summary="A cabinet.",
                      overall_bbox=BBox(center=(0, 0, 0.4), extents=(0.48, 0.52, 0.80)),  # Z-up plan: W×D×H
                      parts=[PartPlan(name="Body", role="body", description="box",
                                      bbox=BBox(center=(0, 0, 0.4), extents=(0.48, 0.52, 0.80)))])
    zup = plan_summary(plan, Language.BLENDER)
    assert "Overall 0.48×0.80×0.52 m (W×H×D)" in zup  # H and D swapped into the GLB/measurement order
    yup = plan_summary(plan, Language.THREEJS)
    assert "Overall 0.48×0.52×0.80 m (W×H×D)" in yup  # identity for Y-up plans


def test_a_successful_re_finalise_clears_the_stale_rebuild_failed_flag(tmp_path, chair_plan, settings, monkeypatch):
    """A failed finalise rebuild is flagged; a later resume that rebuilds successfully clears the flag."""
    spec = make_spec(max_rounds=3)
    ws = Workspace(tmp_path / "runs" / "r")
    runtime = _RebuildFails(Language.THREEJS, fails=1)

    def track():
        return StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.7,), targets=("Seat",)),
                                 agent=FakeAgent(_writer, minutes=6.0), planner_model=_planner(chair_plan.model_dump(mode="json")),
                                 settings=settings, runtime=runtime)

    with fake_clock():
        rec1 = track().run(spec, ws)
    assert "runtime crashed on rebuild" in rec1.extra["finalise_rebuild_failed"]
    # the resume is still past the clock: no round runs, and its finalise rebuilds the last round
    with fake_clock():
        rec2 = track().run(spec, ws, resume=True)
    assert "finalise_rebuild_failed" not in rec2.extra, "a successful rebuild must clear the stale flag"
    assert not rec2.error.startswith("finalise rebuild failed")
    assert (ws.artifacts / "object.glb").is_file()
