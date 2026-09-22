"""bench/ab_plan.py — pairing, verdict rule, outage exclusion, env isolation (offline)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._ab_report import (  # noqa: E402
    CONTROL,
    KEEP_DELTA,
    VARIANT,
    PairOutcome,
    pair_up,
    render_summary,
    verdict_of,
)
from bench._compare_report import CellResult, arm_stats  # noqa: E402
from bench._jsonl import latest, read_jsonl  # noqa: E402
from bench.ab_plan import (  # noqa: E402
    DEFAULT_MAX_IN_FLIGHT,
    MAX_IN_FLIGHT_ENV,
    NESTED_MAX_IN_FLIGHT_ENV,
    AbOptions,
    Todo,
    _plan_todo,
    _stored_options,
    archive_cell,
    cell_dir,
    child_env,
    inherited_max_in_flight,
    main,
    parse_variant_env,
    run_ab,
    worker_argv,
)
from bench.run_bench import Battery  # noqa: E402
from bench.stats import n_to_resolve, t975  # noqa: E402

BATTERY = REPO / "bench" / "prompts" / "compare_v1.yaml"


def _row(pid: str, arm: str, score: float | None, status: str = "scored", **kw) -> CellResult:
    return CellResult(prompt_id=pid, arm=arm, kind="harness", score=score, status=status,
                      gen_cost_usd=kw.pop("cost", 1.0), judge_cost_usd=0.1, wall_s=kw.pop("wall", 120.0), **kw)


def _journal(out: Path) -> list[CellResult]:
    return list(latest(read_jsonl(out / "results.jsonl", CellResult)).values())


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
    by = {s.arm: s for s in arm_stats(clear + unlucky)}
    c, v = by[CONTROL], by[VARIANT]
    assert c.mean_score == v.mean_score and c.mean_minutes == v.mean_minutes and c.mean_gen_usd == v.mean_gen_usd
    assert v.infra_failed == 1 and v.n == 5 and v.n_scored == 4
    md = render_summary(pair_up(clear + unlucky), clear + unlucky, title="t", variant_env={"X": "1"},
                        generator="g", judge="j", rounds=2)
    assert "n_infra_failed: 1" in md and "--redo-status infra_failed" in md and "variant env: `X=1`" in md
    assert "| p9 |  | missing | infra_failed | — | unpaired: control missing; variant infra_failed |" in md


def test_summary_marks_regressions_and_states_the_rule():
    rows = [_row("a", CONTROL, 0.8), _row("a", VARIANT, 0.7), _row("b", CONTROL, 0.5), _row("b", VARIANT, 0.6)]
    md = render_summary(pair_up(rows), rows, title="t", variant_env={}, generator="g", judge="j", rounds=1)
    assert "| a |  | 0.800 | 0.700 | -0.100 | REGRESSION |" in md and "## Verdict: **inconclusive**" in md
    assert "keep iff mean delta >= +0.02" in md and "(none)" in md


# ----------------------------------------------------------------------------- noise floor
def test_a_verdict_states_the_spread_it_was_decided_on():
    """The rule fires on a mean, and a mean of stochastic generations has a spread.  An
    A/A run of this rig (identical arms) returned +0.344 on one prompt and the rule said
    "keep" — so the report has to carry the noise beside the word."""
    v = verdict_of(_pairs(0.30, -0.20, 0.10, 0.05, -0.10, 0.25, 0.02, -0.15))
    assert v.sd_delta is not None and v.se_delta == pytest.approx(v.sd_delta / 8 ** 0.5, abs=1e-3)
    assert v.ci_half == pytest.approx(t975(7) * v.se_delta, abs=1e-3), "the 95 % t-interval, n - 1 = 7 df"
    assert not v.separated and "NOT separated from noise" in v.caution
    # the honest answer to "would one more prompt settle this?": at this spread, hundreds
    assert v.n_for_power == pytest.approx(n_to_resolve(v.sd_delta, KEEP_DELTA), rel=0.01)
    assert v.n_for_power > 100


def test_sign_consistency_catches_the_win_the_mean_rule_throws_away():
    """A change that helps every prompt a little is invisible to a +-0.02 mean at this
    spread, and obvious to the sign test — which is the whole point of reporting it."""
    v = verdict_of(_pairs(*([0.01] * 8)))
    assert v.decision == "inconclusive", "the mean rule cannot see it"
    assert (v.n_up, v.n_down) == (8, 0) and v.sign_p == pytest.approx(2 / 2 ** 8, abs=1e-4)
    v = verdict_of(_pairs(0.30, -0.20, 0.10, 0.05, -0.10, 0.25, 0.02, -0.15))
    assert (v.n_up, v.n_down) == (5, 3) and v.sign_p > 0.7, "a big mean with a coin-flip sign pattern"
    assert verdict_of(_pairs(0.0, 0.0)).sign_p is None, "zero deltas are dropped, as the test requires"


def test_a_tight_win_is_marked_separated_and_carries_no_caution():
    v = verdict_of(_pairs(0.10, 0.11, 0.09, 0.12))
    assert v.decision == "keep" and v.separated and v.caution == ""


def test_one_pair_can_never_be_separated_from_noise():
    v = verdict_of(_pairs(0.344))
    assert v.decision == "keep", "the blunt rule still fires — that is exactly the danger"
    assert v.sd_delta is None and not v.separated and "one pair cannot separate" in v.caution


def test_summary_prints_the_confidence_block():
    rows = [_row(f"p{i}", arm, s) for i, (c, x) in enumerate([(0.5, 0.9), (0.6, 0.3), (0.7, 0.72)])
            for arm, s in ((CONTROL, c), (VARIANT, x))]
    md = render_summary(pair_up(rows), rows, title="t", variant_env={"K": "v"}, generator="g", judge="j", rounds=1)
    assert "## Confidence" in md and "separated from noise: NO" in md and "NOT separated from noise" in md
    assert "sign consistency: 2 up / 1 down" in md and "sign test p = 1.000" in md


def test_an_aa_run_is_labelled_so_nobody_reads_it_as_a_decision():
    rows = [_row("a", CONTROL, 0.59), _row("a", VARIANT, 0.93)]
    md = render_summary(pair_up(rows), rows, title="t", variant_env={}, generator="g", judge="j", rounds=1, aa=True)
    assert md.startswith("# A/A: t") and "A/A calibration — the arms are identical" in md


# ----------------------------------------------------------------------------- env isolation
def test_variant_env_is_applied_to_the_variant_arm_only():
    opts = AbOptions(variant_env={"C3D_PLAN_BRIEF": "on"})
    base = {"PATH": "/bin", "C3D_PLAN_BRIEF": "on"}  # leaked from the launching shell
    c, v = child_env(CONTROL, opts, base), child_env(VARIANT, opts, base)
    assert v["C3D_PLAN_BRIEF"] == "on"
    assert "C3D_PLAN_BRIEF" not in c, "the control must never inherit the switch under test"
    assert c["PATH"] == v["PATH"] == "/bin"
    assert c[MAX_IN_FLIGHT_ENV] == v[MAX_IN_FLIGHT_ENV] == "16"
    assert child_env(VARIANT, opts, {})["PYTHONUNBUFFERED"] == "1"


def test_children_run_at_exactly_the_cap_the_budget_reserved(monkeypatch):
    """CQ-3: both children run at the cap the driver was given.  child_env used
    ``setdefault``, so a shell exporting C3D_MAX_IN_FLIGHT=32 under ``--max-in-flight 8``
    ran a child at 32 (docs/COST.md §23) — three lines after the function deliberately pops
    every variant key so an inherited switch cannot win."""
    opts = AbOptions(variant_env={"K": "v"}, max_in_flight=8)
    base = {"PATH": "/bin", MAX_IN_FLIGHT_ENV: "32", NESTED_MAX_IN_FLIGHT_ENV: "48"}
    for arm in (CONTROL, VARIANT):
        env = child_env(arm, opts, base)
        assert env[MAX_IN_FLIGHT_ENV] == "8", "an explicit --max-in-flight must beat the shell"
        assert NESTED_MAX_IN_FLIGHT_ENV not in env, "the nested spelling must not fight the flat one"
    # without the flag the inherited cap is still honoured — resolved ONCE, into AbOptions,
    # so the number the children get and the number the preflight reserves are the same one
    monkeypatch.setenv(MAX_IN_FLIGHT_ENV, "6")
    monkeypatch.delenv(NESTED_MAX_IN_FLIGHT_ENV, raising=False)
    assert inherited_max_in_flight() == 6
    monkeypatch.delenv(MAX_IN_FLIGHT_ENV)
    assert inherited_max_in_flight() == DEFAULT_MAX_IN_FLIGHT


def test_parse_variant_env():
    assert parse_variant_env(["A=1", "B=x=y", "C="]) == {"A": "1", "B": "x=y", "C": ""}
    for bad in (["A"], ["=1"]):
        with pytest.raises(ValueError):
            parse_variant_env(bad)
    for name in (MAX_IN_FLIGHT_ENV, NESTED_MAX_IN_FLIGHT_ENV):
        with pytest.raises(ValueError, match="not allowed"):
            parse_variant_env([f"{name}=64"])


def test_worker_argv_carries_the_whole_option_block(tmp_path: Path):
    opts = AbOptions(variant_env={"K": "v"}, rounds=1)
    argv = worker_argv(BATTERY, tmp_path, "cmp_easy_stool", VARIANT, opts)
    assert argv[1].endswith("ab_plan.py") and argv[2] == "cell" and "--arm" in argv
    assert AbOptions.model_validate_json(argv[-1]) == opts
    assert cell_dir(tmp_path, CONTROL, "x", "gemini-cli:gemini-3.6-flash").parent.parts[-4:] == ("arms", "control", "cells", "x")


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


def test_a_non_utf8_byte_in_the_worker_log_does_not_kill_the_driver(tmp_path: Path, monkeypatch):
    """CP-1: on the no-cell.json path the log tail is the only evidence there is, and an
    agent CLI is free to print any byte.  One 0xff used to raise UnicodeDecodeError out of
    spawn_cell — which run_ab re-raises through fut.result(), ending the whole A/B without
    a summary — for a log that says '503 UNAVAILABLE' and classifies perfectly well."""
    import bench.ab_plan as ab
    from bench.run_bench import BenchPrompt

    monkeypatch.setattr(ab, "worker_argv",
                        lambda *a, **k: ["bash", "-c", r"printf 'starting cell\n503 Service Unavailable \xff\xfe\n'; exit 1"])
    item = BenchPrompt(id="p_bad_bytes", prompt="x", tier="easy")
    res = ab.spawn_cell(BATTERY, tmp_path, item, CONTROL, AbOptions(variant_env={"K": "v"}))
    assert res.status == "infra_failed" and res.score is None
    assert "503 Service Unavailable" in res.error


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
            return _row(item.id, arm, None, s, tier=item.tier, workspace=env.get("C3D_PLAN_BRIEF", ""))
        return _row(item.id, arm, s, tier=item.tier, workspace=env.get("C3D_PLAN_BRIEF", ""))


def test_driver_runs_pairs_in_prompt_order_and_writes_the_verdict(tmp_path: Path):
    b = Battery.load(BATTERY)
    ids = [p.id for p in b.prompts[:3]]
    fake = FakeCells({(ids[0], VARIANT): 0.55, (ids[1], VARIANT): 0.53, (ids[2], VARIANT): 0.52})
    opts = AbOptions(variant_env={"C3D_PLAN_BRIEF": "on"}, ids=ids)
    v = run_ab(BATTERY, tmp_path, opts, run_cell_fn=fake)
    # both arms of prompt N before anything of prompt N+1
    assert [c[0] for c in fake.calls] == [i for i in ids for _ in range(2)]
    assert {c[1] for c in fake.calls} == {CONTROL, VARIANT}
    assert v.decision == "keep" and v.n_pairs == 3 and v.mean_delta == pytest.approx(0.0333, abs=1e-3)
    rows = _journal(tmp_path)
    assert len(rows) == 6
    # the env reached only the variant cells
    assert {r.workspace for r in rows if r.arm == VARIANT} == {"on"}
    assert {r.workspace for r in rows if r.arm == CONTROL} == {""}
    pairs = json.loads((tmp_path / "pairs.json").read_text())
    assert pairs["verdict"]["decision"] == "keep" and len(pairs["pairs"]) == 3
    assert (tmp_path / "summary.md").read_text().startswith("# A/B: compare_v1")
    assert json.loads((tmp_path / "ab.json").read_text())["arms"] == {"control": {}, "variant": {"C3D_PLAN_BRIEF": "on"}}


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
    assert len((tmp_path / "results.jsonl").read_text().strip().splitlines()) == 6, "the file is append-only"
    cells = latest(read_jsonl(tmp_path / "results.jsonl", CellResult))
    rows = list(cells.values())
    assert cells[(ids[1], VARIANT)].status == "scored"
    assert len(rows) == 4, "readers see one row per (prompt, arm): the redo replaces the stale attempt"


def test_todo_planning_handles_interrupts_and_redos():
    b = Battery.load(BATTERY)
    p = b.prompts[0]
    done = {(p.id, CONTROL): _row(p.id, CONTROL, 0.5)}
    todo = _plan_todo(b, done, AbOptions(ids=[p.id]))
    assert todo == [Todo(p, [VARIANT], fresh=False)], "a finished partner is not regenerated"
    assert _plan_todo(b, {(p.id, CONTROL): _row(p.id, CONTROL, 0.5), (p.id, VARIANT): _row(p.id, VARIANT, 0.5)},
                      AbOptions(ids=[p.id])) == []
    interrupted = {**done, (p.id, VARIANT): _row(p.id, VARIANT, None, "infra_failed")}
    opts = AbOptions(ids=[p.id], redo_status=["infra_failed"])
    assert _plan_todo(b, interrupted, opts) == [Todo(p, [CONTROL, VARIANT], fresh=True)]
    # --redo-resume opts back into the cheap behaviour, explicitly
    assert _plan_todo(b, interrupted, opts.model_copy(update={"redo_fresh": False})) == [Todo(p, [CONTROL, VARIANT], False)]


def test_archive_cell_moves_the_old_attempt_aside_and_keeps_it(tmp_path: Path):
    opts = AbOptions(variant_env={"K": "v"})
    cell = cell_dir(tmp_path, CONTROL, "p1", opts.generator)
    (cell / "run").mkdir(parents=True)
    (cell / "run" / "record.json").write_text("{}")
    first = archive_cell(tmp_path, CONTROL, "p1", opts)
    assert first is not None and first.name.endswith(".attempt1") and (first / "run" / "record.json").is_file()
    assert not cell.exists(), "the redo must start from an empty cell or the harness resumes"
    assert archive_cell(tmp_path, CONTROL, "p1", opts) is None  # nothing left to archive
    cell.mkdir(parents=True)
    assert archive_cell(tmp_path, CONTROL, "p1", opts).name.endswith(".attempt2")  # type: ignore[union-attr]


def test_cli_refuses_identical_arms_and_wrong_parallel(tmp_path: Path, capsys):
    with pytest.raises(SystemExit):
        main(["--prompts", str(BATTERY), "--out", str(tmp_path), "--no-preflight"])
    with pytest.raises(SystemExit):
        main(["--prompts", str(BATTERY), "--out", str(tmp_path), "--variant-env", "K=v", "--parallel", "8"])


def test_aa_run_records_two_empty_arms(tmp_path: Path):
    """--aa is the ONLY sanctioned way to run identical arms, and the record must say so
    or a later reader will mistake a noise measurement for a result."""
    b = Battery.load(BATTERY)
    ids = [p.id for p in b.prompts[:2]]
    fake = FakeCells({(ids[0], VARIANT): 0.9})
    run_ab(BATTERY, tmp_path, AbOptions(variant_env={"C3D_PLAN_FEATURES": "all"}, aa=True, ids=ids), run_cell_fn=fake)
    meta = json.loads((tmp_path / "ab.json").read_text())
    assert meta["aa"] is True and meta["arms"] == {"control": {}, "variant": {}}
    assert {r.workspace for r in _journal(tmp_path)} == {""}, "no arm saw the switch"
    assert (tmp_path / "summary.md").read_text().startswith("# A/A: ")


def test_cli_report_only_rebuilds_from_results(tmp_path: Path, capsys):
    (tmp_path / "results.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in
                                                    [_row("cmp_easy_stool", CONTROL, 0.5),
                                                     _row("cmp_easy_stool", VARIANT, None, "infra_failed"),
                                                     _row("cmp_easy_stool", VARIANT, 0.4)]))
    opts = AbOptions(variant_env={"C3D_SKILLS": "0"}, rounds=1)   # skills are on by default: the variant turns them off
    (tmp_path / "ab.json").write_text(json.dumps({"options": json.loads(opts.model_dump_json())}))
    assert main(["--prompts", str(BATTERY), "--out", str(tmp_path), "--report-only"]) == 0
    assert "verdict: revert" in capsys.readouterr().out
    assert (tmp_path / "pairs.json").is_file()
    summary = (tmp_path / "summary.md").read_text()
    assert "variant env: `C3D_SKILLS=0`" in summary and "n_infra_failed: 0" in summary


def test_report_only_survives_a_run_dir_with_no_or_broken_ab_json(tmp_path):
    assert _stored_options(tmp_path) is None
    (tmp_path / "ab.json").write_text("{not json")
    assert _stored_options(tmp_path) is None
    (tmp_path / "ab.json").write_text('{"no options key": 1}')
    assert _stored_options(tmp_path) is None


def test_a_pinned_plan_that_dies_in_a_storm_records_the_pair_and_continues(tmp_path: Path, monkeypatch):
    """The pinned plan is ONE model call with a 15-minute retry budget. When a 503 storm
    outlasts it the call raises — and that used to propagate out of run_ab's loop and end
    the whole battery. Measured 2026-08-26: a 3-prompt driver died on its first pin, two
    prompts never attempted, while the drivers beside it waited the storm out. An outage is
    not a score (docs/EVAL.md §7): the pair is recorded infra_failed so --redo-status
    infra_failed picks it up, and the loop goes on."""
    from bench import ab_plan as ab
    from codeverse3d.models.base import ModelError

    b = Battery.load(BATTERY)
    ids = [p.id for p in b.prompts[:2]]
    fake = FakeCells({(ids[1], VARIANT): 0.6})
    dead = {ids[0]}

    def stormy_pin(battery, item, out, arms, opts):
        if item.id in dead:
            raise ModelError("Gemini API error 503: This model is currently experiencing high demand.")
        return "h1"

    monkeypatch.setattr(ab, "pin_pair", stormy_pin)
    opts = AbOptions(variant_env={"C3D_SKILLS": "0"}, pin_plan=True, ids=ids)
    run_ab(BATTERY, tmp_path, opts, run_cell_fn=fake)

    rows = _journal(tmp_path)
    first = [r for r in rows if r.prompt_id == ids[0]]
    assert {r.arm for r in first} == {CONTROL, VARIANT} and all(r.status == "infra_failed" for r in first)
    assert all("pinned plan" in r.error for r in first)
    assert [c[0] for c in fake.calls] == [ids[1], ids[1]], "no cell was spent on the dead pair; the next prompt ran"


def test_max_usd_flag_was_deleted(capsys):
    """The money ceiling left the harness on 2026-08-28: the flag must be rejected,
    not silently parsed into nothing."""
    with pytest.raises(SystemExit):
        main(["--prompts", "p.yaml", "--out", "o", "--max-usd=2.5"])
    assert "--max-usd" in capsys.readouterr().err
