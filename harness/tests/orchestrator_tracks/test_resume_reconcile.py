"""reconcile_resume: on resume, the durable round journal is the truth.

Covers the audited failure modes:

* the crash window between the round-journal write and the state save — a stale
  best used to be restored and DELIVERED although a newer PAID round was on disk;
* a planner outage on resume serializing record.json with ``rounds=[]`` (the
  flywheel-visible history wipe);
* ``resume --force`` after a spec edit pairing old rounds with the new spec/plan
  (fake prompt→code provenance) — the old rounds are archived under
  ``rounds/pre_force/`` instead;
* a spec edit WITHOUT ``--force`` → typed :class:`SpecChanged` refusal, before
  any model call and without damaging the run;
* judge-failure notes lost to ``_run_round``'s local notes list.
"""

from __future__ import annotations

import json

import pytest

from codeverse.contracts.common import Language
from codeverse.events import EventLog
from codeverse.orchestrator.rounds import RoundPolicy
from codeverse.orchestrator.state import RunState
from codeverse.tracks.lifecycle import SpecChanged
from codeverse.tracks.static_object import StaticObjectTrack
from codeverse.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
)


def _planner(plan_dict):
    return FakeChatModel(lambda req: plan_dict)


def _writer(job, ws):
    return {"src/model.py": f"import bpy  # {job.label} r{job.extra.get('round', 0)}\n"}


def _track(planner, settings, *, judge=None, policy=None):
    return StaticObjectTrack(services=FakeServices(), judge=judge or FakeJudge(scores=(0.5, 0.7)),
                             agent=FakeAgent(_writer), planner_model=planner, settings=settings,
                             runtime=FakeRuntime(Language.BLENDER), policy=policy)


# --------------------------------------------------------------------- (a) crash window
def test_crash_between_round_write_and_state_save_delivers_the_newer_round(tmp_path, chair_plan, settings):
    """rNN.json + its commit are durable; mark_round_done/update_best used to live only
    in memory until _save_budget — with a possible PAID pairwise call in between.  A
    crash in that window plus an immediate stop on resume restored and delivered a
    STALE best although the newer paid round was on disk."""
    spec = make_spec(language=Language.BLENDER, max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "r")
    planner = _planner(chair_plan.model_dump(mode="json"))
    pol = RoundPolicy(max_rounds=1, target=0.9)
    rec1 = _track(planner, settings, policy=pol).run(spec, ws)
    assert rec1.best_round == 1 and len(rec1.rounds) == 2
    # rewind state.json to what a crash between the r01 write and the state save leaves
    state = json.loads(ws.state_path.read_text())
    state["completed_rounds"], state["current_round"] = [0], 1
    state["round_commits"] = {"0": rec1.rounds[0].commit}
    state["best_round"], state["best_commit"], state["best_score"] = 0, rec1.rounds[0].commit, 0.5
    ws.state_path.write_text(json.dumps(state))
    ws.restore(rec1.rounds[0].commit)  # ...and the tree at the stale best, as delivered
    ws.commit("stale delivery")

    rec2 = _track(planner, settings, policy=pol).run(spec, ws, resume=True)
    # the immediate stop (max_rounds) now delivers the NEWER paid round
    assert rec2.best_round == 1 and rec2.final_score == pytest.approx(0.7)
    assert [r.index for r in rec2.rounds] == [0, 1]
    assert "refine" in (ws.src / "model.py").read_text(), "round 1's code must be restored"
    st = RunState.load(ws)
    assert st.completed_rounds == [0, 1] and st.best_round == 1
    assert st.best_commit == rec1.rounds[1].commit
    ev = [e for e in EventLog(ws.events_path).read() if e["event"] == "resume.reconciled"]
    assert ev and ev[-1]["state_was_stale"] is True and ev[-1]["best_changed"] is True


# --------------------------------------------------------------------- (b) history wipe
def test_a_planner_outage_on_resume_keeps_the_recorded_history(tmp_path, chair_plan, settings):
    """The FAILED handler used to serialize record.json with rounds=[] because the
    journal was only loaded AFTER plan/prepare — one planner outage on resume wiped
    record.rounds (and with it the flywheel's rounds_summary)."""
    spec = make_spec(language=Language.BLENDER, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "r")
    rec1 = _track(_planner(chair_plan.model_dump(mode="json")), settings,
                  policy=RoundPolicy(max_rounds=0, target=0.9)).run(spec, ws)
    assert len(rec1.rounds) == 1
    (ws.root / "stages" / "plan.json").unlink()  # clobbered cache → the resume re-plans

    def _boom(req):
        raise RuntimeError("planner 503 storm")

    with pytest.raises(RuntimeError, match="503 storm"):
        _track(FakeChatModel(_boom), settings,
               policy=RoundPolicy(max_rounds=1, target=0.9)).run(spec, ws, resume=True)
    saved = json.loads(ws.record_path.read_text())
    assert saved["status"] == "failed" and "503 storm" in saved["error"]
    assert [r["index"] for r in saved["rounds"]] == [0], "a planner outage must not wipe the history"
    assert saved["rounds"][0]["commit"] == rec1.rounds[0].commit
    assert RunState.load(ws).completed_rounds == [0]


# --------------------------------------------------------------------- (d) force provenance
def test_force_resume_with_an_edited_prompt_archives_the_old_rounds(tmp_path, chair_plan, settings):
    """`resume --force` after a prompt edit used to re-plan and then pair the OLD
    rounds with the NEW spec/plan in record.json — fake prompt→code provenance.
    The old journal + record are archived under rounds/pre_force/ instead."""
    spec = make_spec(language=Language.BLENDER, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "r")
    pol = RoundPolicy(max_rounds=0, target=0.9)
    rec1 = _track(_planner(chair_plan.model_dump(mode="json")), settings, policy=pol).run(spec, ws)
    old_commit = rec1.rounds[0].commit

    spec2 = make_spec(language=Language.BLENDER, max_rounds=0,
                      prompt="a completely different bar stool")
    ws.write_json(ws.spec_path, spec2)  # the user edited the prompt
    p2 = _planner(chair_plan.model_dump(mode="json"))
    rec2 = _track(p2, settings, policy=pol).run(spec2, ws, resume=True, force=True)

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


def test_a_spec_edit_without_force_is_refused_before_anything_runs(tmp_path, chair_plan, settings):
    spec = make_spec(language=Language.BLENDER, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "r")
    pol = RoundPolicy(max_rounds=0, target=0.9)
    _track(_planner(chair_plan.model_dump(mode="json")), settings, policy=pol).run(spec, ws)

    spec2 = make_spec(language=Language.BLENDER, max_rounds=0,
                      prompt="a completely different bar stool")
    ws.write_json(ws.spec_path, spec2)
    p2 = _planner(chair_plan.model_dump(mode="json"))
    with pytest.raises(SpecChanged, match="--force"):
        _track(p2, settings, policy=pol).run(spec2, ws, resume=True)
    # refused cleanly: nothing archived, nothing spent, nothing overwritten
    assert p2.requests == [], "no model call before the refusal"
    assert not (ws.root / "rounds" / "pre_force").exists()
    assert (ws.root / "rounds" / "r00.json").is_file()
    st = RunState.load(ws)
    assert st.best_round == 0 and st.completed_rounds == [0]
    assert json.loads(ws.record_path.read_text())["status"] != "failed"

    # the sanctioned budget raise (outside the fingerprint) still resumes plainly
    spec3 = make_spec(language=Language.BLENDER, max_rounds=0, max_usd=50.0)
    ws.write_json(ws.spec_path, spec3)
    rec3 = _track(_planner(chair_plan.model_dump(mode="json")), settings, policy=pol).run(
        spec3, ws, resume=True)
    assert [r.index for r in rec3.rounds] == [0]


# --------------------------------------------------------------------- (f3) notes survive
def test_notes_survive_a_judge_crash(tmp_path, chair_plan, settings):
    """_judge used to append 'judge failed: ...' to rec.notes, which _run_round then
    overwrote with its own local notes list — the note never reached rNN.json."""
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


def test_a_self_consistent_state_with_an_unranked_round_still_re_ranks(tmp_path, chair_plan, settings):
    """The OTHER crash shape, and the sharper one (reproduced by review, 2026-08-27).

    While the round was saved BEFORE best selection, a crash in between left a state
    that looked perfectly consistent — completed_rounds and round_commits matched the
    journal exactly — while best_round still pointed at the older, worse round.  Nothing
    was 'stale', so reconcile kept it and the run DELIVERED r0=0.5 over the paid r1=0.7.
    The writer now saves once, after ranking; `best_considered_through` makes the repair
    independent of write ordering, so even such a state (an older run dir, a partial
    write) is re-ranked rather than believed."""
    spec = make_spec(language=Language.BLENDER, max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "r")
    planner = _planner(chair_plan.model_dump(mode="json"))
    pol = RoundPolicy(max_rounds=1, target=0.9)
    rec1 = _track(planner, settings, policy=pol).run(spec, ws)
    assert rec1.best_round == 1 and rec1.rounds[1].score == pytest.approx(0.7)

    state = json.loads(ws.state_path.read_text())      # journal-consistent, best stale
    assert state["completed_rounds"] == [0, 1]
    state["best_round"], state["best_commit"], state["best_score"] = 0, rec1.rounds[0].commit, 0.5
    state["best_considered_through"] = 0               # r1 was written but never ranked
    ws.state_path.write_text(json.dumps(state))
    ws.restore(rec1.rounds[0].commit)
    ws.commit("stale delivery")

    rec2 = _track(planner, settings, policy=pol).run(spec, ws, resume=True)
    assert rec2.best_round == 1 and rec2.final_score == pytest.approx(0.7)
    ev = [e for e in EventLog(ws.events_path).read() if e["event"] == "resume.reconciled"][-1]
    assert ev["state_was_stale"] is False and ev["unranked_rounds"] is True and ev["best_changed"] is True


def test_resume_charges_for_spend_the_snapshot_missed(tmp_path, chair_plan, settings):
    """The ledger is appended per CALL; the snapshot is saved at boundaries.  A crash
    between a round's spend and its save used to hand the resumed run that money back —
    silently under-counting is how a resumed run walks past its ceiling."""
    from codeverse.cost.ledger import open_run_ledger
    from codeverse.cost.types import CallCost
    from codeverse.orchestrator.budget import BudgetGuard, BudgetSnapshot
    from codeverse.tracks.lifecycle import _reconcile_billed_from_ledger

    ws = Workspace(tmp_path / "runs" / "r")
    ws.create()
    led = open_run_ledger(ws.root)
    for cost in (0.30, 0.12):                       # what the provider actually billed
        led.append(CallCost(run=ws.root.name, model="gemini:flash", label="planner", cost_usd=cost))
    guard = BudgetGuard(make_spec().budget, run=ws.root.name)
    guard.restore(BudgetSnapshot(spent=guard.spent, billed_usd=0.10,   # the boundary save missed 0.32
                                 calls=1, by_stage={}, by_round={}, active_s=0.0))
    _reconcile_billed_from_ledger(guard, ws, EventLog(ws.events_path))
    assert guard.billed_usd == pytest.approx(0.42), "resume must charge for every billed call"
    guard.billed_usd = 5.0                           # a snapshot AHEAD of the ledger wins
    _reconcile_billed_from_ledger(guard, ws, EventLog(ws.events_path))
    assert guard.billed_usd == pytest.approx(5.0), "reconcile never lowers what was already billed"
