"""A provider outage must never be scored as a model failure (compare_v2, 2026-08-24)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench._compare_report import CellResult, arm_stats
from bench._infra import is_infra_failure

OUTAGES = [
    "ModelError: Gemini API error 503: This model is currently experiencing high demand.",
    "ModelError: Gemini request timed out: The read operation timed out",
    "ModelError: Gemini API error 529: is overloaded",
    "Gemini API error 504: Deadline expired before operation could complete.",
    "ModelError: Gemini stream exceeded its attempt budget after 293 chunks",
    "ModelError: structured output unavailable (finish_reason=PROHIBITED_CONTENT; raise max_output_tokens if truncated)",
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


@pytest.mark.parametrize("err, infra", [(e, True) for e in OUTAGES] + [(e, False) for e in MODEL_FAILURES])
def test_outages_are_infra_and_capability_failures_keep_their_zero(err, infra):
    assert is_infra_failure(err) is infra


class ModelError(Exception):
    """Stands in for codeverse3d.models.base.ModelError: the .status the classifier reads."""
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class ClassifierTrap(Exception):
    """A .status that raises: the cheapest stand-in for any classifier bug."""
    @property
    def status(self):
        raise RecursionError("cycle")


def test_structured_exceptions_beat_string_matching():
    from codeverse3d.models.base import ModelError as ProviderError

    # no recognisable prose at all — only the status says what happened
    assert is_infra_failure(ModelError("upstream said no", status=503)) is True
    assert is_infra_failure(ModelError("bad request: prompt too long", status=400)) is False
    assert is_infra_failure(TimeoutError("read timed out")) is True
    # a wrapped cause still counts
    err = ValueError("generation failed")
    err.__cause__ = ModelError("overloaded", status=529)
    assert is_infra_failure(err) is True
    for capability_failure in (
        ProviderError("Gemini finish_reason=SAFETY", retryable=True),
        ProviderError("Gemini returned no candidates", retryable=True),
        ProviderError("Anthropic refused the request: None", retryable=False),
    ):
        assert is_infra_failure(capability_failure) is False


def test_both_failure_paths_classify_the_same_way(tmp_path):
    """CQ-1: one outage hits both arms and both reach one verdict — gemini's 500 prose matches no marker."""
    from bench._oneshot import ApiOneShot
    from bench.compare_backends import CompareDeps, CompareOptions, parse_arm, run_cell
    from bench.run_bench import Battery
    from codeverse3d.models.base import ModelError
    from tests.conftest import BATTERY, FakeEvaluator

    def verdicts(outage: Exception, out: Path) -> dict[str, tuple[str, float | None]]:
        class DeadModel:  # the real ApiOneShot around a model that raises the outage
            provider, model = "gemini", "gemini-3.7-flash"

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

    for i, outage in enumerate((
        ModelError("Gemini API error 500: An internal error has occurred.", retryable=True, status=500),
        ModelError("Gemini transport error: [Errno 104] Connection reset by peer", retryable=True),
    )):
        got = verdicts(outage, tmp_path / str(i))
        assert got["harness"] == ("infra_failed", None), f"{outage} / harness -> {got}"
        assert got["oneshot"] == got["harness"], (
            f"one error, two verdicts for {outage!s}: {got}")


def test_budget_exhaustion_is_scoreless_but_still_counts_against_build_rate():
    """The 50-minutes-for-nothing case: no score to average, but the arm did miss."""

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


@pytest.mark.parametrize(("raw_arms", "needs_loop"), [
    ("harness:codex:gpt-5.6-sol", True),
    ("oneshot:claude-code,oneshot:codex", False),
])
def test_preflight_probes_only_models_the_selected_arms_need(monkeypatch, raw_arms, needs_loop):
    from bench import compare_backends as cb
    from codeverse3d.config import get_settings
    from codeverse3d.models.health import Health

    probed: list[str] = []

    def fake_probe(model, **_):
        probed.append(model)
        return Health(model=model, n_ok=4, n_tried=4)

    monkeypatch.setattr("codeverse3d.models.health.probe", fake_probe)
    opts = cb.CompareOptions(judge="gemini:fixed-judge")
    assert cb._preflight(opts.judge, cb.parse_arms(raw_arms), opts) is True
    expected = {opts.judge}
    if needs_loop:
        backends = get_settings().backends()
        expected |= {backends.planner, backends.judge}
    assert set(probed) == expected
    assert not any("codex" in model for model in probed), "subscription CLIs are not probed"


def test_a_repair_lost_to_an_outage_drops_the_cell_instead_of_scoring_the_pre_repair_code(tmp_path):
    """A repair call lost to a 503 is downtime, not a score on the unfinished attempt-0 code (compare_v4)."""
    from bench._oneshot import OneShotResult
    from bench.compare_backends import CompareDeps, CompareOptions, parse_arm, run_cell
    from bench.run_bench import Battery
    from codeverse3d.contracts.common import Usage
    from tests.conftest import BATTERY, GOOD, FakeEvaluator

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
    """PlanningError is the harness's own failure: no_code / 0.0, never dropped (compare_art_v2)."""
    from bench.compare_backends import CompareDeps, CompareOptions, parse_arm, run_cell
    from bench.run_bench import Battery
    from codeverse3d.tracks.planner import PlanningError
    from tests.conftest import BATTERY, FakeEvaluator

    def no_plan(spec, ws, resume):
        raise PlanningError("plan did not validate after re-ask: joint X references unknown link(s)")

    battery = Battery.load(BATTERY)
    opts = CompareOptions(judge="gemini:x", loop_judge="gemini:x")
    r = run_cell(battery, battery.prompts[0], parse_arm("harness:gemini-cli:gemini-3.6-flash"), tmp_path, opts,
                 CompareDeps(FakeEvaluator(), run_track=no_plan))
    assert (r.status, r.score, r.passed, r.build_ok) == ("no_code", 0.0, False, False)
    assert r.error.startswith("PlanningError:")


def test_a_cyclic_cause_chain_does_not_recurse():
    """retry.py's `raise e from e` closes the __cause__ chain into a cycle (compare_art_v3)."""
    self_loop = ModelError("structured output unavailable (finish_reason=MAX_TOKENS)")
    self_loop.__cause__ = self_loop
    assert is_infra_failure(self_loop) is False
    c = ModelError("wrapped")
    c.__cause__ = ModelError("upstream said no", status=503)
    c.__cause__.__cause__ = c  # cycle through an infra cause
    assert is_infra_failure(c) is True


def test_a_classifier_crash_still_records_the_cell(tmp_path):
    """End to end: a failure whose classification blows up is still a row on disk."""
    from bench import compare_backends as cb
    from bench.run_bench import Battery
    from tests.conftest import BATTERY, FakeEvaluator

    def boom(spec, ws, resume):
        raise ClassifierTrap("planner died")

    battery = Battery.load(BATTERY)
    r = cb.run_cell(battery, battery.prompts[0], cb.parse_arm("harness:gemini-cli:gemini-3.6-flash"), tmp_path,
                    cb.CompareOptions(judge="gemini:x", loop_judge="gemini:x"), cb.CompareDeps(FakeEvaluator(), run_track=boom))
    assert r.status == "error" and "planner died" in r.error
    assert (Path(r.workspace) / "cell.json").is_file()


def test_the_ab_viewer_refuses_to_call_a_winner_it_cannot_support():
    """B − A per brief — the pairs the page shows — and the 95 % t-interval of its mean."""
    from bench.ab_view import Run, verdict

    def arm(name, *scores):
        return [Run(slug=f"{name}{i}", arm=name, brief=f"brief {i}", picked=s, status="max_rounds")
                for i, s in enumerate(scores)]

    cases = [
        ((0.7,), (0.9,), "Inconclusive"),                                 # one pair
        ((0.70, 0.72), (0.74, 0.76), "Inconclusive"),                     # two pairs
        ((0.50, 0.70, 0.90), (0.56, 0.66, 0.96), "Inconclusive"),         # +0.06 / -0.04 / +0.06
        ((0.30, 0.32, 0.31), (0.80, 0.82, 0.81), "B wins"),
        # the same +0.06 on every brief is a consistent paired gain: the unpaired rule this
        # replaced called it noise because the BRIEFS differ by more than 0.06 — the very
        # variance pairing on the brief removes
        ((0.50, 0.70, 0.90), (0.56, 0.76, 0.96), "B wins"),
    ]
    for a_scores, b_scores, expected in cases:
        head, _ = verdict(arm("a", *a_scores), arm("b", *b_scores))
        assert head.startswith(expected), head

    # a run that never scored must not be counted as an observation
    a = arm("a", 0.5, 0.5) + [Run(slug="a9", arm="a", brief="brief 9", picked=None, status="failed")]
    head, why = verdict(a, arm("b", 0.5))
    assert head.startswith("Inconclusive") and "1 of 1" in why and "2 of 3" in why

    # a brief run twice in one arm is its LAST run, as on the page; a brief only one arm ran is no pair
    b = arm("b", 0.1, 0.1, 0.1) + [Run(slug="b0r", arm="b", brief="brief 0", picked=0.9),
                                   Run(slug="bx", arm="b", brief="brief x", picked=0.0)]
    head, why = verdict(arm("a", 0.5, 0.5, 0.5), b)
    assert head.startswith("Inconclusive") and "over 3 paired briefs" in why


def test_a_harness_run_whose_judge_never_answered_is_dropped_and_redone_fresh(tmp_path):
    """judge_unavailable is the provider's failure, not the harness's: infra_failed, no score — and a redo
    archives the finished run instead of resuming it (p3_graphics_v2 mushroom_forest, 2026-09-23)."""
    from bench.compare_backends import CompareDeps, CompareOptions, parse_arm, run_cell
    from bench.run_bench import Battery
    from codeverse3d.contracts.run import RunStatus
    from tests.conftest import BATTERY, FakeEvaluator, fake_run_track

    storm, calm = fake_run_track(), fake_run_track()

    def judge_down(spec, ws, resume):
        rec = storm(spec, ws, resume)
        rec.status = RunStatus.JUDGE_UNAVAILABLE
        ws.write_json(ws.record_path, rec)
        return rec

    battery = Battery.load(BATTERY)
    opts = CompareOptions(judge="gemini:x", loop_judge="gemini:x")
    arm = parse_arm("harness:gemini-cli:gemini-3.8-flash")
    ev = FakeEvaluator()
    r = run_cell(battery, battery.prompts[0], arm, tmp_path, opts, CompareDeps(ev, run_track=judge_down))
    assert (r.status, r.score, r.passed) == ("infra_failed", None, None), r
    assert not ev.evaluated and "judge never answered" in r.error

    seen: list[bool] = []

    def redo(spec, ws, resume):
        seen.append(resume)
        return calm(spec, ws, resume)

    r2 = run_cell(battery, battery.prompts[0], arm, tmp_path, opts, CompareDeps(FakeEvaluator(), run_track=redo))
    assert seen == [False] and r2.status == "scored", (seen, r2)
    assert any(p.name.startswith("run.attempt") for p in (tmp_path / "cells" / battery.prompts[0].id / arm.slug).iterdir())
