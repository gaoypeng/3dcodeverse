"""Call attribution: explicit > a label naming its own job > ambient > the label."""

from __future__ import annotations

from codeverse.cost.context import (
    CallContext,
    attribute,
    bound_run,
    call_context,
    context_from_label,
    current,
)
from codeverse.cost.types import Role, Stage


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


def test_repair_label_wins_over_the_label_it_repairs():
    assert context_from_label("baseline_repair1").stage is Stage.REPAIR


def test_a_generation_label_yields_to_the_session_and_explicit_beats_both():
    # best-of-N: the session knows it is a candidate, a call inside it only says "baseline"
    with call_context(round=3, stage=Stage.CANDIDATE, label="cand_c1"):
        got = attribute(label="baseline")
        assert got.stage is Stage.CANDIDATE and got.round == 3
        assert got.label == "baseline"  # the most specific label is kept
        forced = attribute(CallContext(stage=Stage.REPAIR, round=9), label="baseline")
        assert forced.stage is Stage.REPAIR and forced.round == 9


def test_a_call_that_names_its_own_job_beats_the_session_it_runs_inside():
    """The verifier's finding: a spatial tool that bills a model inside a refine
    session was recorded as stage=refine / role=generator."""
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


def test_nested_contexts_merge_and_unwind():
    with bound_run("run-a"):
        assert current().run == "run-a"
        with call_context(stage=Stage.BASELINE, round=0):
            with call_context(label="inner"):
                inner = current()
                assert inner.stage is Stage.BASELINE and inner.round == 0 and inner.label == "inner"
            assert current().label == ""
        assert current().stage is None and current().run == "run-a"
    assert current().run == ""


def test_a_run_bound_in_a_thread_is_never_published_to_the_others():
    """The 2026-08-30 leak: the binding also lived in a process global, so once the
    first parallel run exited it republished ITS name for every later caller."""
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


def test_role_is_derived_from_stage_when_unstated():
    assert attribute(CallContext(stage=Stage.ZONES)).role is Role.GENERATOR
    assert attribute(CallContext(stage=Stage.PAIRWISE)).role is Role.JUDGE
    # the scene track's compose task is an agent session, not deterministic assembly
    assert attribute(CallContext(stage=Stage.ASSEMBLE)).role is Role.GENERATOR


def test_the_static_track_detail_round_is_a_refine_pass():
    """``DEFAULT_DETAIL_ROUNDS=1``, so every static run bills one of these; until
    2026-08-30 no prefix matched and the whole round landed under ``other``."""
    for label in ("detail", "detail_seat_edge"):
        got = attribute(label=label)
        assert got.stage is Stage.REFINE and got.role is Role.GENERATOR, label
