"""Call attribution: explicit > a label naming its own job > ambient > the label."""

from __future__ import annotations

from codeverse3d.cost.context import (
    CallContext,
    attribute,
    bound_run,
    call_context,
    context_from_label,
    current,
)
from codeverse3d.cost.types import Role, Stage


def test_label_tells_stage_role_and_round():
    c = context_from_label("refine_drip_tray")
    assert c.stage is Stage.REFINE and c.role is None  # a generation label states no role...
    assert attribute(label="refine_drip_tray").role is Role.GENERATOR  # ...the stage derives it
    c = context_from_label("judge:static_object_v1:r02:s1")
    assert c.stage is Stage.JUDGE and c.role is Role.JUDGE and c.round == 2
    assert context_from_label("planner-retry").stage is Stage.PLAN
    assert context_from_label("pairwise:0v1:fwd").stage is Stage.PAIRWISE
    assert context_from_label("texture_plan").stage is Stage.TEXTURE
    assert context_from_label("captioner").role is Role.CAPTIONER
    assert context_from_label("").stage is None
    assert context_from_label("baseline_repair1").stage is Stage.REPAIR  # repair wins
    assert attribute(label="detail_seat_edge").stage is Stage.REFINE  # the static detail round
    zl = attribute(label="zone-layout")  # the ledger agrees with the guard's stage='plan'
    assert zl.stage is Stage.PLAN and zl.role is Role.PLANNER


def test_session_beats_a_generation_label_a_named_job_beats_the_session_explicit_beats_all():
    # best-of-N: the session knows it is a candidate, a call inside it only says "baseline"
    with call_context(round=3, stage=Stage.CANDIDATE, label="cand_c1"):
        got = attribute(label="baseline")
        assert got.stage is Stage.CANDIDATE and got.round == 3
        assert got.label == "baseline"  # the most specific label is kept
        forced = attribute(CallContext(stage=Stage.REPAIR, round=9), label="baseline")
        assert forced.stage is Stage.REPAIR and forced.round == 9
    # a spatial tool that bills a model inside a refine session is not a refine call
    with call_context(round=2, stage=Stage.REFINE, role=Role.GENERATOR, label="refine_seat"):
        tex = attribute(label="texture_plan")
        assert tex.stage is Stage.TEXTURE and tex.role is Role.OTHER and tex.round == 2
        gate = attribute(label="texture_gate")
        assert gate.stage is Stage.TEXTURE and gate.role is Role.JUDGE
        verdict = attribute(label="judge:static_object_v1:r02:s0")
        assert verdict.stage is Stage.JUDGE and verdict.role is Role.JUDGE and verdict.round == 2
        cap = attribute(label="caption:best")
        assert cap.stage is Stage.CAPTION and cap.role is Role.CAPTIONER
        # a label that names nothing keeps the session's attribution
        anon = attribute(label="summarise")
        assert anon.stage is Stage.REFINE and anon.role is Role.GENERATOR


def test_a_run_bound_in_a_thread_is_never_published_to_the_others():
    """The 2026-08-30 leak: a process-global binding republished one parallel run's name."""
    import threading

    ready, seen = threading.Barrier(3), {}

    def one(name: str) -> None:
        with bound_run(name):
            ready.wait(timeout=5)
            seen[name] = current().run

    threads = [threading.Thread(target=one, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    ready.wait(timeout=5)
    for t in threads:
        t.join(timeout=5)
    assert seen == {"a": "a", "b": "b"}
    assert current().run == ""  # ...and the main thread never inherited either of them
