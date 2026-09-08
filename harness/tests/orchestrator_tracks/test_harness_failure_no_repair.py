"""A build that failed IN THE HARNESS never becomes an agent repair.

Measured 2026-09-05 on `bench/out/scene_textures` (japanese_garden): the scene probe
returned no summary line — a dropped stdout tail, which `probes.probe_report` already
labels "scene probe produced no result (driver output lost)" with
`harness_failure: True` and the hint "this is a harness/driver failure, not your code".
`build_with_repair` never read that flag, so the round spent all three repair attempts on
it: the agent rewrote 5 files, then **14** (env.js and every zone), then 3, and the
fourth build passed on its own.  The 14-file rewrite deleted the texture use the arm
existed to measure — the cell scored 0.496 where the same prompt scored 0.636 without the
detour.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.tracks import common as C
from codeverse.tracks import repair as R


def _build(*, ok: bool, harness: bool = False, msg: str = "boom") -> BuildResult:
    return BuildResult(ok=ok, language="scene_threejs", error_message=msg, harness_failure=harness)


def _ctx(**over):
    base = SimpleNamespace(
        spec=SimpleNamespace(budget=SimpleNamespace(max_repair_attempts=3)),
        events=SimpleNamespace(emit=lambda *a, **k: None),
        ws=None, agent_id="x", agent=None, model=None, settings=None, budget=None,
        policy=SimpleNamespace(agent_max_turns=8, wrapup_turns=1, agent_wrapup_turns=1),
        cookbook_text="",
    )
    base.__dict__.update(over)
    return base


@pytest.fixture
def no_generate(monkeypatch):
    """Any call to the model is a failure of the thing under test."""
    calls: list[str] = []

    def boom(*a, **k):
        calls.append("generate")
        raise AssertionError("the agent must not be asked to repair a harness failure")

    monkeypatch.setattr(C, "generate", boom)
    return calls


def test_a_harness_failure_is_rebuilt_not_repaired(monkeypatch, no_generate):
    """The build is simply re-run — no model call — and a rebuild that succeeds ends it."""
    seq = [(_build(ok=False, harness=True, msg="scene probe produced no result (driver output lost)"),
            GateReport(gate="lint", passed=True, findings=[])),
           (_build(ok=True), GateReport(gate="lint", passed=True, findings=[]))]
    calls = {"n": 0}

    def fake_build_once(ctx):
        calls["n"] += 1
        return seq[min(calls["n"] - 1, len(seq) - 1)]

    monkeypatch.setattr(R, "build_once", fake_build_once)
    out = R.build_with_repair(_ctx(), round_index=0, label="r00")
    assert out.ok is True
    assert calls["n"] == 2, "one rebuild, no repair"
    assert out.attempts == [], "no agent attempt was spent"


def test_a_harness_failure_that_persists_gives_up_without_a_repair(monkeypatch, no_generate):
    """Bounded: the driver already retries once itself, so a failure that survives
    MAX_HARNESS_REBUILDS is not transient and is not the agent's to fix either."""
    stuck = (_build(ok=False, harness=True), GateReport(gate="lint", passed=True, findings=[]))
    calls = {"n": 0}

    def fake_build_once(ctx):
        calls["n"] += 1
        return stuck

    monkeypatch.setattr(R, "build_once", fake_build_once)
    out = R.build_with_repair(_ctx(), round_index=0, label="r00")
    assert out.ok is False
    assert calls["n"] == 1 + R.MAX_HARNESS_REBUILDS
    assert out.attempts == []


def test_a_real_build_failure_is_still_repaired(monkeypatch):
    """The control: an ordinary failure still reaches the agent, as it always did."""
    seen: list[int] = []

    from codeverse.contracts.common import Usage

    def fake_generate(ws, **kw):
        seen.append(1)
        return SimpleNamespace(ok=True, files_changed=[], usage=Usage(), notes="", storm=False, transient=False)

    monkeypatch.setattr(C, "generate", fake_generate)
    monkeypatch.setattr(R, "make_repair_task", lambda *a, **k: SimpleNamespace(label="t"))
    seq = [(_build(ok=False, msg="TypeError: x is not a function"), GateReport(gate="lint", passed=True, findings=[])),
           (_build(ok=True), GateReport(gate="lint", passed=True, findings=[]))]
    calls = {"n": 0}

    def fake_build_once(ctx):
        calls["n"] += 1
        return seq[min(calls["n"] - 1, len(seq) - 1)]

    monkeypatch.setattr(R, "build_once", fake_build_once)
    out = R.build_with_repair(_ctx(), round_index=0, label="r00")
    assert out.ok is True and len(seen) == 1, "a real error still gets one repair"


def test_a_repair_session_that_died_in_the_storm_ends_the_loop(monkeypatch):
    """D68: a repair session that timed out in a 503 streak (partial or no files) is built once
    and the loop stops — the next attempt would spend its whole window at the same wall."""
    seen: list[int] = []

    from codeverse.contracts.common import Usage

    def fake_generate(ws, **kw):
        seen.append(1)
        return SimpleNamespace(ok=False, files_changed=[], usage=Usage(), notes="exit=timeout", storm=False, transient=True)

    monkeypatch.setattr(C, "generate", fake_generate)
    monkeypatch.setattr(R, "make_repair_task", lambda *a, **k: SimpleNamespace(label="t"))
    calls = {"n": 0}

    def fake_build_once(ctx):
        calls["n"] += 1
        return _build(ok=False, msg="TypeError: x is not a function"), GateReport(gate="lint", passed=True, findings=[])

    monkeypatch.setattr(R, "build_once", fake_build_once)
    out = R.build_with_repair(_ctx(), round_index=0, label="r00", max_attempts=3)
    assert out.ok is False and len(seen) == 1 and calls["n"] == 2, "one storm-dead repair, one rebuild, then stop"


def test_a_harness_failure_is_rebuilt_even_with_no_repair_budget(monkeypatch, no_generate):
    """The rebuilds are bounded by MAX_HARNESS_REBUILDS, not by the repair budget: a
    baseline arm that runs with max_repair_attempts=0 must still get its retries, or a
    dropped stdout tail costs it the whole round."""
    seq = [(_build(ok=False, harness=True, msg="scene probe produced no result (driver output lost)"),
            GateReport(gate="lint", passed=True, findings=[])),
           (_build(ok=True), GateReport(gate="lint", passed=True, findings=[]))]
    calls = {"n": 0}

    def fake_build_once(ctx):
        calls["n"] += 1
        return seq[min(calls["n"] - 1, len(seq) - 1)]

    monkeypatch.setattr(R, "build_once", fake_build_once)
    ctx = _ctx(spec=SimpleNamespace(budget=SimpleNamespace(max_repair_attempts=0)))
    out = R.build_with_repair(ctx, round_index=0, label="r00")
    assert out.ok is True and calls["n"] == 2 and out.attempts == []
