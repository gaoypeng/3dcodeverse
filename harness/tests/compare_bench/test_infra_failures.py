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

    for outage in (
        ModelError("Gemini API error 500: An internal error has occurred.", retryable=True, status=500),
        ModelError("Gemini transport error: [Errno 104] Connection reset by peer", retryable=True),
        ModelError("Anthropic connection error: TLS handshake failed", retryable=True),
        ModelError("Gemini request timed out: 600s", retryable=True, status=408),
    ):
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
        out = Path(tempfile.mkdtemp(dir=tmp_path))
        got = {}
        for raw in ("harness:api-agent:gemini:gemini-3.7-flash", "oneshot:gemini:gemini-3.7-flash"):
            arm = parse_arm(raw)
            r = run_cell(battery, battery.prompts[0], arm, out, opts, deps)
            got[arm.kind] = (r.status, r.score)
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
