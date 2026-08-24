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


def test_both_failure_paths_classify_the_same_way():
    """The asymmetry itself: whichever path records the outage, the verdict matches."""
    from bench import compare_backends as cb

    src = cb.run_cell.__code__.co_consts
    assert any(c == "infra_failed" for c in src if isinstance(c, str)), \
        "the no-code path must be able to record infra_failed"
    outage = "Gemini API error 503: This model is currently experiencing high demand."
    assert is_infra_failure(outage) == is_infra_failure(RuntimeError(outage))


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
