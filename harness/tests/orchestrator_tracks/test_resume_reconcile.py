"""reconcile_resume: on resume, the durable round journal is the truth."""

from __future__ import annotations

import json
from functools import partial
from types import SimpleNamespace

import pytest

from codeverse3d.contracts.common import Language
from codeverse3d.contracts.run import RunStatus
from codeverse3d.orchestrator import RoundPolicy
from codeverse3d.proc import EventLog
from codeverse3d.tracks.lifecycle import SpecChanged
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
)


def _writer(job, ws):
    return {"src/model.py": f"import bpy  # {job.label} r{job.round}\n"}


def _track(planner, settings, *, judge=None, policy=None):
    return StaticObjectTrack(services=FakeServices(), judge=judge or FakeJudge(scores=(0.5, 0.7)),
                             agent=FakeAgent(_writer), planner_model=planner, settings=settings,
                             runtime=FakeRuntime(Language.BLENDER), policy=policy)


@pytest.fixture
def completed_run(tmp_path, chair_plan, settings):
    """A finished run; ``.rerun(...)`` replays the track over the same workspace with a fresh planner."""
    plan = chair_plan.model_dump(mode="json")

    def rerun(r, *, planner=None, policy=None, spec=None, **kw):
        return _track(planner or _planner(plan), settings, policy=policy or r.policy).run(
            spec or r.spec, r.ws, **kw)

    def build(max_rounds: int = 1, **spec_kw) -> SimpleNamespace:
        r = SimpleNamespace(plan=plan, ws=Workspace(tmp_path / "runs" / "r"),
                            spec=make_spec(language=Language.BLENDER, max_rounds=max_rounds, **spec_kw),
                            policy=RoundPolicy(max_rounds=max_rounds))
        r.rerun = partial(rerun, r)
        r.record = r.rerun()
        return r

    return build


# --------------------------------------------------------------------- (a) crash window
def test_crash_between_round_write_and_state_save_ends_at_the_newer_round(completed_run):
    """A crash after rNN.json but before the state save resumes from the journal, on the last round."""
    run = completed_run(max_rounds=1)
    rec1, ws = run.record, run.ws
    assert len(rec1.rounds) == 2
    state = json.loads(ws.state_path.read_text())
    state.update(completed_rounds=[0], current_round=1, round_commits={"0": rec1.rounds[0].commit})
    ws.state_path.write_text(json.dumps(state))
    ws.restore(rec1.rounds[0].commit)
    ws.commit("stale tree")

    rec2 = run.rerun(resume=True)
    assert [r.index for r in rec2.rounds] == [0, 1] and rec2.rounds[1].commit == rec1.rounds[1].commit
    assert "refine" in (ws.src / "model.py").read_text(), "round 1's code must be restored"
    assert "completed_rounds" not in json.loads(ws.state_path.read_text()), "the state keeps no copy of the journal"
    ev = [e for e in EventLog(ws.events_path).read() if e["event"] == "resume.reconciled"]
    assert ev and ev[-1]["rounds"] == 2 and ev[-1]["restored_last_round"] == 1


def test_an_old_run_that_restored_its_best_round_resumes_from_its_last(completed_run, settings):
    """A pre-2026-09-22 run that ended on "restore best round" resumes from, and refines, its LAST round."""
    run = completed_run(max_rounds=1)
    rec1, ws = run.record, run.ws
    state = json.loads(ws.state_path.read_text())
    state.update(status="passed", stop_reason="pass", best_round=0, best_commit=rec1.rounds[0].commit,
                 best_score=0.5, best_considered_through=1)
    ws.state_path.write_text(json.dumps(state))
    record = json.loads(ws.record_path.read_text())
    record.update(status="passed", best_round=0, baseline_score=0.5, final_score=0.5)
    ws.record_path.write_text(json.dumps(record))
    ws.restore(rec1.rounds[0].commit)       # what the old finalise did
    ws.commit("restore best round r00")

    seen: list[tuple[int, str]] = []

    def writer(job, ws_):
        seen.append((job.round, (ws_.src / "model.py").read_text()))
        return _writer(job, ws_)

    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.6,)), agent=FakeAgent(writer),
                              planner_model=_planner(run.plan), settings=settings,
                              runtime=FakeRuntime(Language.BLENDER), policy=RoundPolicy(max_rounds=2))
    rec2 = track.run(make_spec(language=Language.BLENDER, max_rounds=2), ws, resume=True)
    assert [r.index for r in rec2.rounds] == [0, 1, 2] and rec2.status is RunStatus.MAX_ROUNDS
    assert seen and all(rnd == 2 for rnd, _ in seen), "only the new round generated"
    assert seen[0][1] == _code_at(ws, rec1.rounds[1].commit) != _code_at(ws, rec1.rounds[0].commit), "round 2 refines round 1"
    ev = [e for e in EventLog(ws.events_path).read() if e["event"] == "resume.reconciled"]
    assert ev[-1]["restored_last_round"] == 1
    assert ws.head() == rec2.rounds[-1].commit and _code_at(ws, ws.head()) == (ws.src / "model.py").read_text()


def _code_at(ws: Workspace, commit: str) -> str:
    return ws._git("show", f"{commit}:src/model.py").stdout  # noqa: SLF001


# --------------------------------------------------------------------- (b) history wipe
def test_a_planner_outage_on_resume_keeps_the_recorded_history(completed_run):
    run = completed_run(max_rounds=0)
    ws = run.ws
    assert len(run.record.rounds) == 1
    (ws.root / "stages" / "plan.json").unlink()  # clobbered cache → the resume re-plans

    def _boom(req):
        raise RuntimeError("planner 503 storm")

    with pytest.raises(RuntimeError, match="503 storm"):
        run.rerun(planner=FakeChatModel(_boom), policy=RoundPolicy(max_rounds=1), resume=True)
    saved = json.loads(ws.record_path.read_text())
    assert saved["status"] == "failed" and "503 storm" in saved["error"]
    assert [r["index"] for r in saved["rounds"]] == [0], "a planner outage must not wipe the history"
    assert saved["rounds"][0]["commit"] == run.record.rounds[0].commit


# --------------------------------------------------------------------- (d) force provenance
def test_force_resume_with_an_edited_prompt_archives_the_old_rounds(completed_run):
    """`resume --force` after a prompt edit archives the old journal and record under rounds/pre_force/."""
    run = completed_run(max_rounds=0)
    ws = run.ws
    old_commit = run.record.rounds[0].commit

    spec2 = make_spec(language=Language.BLENDER, max_rounds=0,
                      prompt="a completely different bar stool")
    ws.write_json(ws.spec_path, spec2)  # the user edited the prompt
    p2 = _planner(run.plan)
    rec2 = run.rerun(planner=p2, spec=spec2, resume=True, force=True)

    # the old journal + record are ARCHIVED, never paired with the new spec
    assert (ws.root / "rounds" / "pre_force" / "r00.json").is_file()
    assert (ws.root / "rounds" / "pre_force" / "record.json").is_file()
    archived = json.loads((ws.root / "rounds" / "pre_force" / "r00.json").read_text())
    assert archived["commit"] == old_commit
    # a fresh plan and a fresh round 0 under the new spec
    assert len(p2.requests) >= 1, "--force after a spec edit must re-plan"
    assert rec2.spec.prompt == "a completely different bar stool"
    assert [r.index for r in rec2.rounds] == [0] and rec2.rounds[0].commit != old_commit
    on_disk = json.loads(ws.record_path.read_text())
    assert on_disk["spec"]["prompt"] == "a completely different bar stool"
    assert [r["commit"] for r in on_disk["rounds"]] == [rec2.rounds[0].commit]
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "resume.spec_changed" in kinds and "resume.reconciled" in kinds


def test_a_spec_edit_without_force_is_refused_before_anything_runs(completed_run):
    run = completed_run(max_rounds=0)
    ws = run.ws

    spec2 = make_spec(language=Language.BLENDER, max_rounds=0,
                      prompt="a completely different bar stool")
    ws.write_json(ws.spec_path, spec2)
    p2 = _planner(run.plan)
    with pytest.raises(SpecChanged, match="--force"):
        run.rerun(planner=p2, spec=spec2, resume=True)
    # refused cleanly: nothing archived, nothing spent, nothing overwritten
    assert p2.requests == [], "no model call before the refusal"
    assert not (ws.root / "rounds" / "pre_force").exists()
    assert (ws.root / "rounds" / "r00.json").is_file()
    assert json.loads(ws.record_path.read_text())["status"] != "failed"


# --------------------------------------------------------------------- (f3) notes survive
def test_notes_survive_a_judge_crash(tmp_path, chair_plan, settings):
    class BoomJudge(FakeJudge):
        def judge(self, inp):
            if inp.round_index >= 1:
                raise RuntimeError("vlm 500")
            return super().judge(inp)

    spec = make_spec(language=Language.BLENDER, max_rounds=2)
    ws = Workspace(tmp_path / "runs" / "r")
    rec = _track(_planner(chair_plan.model_dump(mode="json")), settings,
                 judge=BoomJudge(scores=(0.5,))).run(spec, ws)
    assert rec.extra["stop_reason"] == "judge_unavailable"
    r1 = rec.rounds[1]
    assert r1.judgment is None
    assert "judge failed: RuntimeError: vlm 500" in r1.notes
    saved = json.loads((ws.root / "rounds" / "r01.json").read_text())
    assert "judge failed: RuntimeError: vlm 500" in saved["notes"]


def test_has_commit_answers_no_when_git_cannot_answer(tmp_path, monkeypatch):
    """has_commit answers False on a missing dir or a hung git (with the shared timeout)."""
    import subprocess

    from codeverse3d.workspace import HAS_COMMIT_TIMEOUT_S, Workspace

    assert Workspace(tmp_path / "gone").has_commit("abc123") is False
    ws = Workspace(tmp_path / "ws").create()
    sha = ws.commit("one")
    assert ws.has_commit(sha)
    seen: dict = {}

    def hung(*a, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(a[0], kw.get("timeout"))

    monkeypatch.setattr(subprocess, "run", hung)
    assert ws.has_commit(sha) is False and seen["timeout"] == HAS_COMMIT_TIMEOUT_S
