"""reconcile_resume: on resume, the durable round journal is the truth.

One test per audited failure mode — the two crash windows between a round write and the
state save, a planner outage wiping ``record.rounds``, ``--force``/no-``--force`` after a
spec edit, judge notes lost to a local list, and spend the budget snapshot missed.
"""

from __future__ import annotations

import json
from functools import partial
from types import SimpleNamespace

import pytest

from codeverse.contracts.common import Language
from codeverse.orchestrator import RoundPolicy, RunState
from codeverse.proc import EventLog
from codeverse.tracks.lifecycle import SpecChanged
from codeverse.tracks.static_object import StaticObjectTrack
from codeverse.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakePairwise,
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
    """A finished StaticObjectTrack run — the starting point of every resume test below.

    ``.rerun(...)`` replays the same track over the same workspace (``resume=``/``force=``)
    with a FRESH planner, so a test that passes its own ``planner=`` sees in ``.requests``
    only what the resume asked for; ``policy=`` / ``spec=`` override the rest.
    """
    plan = chair_plan.model_dump(mode="json")

    def rerun(r, *, planner=None, policy=None, spec=None, **kw):
        return _track(planner or _planner(plan), settings, policy=policy or r.policy).run(
            spec or r.spec, r.ws, **kw)

    def build(max_rounds: int = 1, **spec_kw) -> SimpleNamespace:
        r = SimpleNamespace(plan=plan, ws=Workspace(tmp_path / "runs" / "r"),
                            spec=make_spec(language=Language.BLENDER, max_rounds=max_rounds, **spec_kw),
                            policy=RoundPolicy(max_rounds=max_rounds, target=0.9))
        r.rerun = partial(rerun, r)
        r.record = r.rerun()
        return r

    return build


# --------------------------------------------------------------------- (a) crash window
def test_crash_between_round_write_and_state_save_delivers_the_newer_round(completed_run):
    """rNN.json + its commit are durable but mark_round_done/update_best lived only in
    memory until _save_budget — with a possible PAID pairwise call in between.  A crash in
    that window plus an immediate stop on resume delivered the STALE best."""
    run = completed_run(max_rounds=1)
    rec1, ws = run.record, run.ws
    assert rec1.best_round == 1 and len(rec1.rounds) == 2
    # rewind state.json to what a crash between the r01 write and the state save leaves
    state = json.loads(ws.state_path.read_text())
    state["completed_rounds"], state["current_round"] = [0], 1
    state["round_commits"] = {"0": rec1.rounds[0].commit}
    state["best_round"], state["best_commit"], state["best_score"] = 0, rec1.rounds[0].commit, 0.5
    ws.state_path.write_text(json.dumps(state))
    ws.restore(rec1.rounds[0].commit)  # ...and the tree at the stale best, as delivered
    ws.commit("stale delivery")

    rec2 = run.rerun(resume=True)
    # the immediate stop (max_rounds) now delivers the NEWER paid round
    assert rec2.best_round == 1 and rec2.final_score == pytest.approx(0.7)
    assert [r.index for r in rec2.rounds] == [0, 1]
    assert "refine" in (ws.src / "model.py").read_text(), "round 1's code must be restored"
    st = RunState.load(ws)
    assert st.completed_rounds == [0, 1] and st.best_round == 1
    assert st.best_commit == rec1.rounds[1].commit
    ev = [e for e in EventLog(ws.events_path).read() if e["event"] == "resume.reconciled"]
    assert ev and ev[-1]["state_was_stale"] is True and ev[-1]["best_changed"] is True


def test_a_paid_pairwise_verdict_survives_the_crash_window(tmp_path, chair_plan, settings):
    """``choose_best_round`` re-persists rNN.json WITH its verdict and only THEN does
    ``_promote_best`` save the state, so a kill in that gap (or any state predating
    ``best_considered_through``) left a paid ~$0.05 judgement on disk that reconcile
    could not see — and score-only re-ranking silently reversed it.  18 runs on disk
    were in that shape (2026-08-30)."""
    plan = chair_plan.model_dump(mode="json")

    def build(pairwise):
        return StaticObjectTrack(services=FakeServices(pairwise=pairwise), judge=FakeJudge(scores=(0.70, 0.72)),
                                 agent=FakeAgent(_writer), planner_model=_planner(plan), settings=settings,
                                 runtime=FakeRuntime(Language.BLENDER),
                                 policy=RoundPolicy(max_rounds=1, target=0.9))

    spec = make_spec(language=Language.BLENDER, max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "r")
    paid = FakePairwise([("a", 0.9)])   # r1 outscores r0 by 0.02 (inside the margin); the judge says r0
    rec1 = build(paid).run(spec, ws)
    assert len(paid.calls) == 1 and rec1.best_round == 0 and rec1.final_score == pytest.approx(0.70)
    note = rec1.rounds[1].pairwise
    assert note is not None and note.winner == "a" and note.accepted is False
    on_disk = json.loads((ws.root / "rounds" / "r01.json").read_text())
    assert on_disk["pairwise"]["winner"] == "a", "the verdict must be durable, not just in notes"

    state = json.loads(ws.state_path.read_text())    # ...killed before _promote_best saved
    state["best_considered_through"] = 0
    ws.state_path.write_text(json.dumps(state))

    again = FakePairwise([("b", 0.99)])              # a second verdict would flip it
    rec2 = build(again).run(spec, ws, resume=True)
    assert again.calls == [], "resume replays the stored verdict, it never buys another"
    assert rec2.best_round == 0 and rec2.final_score == pytest.approx(0.70)
    assert RunState.load(ws).best_round == 0


def test_a_self_consistent_state_with_an_unranked_round_still_re_ranks(completed_run):
    """The sharper crash shape (reproduced by review, 2026-08-27): the round was saved
    BEFORE best selection, so completed_rounds/round_commits matched the journal exactly
    while best_round still pointed at the older round — nothing looked stale, reconcile
    believed it, and the run DELIVERED r0=0.5 over the paid r1=0.7.  ``best_considered_
    through`` makes the repair independent of write ordering."""
    run = completed_run(max_rounds=1)
    rec1, ws = run.record, run.ws
    assert rec1.best_round == 1 and rec1.rounds[1].score == pytest.approx(0.7)

    state = json.loads(ws.state_path.read_text())      # journal-consistent, best stale
    assert state["completed_rounds"] == [0, 1]
    state["best_round"], state["best_commit"], state["best_score"] = 0, rec1.rounds[0].commit, 0.5
    state["best_considered_through"] = 0               # r1 was written but never ranked
    ws.state_path.write_text(json.dumps(state))
    ws.restore(rec1.rounds[0].commit)
    ws.commit("stale delivery")

    rec2 = run.rerun(resume=True)
    assert rec2.best_round == 1 and rec2.final_score == pytest.approx(0.7)
    ev = [e for e in EventLog(ws.events_path).read() if e["event"] == "resume.reconciled"][-1]
    assert ev["state_was_stale"] is False and ev["unranked_rounds"] is True and ev["best_changed"] is True


def test_a_stored_pairwise_rejection_is_final(chair_plan):
    """A paid pairwise REJECTION of a challenger (r1 within margin, judge said keep r0)
    used to be silently overturned once ANY later round landed: both the live
    ``choose_best_round`` and the resume ``replay_best_round`` re-ranked the WHOLE
    journal on score alone and crowned the rejected r1.  The verdict is final: live
    and replay agree, whether the later round is worse or failed to score at all."""
    from codeverse.contracts.artifacts import BuildResult, Judgment
    from codeverse.contracts.run import PairwiseNote, RoundRecord
    from codeverse.orchestrator import BestSelector
    from codeverse.tracks.candidates import choose_best_round, replay_best_round

    def rec(i, score, pairwise=None):
        j = None if score is None else Judgment(rubric="r", scores={}, overall=score, passed=False)
        return RoundRecord(index=i, kind="baseline" if i == 0 else "refine", commit=f"c{i}",
                           build=BuildResult(ok=score is not None, language="blender", entrypoint="src/main.py"),
                           judgment=j, pairwise=pairwise)

    r0 = rec(0, 0.70)
    rejected = PairwiseNote(a="r00", b="r01", winner="a", confidence=0.9, accepted=False)
    r1 = rec(1, 0.72, pairwise=rejected)  # outscores r0, but the paid verdict said keep r0
    r2 = rec(2, 0.50)
    assert replay_best_round([r0, r1]) == 0
    assert replay_best_round([r0, r1, r2]) == 0, "a worse later round must not revive the rejected r1"
    ctx = SimpleNamespace(state=SimpleNamespace(best_round=0),
                          policy=SimpleNamespace(pairwise_margin=0.03, pairwise_min_confidence=0.6))
    assert choose_best_round(ctx, [r0, r1, r2], BestSelector(), 2) == 0, "live must agree with replay"
    r2b = rec(2, None)  # the new round never scored (build crash)
    assert choose_best_round(ctx, [r0, r1, r2b], BestSelector(), 2) == 0
    # an ACCEPTED verdict still promotes the challenger, and survives later worse rounds
    accepted = PairwiseNote(a="r00", b="r01", winner="b", confidence=0.9, accepted=True)
    assert replay_best_round([r0, rec(1, 0.72, pairwise=accepted), r2]) == 1


# --------------------------------------------------------------------- (b) history wipe
def test_a_planner_outage_on_resume_keeps_the_recorded_history(completed_run):
    """The FAILED handler serialized record.json with rounds=[] because the journal was
    loaded only AFTER plan/prepare — one planner outage on resume wiped record.rounds
    (and with it the flywheel's rounds_summary)."""
    run = completed_run(max_rounds=0)
    ws = run.ws
    assert len(run.record.rounds) == 1
    (ws.root / "stages" / "plan.json").unlink()  # clobbered cache → the resume re-plans

    def _boom(req):
        raise RuntimeError("planner 503 storm")

    with pytest.raises(RuntimeError, match="503 storm"):
        run.rerun(planner=FakeChatModel(_boom), policy=RoundPolicy(max_rounds=1, target=0.9), resume=True)
    saved = json.loads(ws.record_path.read_text())
    assert saved["status"] == "failed" and "503 storm" in saved["error"]
    assert [r["index"] for r in saved["rounds"]] == [0], "a planner outage must not wipe the history"
    assert saved["rounds"][0]["commit"] == run.record.rounds[0].commit
    assert RunState.load(ws).completed_rounds == [0]


# --------------------------------------------------------------------- (d) force provenance
def test_force_resume_with_an_edited_prompt_archives_the_old_rounds(completed_run):
    """`resume --force` after a prompt edit re-planned and then paired the OLD rounds with
    the NEW spec/plan in record.json — fake prompt→code provenance.  The old journal +
    record are archived under rounds/pre_force/ instead."""
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
    st = RunState.load(ws)
    assert st.best_round == 0 and st.completed_rounds == [0]
    assert json.loads(ws.record_path.read_text())["status"] != "failed"

    # the sanctioned budget raise (outside the fingerprint) still resumes plainly
    spec3 = make_spec(language=Language.BLENDER, max_rounds=0)
    ws.write_json(ws.spec_path, spec3)
    rec3 = run.rerun(spec=spec3, resume=True)
    assert [r.index for r in rec3.rounds] == [0]


# --------------------------------------------------------------------- (f3) notes survive
def test_notes_survive_a_judge_crash(tmp_path, chair_plan, settings):
    """_judge appended 'judge failed: ...' to rec.notes, which _run_round then overwrote
    with its own local notes list — the note never reached rNN.json."""
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


def test_resume_charges_for_spend_the_snapshot_missed(tmp_path, chair_plan, settings):
    """The ledger is appended per CALL, the snapshot saved at boundaries: a crash between
    a round's spend and its save handed the resumed run that money back — silently
    under-counting is how a resumed run walks past its ceiling."""
    from codeverse.cost.ledger import open_run_ledger
    from codeverse.cost.types import CallCost
    from codeverse.orchestrator import BudgetGuard, BudgetSnapshot
    from codeverse.tracks.lifecycle import _reconcile_billed_from_ledger

    ws = Workspace(tmp_path / "runs" / "r")
    ws.create()
    led = open_run_ledger(ws.root)
    for cost in (0.30, 0.12):                       # what the provider actually billed
        led.append(CallCost(run=ws.root.name, model="gemini:flash", label="planner", cost_usd=cost))
    guard = BudgetGuard(make_spec().budget)
    guard.restore(BudgetSnapshot(spent=guard.spent, billed_usd=0.10,   # the boundary save missed 0.32
                                 calls=1, by_stage={}, active_s=0.0))
    _reconcile_billed_from_ledger(guard, ws, EventLog(ws.events_path))
    assert guard.billed_usd == pytest.approx(0.42), "resume must charge for every billed call"
    guard.billed_usd = 5.0                           # a snapshot AHEAD of the ledger wins
    _reconcile_billed_from_ledger(guard, ws, EventLog(ws.events_path))
    assert guard.billed_usd == pytest.approx(5.0), "reconcile never lowers what was already billed"


def test_resume_reconcile_keeps_subscription_spend_notional(tmp_path, settings):
    """A codex/claude/agy ledger row is priced at list rates but bills $0 (bills_usd):
    resuming a subscription-backend run offline must not flip billed from $0 to the
    notional sum — reconcile shares the exact predicate the live spend path uses."""
    from codeverse.cost.ledger import open_run_ledger
    from codeverse.cost.types import CallCost
    from codeverse.orchestrator import BudgetGuard
    from codeverse.tracks.lifecycle import _reconcile_billed_from_ledger

    ws = Workspace(tmp_path / "runs" / "sub")
    ws.create()
    led = open_run_ledger(ws.root)
    led.append(CallCost(run=ws.root.name, backend="codex", model="gpt-5.6-sol", label="generator", cost_usd=7.7))
    led.append(CallCost(run=ws.root.name, backend="claude-code", model="sonnet", cost_usd=1.1))
    guard = BudgetGuard(make_spec().budget)
    _reconcile_billed_from_ledger(guard, ws, EventLog(ws.events_path))
    assert guard.billed_usd == 0.0, "subscription cost is notional; resume must keep billed at $0"
    led.append(CallCost(run=ws.root.name, backend="gemini", model="gemini-3.7-flash", cost_usd=0.25))
    _reconcile_billed_from_ledger(guard, ws, EventLog(ws.events_path))
    assert guard.billed_usd == pytest.approx(0.25), "the API-billed row still counts in full"
