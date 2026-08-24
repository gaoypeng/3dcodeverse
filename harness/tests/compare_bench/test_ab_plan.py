"""bench/ab_plan.py — pairing, verdict rule, outage exclusion, env isolation (offline)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._ab_report import (  # noqa: E402
    CONTROL,
    VARIANT,
    PairOutcome,
    arm_summary,
    pair_up,
    render_summary,
    verdict_of,
)
from bench._compare_report import CellResult, load_jsonl  # noqa: E402
from bench.ab_plan import (  # noqa: E402
    MAX_IN_FLIGHT_ENV,
    AbOptions,
    _plan_todo,
    cell_dir,
    child_env,
    main,
    parse_variant_env,
    run_ab,
    worker_argv,
)
from bench.run_bench import Battery  # noqa: E402

BATTERY = REPO / "bench" / "prompts" / "compare_v1.yaml"


def _row(pid: str, arm: str, score: float | None, status: str = "scored", **kw) -> CellResult:
    return CellResult(prompt_id=pid, arm=arm, kind="harness", score=score, status=status,
                      gen_cost_usd=kw.pop("cost", 1.0), judge_cost_usd=0.1, wall_s=kw.pop("wall", 120.0), **kw)


def _pairs(*deltas: float) -> list[PairOutcome]:
    return [PairOutcome(prompt_id=f"p{i}", paired=True, delta=d) for i, d in enumerate(deltas)]


# ----------------------------------------------------------------------------- verdict rule
def test_keep_needs_mean_gain_and_no_regression():
    assert verdict_of(_pairs(0.03, 0.02, 0.01, 0.04)).decision == "keep"
    # one regression blocks a keep even when the mean clears the bar
    v = verdict_of(_pairs(0.10, 0.10, 0.10, -0.03))
    assert v.decision == "inconclusive" and v.regressions == ["p3"]
    # a mean just under the bar is not a keep
    assert verdict_of(_pairs(0.01, 0.02, 0.02, 0.01)).decision == "inconclusive"


def test_revert_on_mean_loss_or_two_regressions():
    assert verdict_of(_pairs(-0.02, -0.02, -0.02)).decision == "revert"
    v = verdict_of(_pairs(0.20, 0.20, -0.03, -0.05))  # mean +0.08 but two prompts lost
    assert v.decision == "revert" and v.regressions == ["p2", "p3"]
    assert verdict_of(_pairs(-0.019, 0.0)).decision == "inconclusive"


def test_no_pairs_is_inconclusive_never_keep():
    v = verdict_of([PairOutcome(prompt_id="p0", paired=False, reason="control infra_failed")])
    assert v.decision == "inconclusive" and v.n_pairs == 0 and v.mean_delta is None


# ----------------------------------------------------------------------------- pairing
def test_a_pair_counts_only_when_both_arms_scored():
    rows = [_row("a", CONTROL, 0.70), _row("a", VARIANT, 0.75),
            _row("b", CONTROL, 0.80), _row("b", VARIANT, None, "infra_failed"),
            _row("c", CONTROL, None, "budget_exhausted"), _row("c", VARIANT, 0.9),
            _row("d", CONTROL, 0.5)]
    pairs = {p.prompt_id: p for p in pair_up(rows)}
    assert pairs["a"].paired and pairs["a"].delta == pytest.approx(0.05)
    assert not pairs["b"].paired and pairs["b"].reason == "variant infra_failed"
    assert not pairs["c"].paired and pairs["c"].reason == "control budget_exhausted"
    assert not pairs["d"].paired and pairs["d"].reason == "variant missing"
    v = verdict_of(list(pairs.values()))
    assert v.n_pairs == 1 and v.mean_delta == pytest.approx(0.05)


def test_latest_row_wins_and_order_follows_the_battery():
    rows = [_row("a", CONTROL, 0.1), _row("b", CONTROL, 0.2), _row("b", VARIANT, 0.2),
            _row("a", VARIANT, 0.1), _row("a", CONTROL, 0.9)]  # re-appended on a redo
    pairs = pair_up(rows, [("b", "easy"), ("a", "hard")])
    assert [p.prompt_id for p in pairs] == ["b", "a"] and pairs[1].tier == "hard"
    assert pairs[1].delta == pytest.approx(0.1 - 0.9)


def test_outage_cells_are_excluded_from_every_arm_rate():
    """An arm hit by weather must summarise the same as one that ran in the clear, and
    the loss must stay visible (docs/EVAL.md §7)."""
    clear = [_row(f"p{i}", CONTROL, 0.8) for i in range(4)]
    unlucky = [_row(f"p{i}", VARIANT, 0.8) for i in range(4)] + [_row("p9", VARIANT, None, "infra_failed", wall=3600, cost=0.0)]
    c, v = arm_summary(clear + unlucky, CONTROL), arm_summary(clear + unlucky, VARIANT)
    assert c.mean_score == v.mean_score and c.mean_minutes == v.mean_minutes and c.cost_usd == v.cost_usd
    assert v.n_infra_failed == 1 and v.n == 5 and v.n_scored == 4
    md = render_summary(pair_up(clear + unlucky), clear + unlucky, title="t", variant_env={"X": "1"},
                        generator="g", judge="j", rounds=2)
    assert "n_infra_failed: 1" in md and "--redo-status infra_failed" in md and "variant env: `X=1`" in md
    assert "| p9 |  | missing | infra_failed | — | unpaired: control missing; variant infra_failed |" in md


def test_summary_marks_regressions_and_states_the_rule():
    rows = [_row("a", CONTROL, 0.8), _row("a", VARIANT, 0.7), _row("b", CONTROL, 0.5), _row("b", VARIANT, 0.6)]
    md = render_summary(pair_up(rows), rows, title="t", variant_env={}, generator="g", judge="j", rounds=1)
    assert "| a |  | 0.800 | 0.700 | -0.100 | REGRESSION |" in md and "## Verdict: **inconclusive**" in md
    assert "keep iff mean delta >= +0.02" in md and "(none)" in md


# ----------------------------------------------------------------------------- env isolation
def test_variant_env_is_applied_to_the_variant_arm_only():
    opts = AbOptions(variant_env={"CV3D_PLAN_BRIEF": "on"})
    base = {"PATH": "/bin", "CV3D_PLAN_BRIEF": "on"}  # leaked from the launching shell
    c, v = child_env(CONTROL, opts, base), child_env(VARIANT, opts, base)
    assert v["CV3D_PLAN_BRIEF"] == "on"
    assert "CV3D_PLAN_BRIEF" not in c, "the control must never inherit the switch under test"
    assert c["PATH"] == v["PATH"] == "/bin"
    assert c[MAX_IN_FLIGHT_ENV] == v[MAX_IN_FLIGHT_ENV] == "16"
    # a cap set by the driver's own environment is respected, not overwritten
    assert child_env(VARIANT, opts, {MAX_IN_FLIGHT_ENV: "4"})[MAX_IN_FLIGHT_ENV] == "4"
    assert child_env(VARIANT, opts, {})["PYTHONUNBUFFERED"] == "1"


def test_the_in_flight_env_name_is_one_settings_reads(monkeypatch):
    """A cap that Settings does not read is no cap: the children would run at 64 each."""
    from codeverse.config import get_settings

    monkeypatch.setenv(MAX_IN_FLIGHT_ENV, "7")
    monkeypatch.setenv("CV3D_RATE__MAX_IN_FLIGHT", "64")  # a shell-exported nested value must not win
    get_settings.cache_clear()
    try:
        assert get_settings().rate.max_in_flight == 7
    finally:
        get_settings.cache_clear()


def test_parse_variant_env():
    assert parse_variant_env(["A=1", "B=x=y", "C="]) == {"A": "1", "B": "x=y", "C": ""}
    for bad in (["A"], ["=1"]):
        with pytest.raises(ValueError):
            parse_variant_env(bad)


def test_worker_argv_carries_the_whole_option_block(tmp_path: Path):
    opts = AbOptions(variant_env={"K": "v"}, rounds=1)
    argv = worker_argv(BATTERY, tmp_path, "cmp_easy_stool", VARIANT, opts)
    assert argv[1].endswith("ab_plan.py") and argv[2] == "cell" and "--arm" in argv
    assert AbOptions.model_validate_json(argv[-1]) == opts
    assert cell_dir(tmp_path, CONTROL, "x", "api-agent:gemini:g").parent.parts[-4:] == ("arms", "control", "cells", "x")


def test_a_child_that_dies_without_a_cell_is_an_error_not_a_score(tmp_path: Path):
    """Real subprocess, no network: an unknown prompt id makes the worker exit 2 before any
    model is touched, and the driver must record that as a scoreless error cell."""
    from bench.ab_plan import spawn_cell
    from bench.run_bench import BenchPrompt

    ghost = BenchPrompt(id="no_such_prompt", prompt="x", tier="easy")
    res = spawn_cell(BATTERY, tmp_path, ghost, CONTROL, AbOptions(variant_env={"K": "v"}))
    assert res.status == "error" and res.score is None and res.arm == CONTROL
    assert "worker exited 2" in res.error and "no prompt 'no_such_prompt'" in res.error
    assert (Path(res.workspace) / "worker.log").is_file()


# ----------------------------------------------------------------------------- the driver, with a fake cell runner
class FakeCells:
    """Records (prompt, arm, env-at-call) and answers from a script; the pair order is observable."""

    def __init__(self, scores: dict[tuple[str, str], float | None | str]):
        self.scores = scores
        self.calls: list[tuple[str, str]] = []

    def __call__(self, battery_path, out, item, arm, opts) -> CellResult:
        self.calls.append((item.id, arm))
        env = child_env(arm, opts, {})
        s = self.scores.get((item.id, arm), 0.5)
        if isinstance(s, str):
            return _row(item.id, arm, None, s, tier=item.tier, workspace=env.get("CV3D_PLAN_BRIEF", ""))
        return _row(item.id, arm, s, tier=item.tier, workspace=env.get("CV3D_PLAN_BRIEF", ""))


def test_driver_runs_pairs_in_prompt_order_and_writes_the_verdict(tmp_path: Path):
    b = Battery.load(BATTERY)
    ids = [p.id for p in b.prompts[:3]]
    fake = FakeCells({(ids[0], VARIANT): 0.55, (ids[1], VARIANT): 0.53, (ids[2], VARIANT): 0.52})
    opts = AbOptions(variant_env={"CV3D_PLAN_BRIEF": "on"}, ids=ids)
    v = run_ab(BATTERY, tmp_path, opts, run_cell_fn=fake)
    # both arms of prompt N before anything of prompt N+1
    assert [c[0] for c in fake.calls] == [i for i in ids for _ in range(2)]
    assert {c[1] for c in fake.calls} == {CONTROL, VARIANT}
    assert v.decision == "keep" and v.n_pairs == 3 and v.mean_delta == pytest.approx(0.0333, abs=1e-3)
    rows = load_jsonl(tmp_path / "results.jsonl", CellResult)
    assert len(rows) == 6
    # the env reached only the variant cells
    assert {r.workspace for r in rows if r.arm == VARIANT} == {"on"}
    assert {r.workspace for r in rows if r.arm == CONTROL} == {""}
    pairs = json.loads((tmp_path / "pairs.json").read_text())
    assert pairs["verdict"]["decision"] == "keep" and len(pairs["pairs"]) == 3
    assert (tmp_path / "summary.md").read_text().startswith("# A/B: compare_v1")
    assert json.loads((tmp_path / "ab.json").read_text())["arms"] == {"control": {}, "variant": {"CV3D_PLAN_BRIEF": "on"}}


def test_resume_skips_done_pairs_and_redo_reruns_both_arms(tmp_path: Path):
    b = Battery.load(BATTERY)
    ids = [p.id for p in b.prompts[:2]]
    first = FakeCells({(ids[1], VARIANT): "infra_failed"})
    opts = AbOptions(variant_env={"K": "v"}, ids=ids)
    v = run_ab(BATTERY, tmp_path, opts, run_cell_fn=first)
    assert v.n_pairs == 1, "the outage pair must not count"
    # plain resume: nothing left to do
    again = FakeCells({})
    run_ab(BATTERY, tmp_path, opts, run_cell_fn=again)
    assert again.calls == []
    # redo: the whole pair of the hit prompt, nothing else
    redo = FakeCells({})
    v = run_ab(BATTERY, tmp_path, opts.model_copy(update={"redo_status": ["infra_failed"]}), run_cell_fn=redo)
    assert sorted(redo.calls) == [(ids[1], CONTROL), (ids[1], VARIANT)]
    assert v.n_pairs == 2
    rows = load_jsonl(tmp_path / "results.jsonl", CellResult)
    latest = {(r.prompt_id, r.arm): r for r in rows}
    assert latest[(ids[1], VARIANT)].status == "scored" and len(rows) == 6


def test_an_interrupted_pair_runs_only_its_missing_arm():
    b = Battery.load(BATTERY)
    p = b.prompts[0]
    done = {(p.id, CONTROL): _row(p.id, CONTROL, 0.5)}
    todo = _plan_todo(b, done, AbOptions(ids=[p.id]))
    assert todo == [(p, [VARIANT])]
    assert _plan_todo(b, {(p.id, CONTROL): _row(p.id, CONTROL, 0.5), (p.id, VARIANT): _row(p.id, VARIANT, 0.5)},
                      AbOptions(ids=[p.id])) == []


def test_cli_refuses_identical_arms_and_wrong_parallel(tmp_path: Path, capsys):
    with pytest.raises(SystemExit):
        main(["--prompts", str(BATTERY), "--out", str(tmp_path), "--no-preflight"])
    with pytest.raises(SystemExit):
        main(["--prompts", str(BATTERY), "--out", str(tmp_path), "--variant-env", "K=v", "--parallel", "8"])


def test_cli_report_only_rebuilds_from_results(tmp_path: Path, capsys):
    (tmp_path / "results.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in
                                                    [_row("cmp_easy_stool", CONTROL, 0.5), _row("cmp_easy_stool", VARIANT, 0.4)]))
    assert main(["--prompts", str(BATTERY), "--out", str(tmp_path), "--report-only"]) == 0
    assert "verdict: revert" in capsys.readouterr().out
    assert (tmp_path / "pairs.json").is_file()


def test_ab_plan_children_count_as_harness_processes():
    from codeverse.models.health import _is_harness_argv

    assert _is_harness_argv([sys.executable, str(REPO / "bench" / "ab_plan.py"), "cell", "--arm", "control"])
    assert os.environ is not None
