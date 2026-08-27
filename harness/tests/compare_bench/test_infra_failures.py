"""A provider outage must never be scored as a model failure.

Regression (2026-08-24): a multi-hour gemini-3.7-flash 503 storm hit both arms of
compare_v2.  ``compare_backends`` recorded it as ``score=0.0`` on the one-shot path
(counted in the mean) and ``score=None`` on the harness path (dropped), so the same
downtime pushed one-shot means down while leaving harness means untouched.
"""

from __future__ import annotations

import pytest

from bench._compare_report import CellResult, arm_stats
from bench._infra import is_infra_failure

OUTAGES = [
    "ModelError: Gemini API error 503: This model is currently experiencing high demand.",
    "ModelError: Gemini request timed out: The read operation timed out",
    "ModelError: Gemini API error 529: is overloaded",
    "KeyPoolExhausted: every key is cooling down",
    "APIError: 500 Internal server error",
    "ConnectionError: Connection reset by peer",
    "RenderError: render_glb failed: node script render_glb.mjs exited 1: Error creating WebGL context.",
]
MODEL_FAILURES = [
    "unparseable answer: no python block in the response",
    "empty answer",
    "SyntaxError: invalid syntax. Perhaps you forgot a comma? (model.py, line 261)",
    "BuildError: bpy.data.objects['Cube'] not found",
    "the model returned prose instead of code",
]


@pytest.mark.parametrize("err", OUTAGES)
def test_provider_outages_are_infra(err):
    assert is_infra_failure(err) is True


@pytest.mark.parametrize("err", MODEL_FAILURES)
def test_model_failures_are_not_infra(err):
    """These are real capability results and must keep their zero."""
    assert is_infra_failure(err) is False


def test_structured_exceptions_beat_string_matching():
    class ModelError(Exception):
        def __init__(self, msg, status):
            super().__init__(msg)
            self.status = status

    # no recognisable prose at all — only the status says what happened
    assert is_infra_failure(ModelError("upstream said no", status=503)) is True
    assert is_infra_failure(ModelError("bad request: prompt too long", status=400)) is False
    assert is_infra_failure(TimeoutError("read timed out")) is True
    # a wrapped cause still counts
    err = ValueError("generation failed")
    err.__cause__ = ModelError("overloaded", status=529)
    assert is_infra_failure(err) is True


def test_outage_cells_leave_every_rate_alone():
    """The bug in one assertion: an arm hit by downtime must score the same as one
    that ran in the clear, and the loss must be reported rather than hidden."""
    clear = [CellResult(prompt_id=f"p{i}", arm="A", kind="oneshot", status="scored",
                        score=0.8, passed=True, build_ok=True, wall_s=60.0) for i in range(4)]
    unlucky = [*[c.model_copy(update={"arm": "B"}) for c in clear],
               CellResult(prompt_id="p9", arm="B", kind="oneshot", status="infra_failed",
                          score=None, passed=None, build_ok=False, wall_s=3600.0)]
    a, b = {s.arm: s for s in arm_stats([*clear, *unlucky])}["A"], {s.arm: s for s in arm_stats([*clear, *unlucky])}["B"]
    assert a.mean_score == b.mean_score, "downtime changed the score"
    assert a.build_ok_rate == b.build_ok_rate, "downtime changed the build rate"
    assert a.mean_minutes == b.mean_minutes, "an hour spent retrying a 503 is not model latency"
    assert b.infra_failed == 1 and b.n_evaluated == 4 and b.n == 5, "the loss must stay visible"


def test_both_failure_paths_classify_the_same_way(tmp_path):
    """CQ-1, the asymmetry itself: ONE error hits both arms, both must reach one verdict.

    The previous version of this test could not fail — it grepped run_cell's code
    constants for the literal "infra_failed" and then compared a single 503 string that
    DOES contain one of the 14 INFRA_MARKERS.  It therefore passed straight through the
    real bug: gemini's actual 500 prose ("An internal error has occurred") matches no
    marker, and bench/_oneshot.py stringified the exception into `notes`, throwing away
    the .status the harness path classifies on.  Same outage, harness dropped
    (infra_failed / score None), one-shot scored a hard 0.0 — biasing every
    harness-vs-one-shot mean in the harness's favour, which is precisely what this
    module was written to end.
    """
    import tempfile
    from pathlib import Path

    from bench._oneshot import ApiOneShot
    from bench.compare_backends import CompareDeps, CompareOptions, parse_arm, run_cell
    from bench.run_bench import Battery
    from codeverse.models.base import ModelError
    from tests.compare_bench.conftest import BATTERY, FakeEvaluator

    def verdicts(outage: Exception, out: Path) -> dict[str, tuple[str, float | None]]:
        class DeadModel:  # the real ApiOneShot around a model that raises the outage
            provider, model = "gemini", "gemini-3.7-flash"

            def supports_vision(self):
                return True

            def generate(self, request):
                raise outage

        def dying_track(spec, ws, resume):
            raise outage

        battery = Battery.load(BATTERY)
        deps = CompareDeps(FakeEvaluator(), run_track=dying_track,
                           oneshot_backend=lambda t: ApiOneShot("gemini:gemini-3.7-flash", chat_model=DeadModel()))
        opts = CompareOptions(judge="gemini:x", loop_judge="gemini:x")
        got = {}
        for raw in ("harness:gemini-cli:gemini-3.6-flash", "oneshot:gemini:gemini-3.7-flash"):
            arm = parse_arm(raw)
            r = run_cell(battery, battery.prompts[0], arm, out, opts, deps)
            got[arm.kind] = (r.status, r.score)
        return got

    for outage in (
        ModelError("Gemini API error 500: An internal error has occurred.", retryable=True, status=500),
        ModelError("Gemini transport error: [Errno 104] Connection reset by peer", retryable=True),
        ModelError("Anthropic connection error: TLS handshake failed", retryable=True),
        ModelError("Gemini request timed out: 600s", retryable=True, status=408),
    ):
        got = verdicts(outage, Path(tempfile.mkdtemp(dir=tmp_path)))
        assert got["harness"] == ("infra_failed", None), f"{outage} / harness -> {got}"
        assert got["oneshot"] == got["harness"], (
            f"one error, two verdicts for {outage!s}: {got} — the one-shot arm takes a hard "
            f"zero for the same downtime that drops the harness arm")


def test_a_real_capability_failure_still_keeps_its_zero():
    """The other half of the contract: widening the classifier must not start excusing
    models.  Prose, unparseable code and a refusal are results, not outages."""
    from codeverse.models.base import ModelError

    for not_an_outage in (
        "unparseable answer: no code fence found",
        ModelError("Gemini finish_reason=SAFETY", retryable=True),
        ModelError("Gemini returned no candidates", retryable=True),
        ModelError("Anthropic refused the request: None", retryable=False),
        "empty answer",
    ):
        assert not is_infra_failure(not_an_outage), not_an_outage


def test_budget_exhaustion_is_scoreless_but_still_counts_against_build_rate():
    """The 50-minutes-for-nothing case: no score to average, but the arm did miss."""
    from bench._compare_report import CellResult, arm_stats

    rows = [CellResult(prompt_id="p1", arm="A", kind="harness", status="scored",
                       score=0.9, passed=True, build_ok=True),
            CellResult(prompt_id="p2", arm="A", kind="harness", status="budget_exhausted",
                       score=None, passed=False, build_ok=False)]
    s = arm_stats(rows)[0]
    assert s.mean_score == 0.9, "a cell with no artifact cannot contribute a score"
    assert s.build_ok_rate == 0.5, "but it must not be excused on the build rate either"
    assert s.budget_exhausted == 1 and s.infra_failed == 0, "and it is reported separately from an outage"


def test_budget_and_outage_are_different_buckets():
    from bench._infra import is_budget_exhaustion, is_infra_failure

    budget = "harness run produced no src/model.py (status budget: elapsed 50.2 min exceeds max_minutes 45.0)"
    outage = "ModelError: Gemini API error 503: This model is currently experiencing high demand."
    assert is_budget_exhaustion(budget) and not is_infra_failure(budget)
    assert is_infra_failure(outage) and not is_budget_exhaustion(outage)


def test_preflight_probes_every_model_a_harness_arm_needs(monkeypatch):
    """A harness arm is only as available as the weakest model in its loop.

    Regression (2026-08-24): `harness:codex:gpt-5.6-sol` — a local subscription CLI
    generator with a healthy judge — was stuck for hours because the default PLANNER
    is gemini-3.7-flash, which was down.  The first preflight only probed the judge
    and the arm targets, so it would have waved those runs straight into the wall.
    """
    from bench import compare_backends as cb
    from codeverse.models.health import Health

    probed: list[str] = []

    def fake_probe(model, **_):
        probed.append(model)
        return Health(model=model, n_ok=4, n_tried=4)

    monkeypatch.setattr("codeverse.models.health.probe", fake_probe)
    arms = cb.parse_arms("harness:codex:gpt-5.6-sol")
    opts = cb.CompareOptions(judge="gemini:fixed-judge")
    assert cb._preflight("gemini:fixed-judge", arms, opts) is True

    assert "gemini:fixed-judge" in probed, "the fixed judge must always be probed"
    planner = get_settings_planner()
    assert planner in probed, f"the harness planner {planner!r} was not probed: {probed}"
    # the subscription CLI is not an API model and has nothing to probe
    assert not any("codex" in m for m in probed), probed


def get_settings_planner() -> str:
    from codeverse.config import get_settings

    return get_settings().backends().planner


def test_preflight_skips_loop_models_for_oneshot_only_batteries(monkeypatch):
    """A one-shot battery runs no harness loop, so it must not be blocked by a
    planner it will never call."""
    from bench import compare_backends as cb
    from codeverse.models.health import Health

    probed: list[str] = []
    monkeypatch.setattr("codeverse.models.health.probe",
                        lambda m, **_: (probed.append(m), Health(model=m, n_ok=4, n_tried=4))[1])
    arms = cb.parse_arms("oneshot:claude-code,oneshot:codex")
    assert cb._preflight("gemini:fixed-judge", arms, cb.CompareOptions(judge="gemini:fixed-judge")) is True
    assert probed == ["gemini:fixed-judge"], probed


def test_a_repair_lost_to_an_outage_drops_the_cell_instead_of_scoring_the_pre_repair_code(tmp_path):
    """compare_v4, 2026-08-25: four `oneshot+repair` cells whose repair call died in a 503
    storm were recorded build_failed / score 0 — the broken attempt-0 file was already in
    the eval workspace, so the arm was scored on code its protocol had not finished with.
    A truncated one-shot protocol is downtime, not a capability result: infra_failed, and
    --redo-status re-runs only the lost attempt (attempt 0 is cached)."""
    from bench._oneshot import OneShotResult
    from bench.compare_backends import CompareDeps, CompareOptions, parse_arm, run_cell
    from bench.run_bench import Battery
    from codeverse.contracts.common import Usage
    from tests.compare_bench.conftest import BATTERY, GOOD, FakeEvaluator

    broken = GOOD.format(score=0.5).replace("\n", "\n# BOOM\n", 1)

    class StormBackend:
        id = "oneshot:fake"

        def __init__(self, storm_on_repair: bool):
            self.calls, self.storm_on_repair = 0, storm_on_repair

        def generate(self, prompt, *, out_dir, timeout_s=0, label=""):
            self.calls += 1
            out_dir.mkdir(parents=True, exist_ok=True)
            if self.calls == 1 or not self.storm_on_repair:
                return OneShotResult(ok=True, text=broken, usage=Usage(cost_usd=0.02), duration_s=1.0,
                                     transcript_dir=str(out_dir))
            return OneShotResult(ok=False, text="", usage=Usage(), duration_s=1.0, transcript_dir=str(out_dir),
                                 notes="ModelError: Gemini API error 503: This model is currently experiencing high demand.",
                                 infra_failed=True)

    battery = Battery.load(BATTERY)
    opts = CompareOptions(judge="gemini:x", loop_judge="gemini:x", repair_attempts=1)
    arm = parse_arm("oneshot+repair:gemini:gemini-3.7-flash")

    ev = FakeEvaluator()
    r = run_cell(battery, battery.prompts[0], arm, tmp_path / "storm", opts,
                 CompareDeps(ev, oneshot_backend=lambda t: StormBackend(storm_on_repair=True)))
    assert (r.status, r.score, r.error_is_infra) == ("infra_failed", None, True), r
    assert ev.evaluated == [], "the pre-repair code must not be judged"
    assert "503" in r.error

    # the same broken code with the repair delivered (and still broken) keeps its earned zero
    ev2 = FakeEvaluator()
    r2 = run_cell(battery, battery.prompts[0], arm, tmp_path / "calm", opts,
                  CompareDeps(ev2, oneshot_backend=lambda t: StormBackend(storm_on_repair=False)))
    assert (r2.status, r2.score, r2.build_ok) == ("build_failed", 0.0, False), r2
    assert ev2.evaluated and r2.attempts == 2


def test_a_harness_planning_failure_is_a_zero_not_a_dropped_cell(tmp_path):
    """compare_art_v2 (2026-08-25): 5 of 14 articulated harness runs raised PlanningError (the
    plan failed validation twice) and were recorded `error` / score None — dropped from the
    mean, so a third of the harness's failures vanished.  The harness delivered nothing by its
    own doing: no_code / 0.0, exactly what a one-shot answer in the wrong format gets."""
    from bench.compare_backends import CompareDeps, CompareOptions, parse_arm, run_cell
    from bench.run_bench import Battery
    from codeverse.tracks.planner import PlanningError
    from tests.compare_bench.conftest import BATTERY, FakeEvaluator

    def no_plan(spec, ws, resume):
        raise PlanningError("plan did not validate after re-ask: joint X references unknown link(s)")

    battery = Battery.load(BATTERY)
    opts = CompareOptions(judge="gemini:x", loop_judge="gemini:x")
    r = run_cell(battery, battery.prompts[0], parse_arm("harness:gemini-cli:gemini-3.6-flash"), tmp_path, opts,
                 CompareDeps(FakeEvaluator(), run_track=no_plan))
    assert (r.status, r.score, r.passed, r.build_ok) == ("no_code", 0.0, False, False)
    assert r.error.startswith("PlanningError:")


def test_a_cyclic_cause_chain_does_not_recurse():
    """compare_art_v3 (2026-08-27): retry.py's `raise err from exc` closed the __cause__ chain
    into a cycle; is_infra_failure recursed to RecursionError inside run_cell's except
    handler, the matrix loop died, and 11 finished cells went unrecorded."""
    class ModelError(Exception):
        def __init__(self, msg, status=None):
            super().__init__(msg)
            self.status = status

    a = ModelError("structured output unavailable (finish_reason=MAX_TOKENS)")
    b = ModelError("attempt failed")
    a.__cause__, b.__cause__ = b, a  # the cycle
    assert is_infra_failure(a) is False
    c = ModelError("wrapped")
    c.__cause__ = ModelError("upstream said no", status=503)
    c.__cause__.__cause__ = c  # cycle through an infra cause
    assert is_infra_failure(c) is True


def test_a_classifier_crash_still_records_the_cell(tmp_path, monkeypatch):
    from bench import compare_backends as cb
    from bench.run_bench import Battery
    from tests.compare_bench.conftest import BATTERY, FakeEvaluator

    def boom(spec, ws, resume):
        raise RuntimeError("planner died")

    monkeypatch.setattr(cb, "is_infra_failure", lambda e: (_ for _ in ()).throw(RecursionError("cycle")))
    battery = Battery.load(BATTERY)
    r = cb.run_cell(battery, battery.prompts[0], cb.parse_arm("harness:api-agent:gemini:gemini-3.7-flash"), tmp_path,
                    cb.CompareOptions(judge="gemini:x", loop_judge="gemini:x"), cb.CompareDeps(FakeEvaluator(), run_track=boom))
    assert r.status == "error" and "classifier failed: RecursionError" in r.error and "planner died" in r.error
    assert (tmp_path / "cells" / battery.prompts[0].id / "harness_api-agent_gemini_gemini-3.7-flash" / "cell.json").is_file()

