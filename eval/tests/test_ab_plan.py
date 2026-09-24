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
)
from bench.run_bench import Battery  # noqa: E402

BATTERY = REPO / "bench" / "prompts" / "compare_v1.yaml"


def _row(pid: str, arm: str, score: float | None, status: str = "scored", **kw) -> CellResult:
    return CellResult(prompt_id=pid, arm=arm, kind="harness", score=score, status=status,
                      gen_cost_usd=kw.pop("cost", 1.0), judge_cost_usd=0.1, wall_s=kw.pop("wall", 120.0), **kw)


def _journal(out: Path) -> list[CellResult]:
    return list(latest(read_jsonl(out / "results.jsonl", CellResult)).values())


def _pairs(*deltas: float) -> list[PairOutcome]:
    return [PairOutcome(prompt_id=f"p{i}", paired=True, delta=d) for i, d in enumerate(deltas)]


# ----------------------------------------------------------------------------- verdict rule
@pytest.mark.parametrize("deltas, decision, regressions", [
    ((0.03, 0.02, 0.01, 0.04), "keep", []),
    ((0.10, 0.10, 0.10, -0.03), "inconclusive", ["p3"]),     # one regression blocks a keep
    ((0.01, 0.02, 0.02, 0.01), "inconclusive", []),           # a mean just under the bar
    ((-0.02, -0.02, -0.02), "revert", []),
    ((0.20, 0.20, -0.03, -0.05), "revert", ["p2", "p3"]),     # mean +0.08 but two prompts lost
    ((-0.019, 0.0), "inconclusive", []),
])
def test_the_verdict_rule(deltas, decision, regressions):
    v = verdict_of(_pairs(*deltas))
    assert v.decision == decision and v.regressions == regressions


def test_no_pairs_is_inconclusive_never_keep():
    v = verdict_of([PairOutcome(prompt_id="p0", paired=False, reason="control infra_failed")])
    assert v.decision == "inconclusive" and v.n_pairs == 0 and v.mean_delta is None


def test_outage_cells_are_excluded_from_every_arm_rate():
    """An arm hit by weather summarises like one that ran in the clear; the loss stays visible (EVAL.md §7)."""
    clear = [_row(f"p{i}", CONTROL, 0.8) for i in range(4)]
    unlucky = [_row(f"p{i}", VARIANT, 0.8) for i in range(4)] + [_row("p9", VARIANT, None, "infra_failed", wall=3600, cost=0.0)]
    by = {s.arm: s for s in arm_stats(clear + unlucky)}
    c, v = by[CONTROL], by[VARIANT]
    assert c.mean_score == v.mean_score and c.mean_minutes == v.mean_minutes and c.mean_gen_usd == v.mean_gen_usd
    assert c.build_ok_rate == v.build_ok_rate
    assert v.infra_failed == 1 and v.n == 5 and v.n_scored == 4 and v.n_evaluated == 4
    md = render_summary(pair_up(clear + unlucky), clear + unlucky, title="t", variant_env={"X": "1"},
                        generator="g", judge="j", rounds=2)
    assert "n_infra_failed: 1" in md and "--redo-status infra_failed" in md and "variant env: `X=1`" in md
    assert "| p9 |  | missing | infra_failed | — | unpaired: control missing; variant infra_failed |" in md


# ----------------------------------------------------------------------------- noise floor
def test_a_verdict_carries_its_noise_beside_the_word():
    """An A/A run once said "keep" on +0.344 from one prompt: the verdict must say when it is noise."""
    v = verdict_of(_pairs(0.30, -0.20, 0.10, 0.05, -0.10, 0.25, 0.02, -0.15))
    assert not v.separated and "NOT separated from noise" in v.caution and v.n_for_power > 100
    assert (v.n_up, v.n_down) == (5, 3)
    v = verdict_of(_pairs(0.10, 0.11, 0.09, 0.12))
    assert v.decision == "keep" and v.separated and v.caution == ""
    v = verdict_of(_pairs(0.344))
    assert v.decision == "keep", "the blunt rule still fires — that is exactly the danger"
    assert v.sd_delta is None and not v.separated and "one pair cannot separate" in v.caution


def test_the_summary_marks_regressions_states_the_rule_and_labels_an_aa_run():
    rows = [_row("a", CONTROL, 0.8), _row("a", VARIANT, 0.7), _row("b", CONTROL, 0.5), _row("b", VARIANT, 0.6)]
    md = render_summary(pair_up(rows), rows, title="t", variant_env={}, generator="g", judge="j", rounds=1)
    assert "| a |  | 0.800 | 0.700 | -0.100 | REGRESSION |" in md and "## Verdict: **inconclusive**" in md
    assert "keep iff mean delta >= +0.02" in md and "(none)" in md
    # an A/A run is labelled so nobody reads it as a decision
    md = render_summary(pair_up(rows), rows, title="t", variant_env={}, generator="g", judge="j", rounds=1, aa=True)
    assert md.startswith("# A/A: t") and "A/A calibration — the arms are identical" in md


# ----------------------------------------------------------------------------- env isolation
def test_child_env_isolates_the_variant_switch_and_pins_the_in_flight_cap(monkeypatch):
    """The control never inherits the switch under test; CQ-3: an explicit --max-in-flight beats the
    shell's C3D_MAX_IN_FLIGHT (docs/COST.md §23), and --variant-env may not set the cap."""
    opts = AbOptions(variant_env={"C3D_PLAN_BRIEF": "on"})
    base = {"PATH": "/bin", "C3D_PLAN_BRIEF": "on"}  # leaked from the launching shell
    c, v = child_env(CONTROL, opts, base), child_env(VARIANT, opts, base)
    assert v["C3D_PLAN_BRIEF"] == "on"
    assert "C3D_PLAN_BRIEF" not in c, "the control must never inherit the switch under test"
    assert c["PATH"] == v["PATH"] == "/bin"
    assert c[MAX_IN_FLIGHT_ENV] == v[MAX_IN_FLIGHT_ENV] == "16"
    assert child_env(VARIANT, opts, {})["PYTHONUNBUFFERED"] == "1"

    opts = AbOptions(variant_env={"K": "v"}, max_in_flight=8)
    base = {"PATH": "/bin", MAX_IN_FLIGHT_ENV: "32", NESTED_MAX_IN_FLIGHT_ENV: "48"}
    for arm in (CONTROL, VARIANT):
        env = child_env(arm, opts, base)
        assert env[MAX_IN_FLIGHT_ENV] == "8", "an explicit --max-in-flight must beat the shell"
        assert NESTED_MAX_IN_FLIGHT_ENV not in env, "the nested spelling must not fight the flat one"
    # without the flag the inherited cap is honoured, resolved once into AbOptions
    monkeypatch.setenv(MAX_IN_FLIGHT_ENV, "6")
    monkeypatch.delenv(NESTED_MAX_IN_FLIGHT_ENV, raising=False)
    assert inherited_max_in_flight() == 6
    monkeypatch.delenv(MAX_IN_FLIGHT_ENV)
    assert inherited_max_in_flight() == DEFAULT_MAX_IN_FLIGHT

    assert parse_variant_env(["A=1", "B=x=y", "C="]) == {"A": "1", "B": "x=y", "C": ""}
    for bad in (["A"], ["=1"]):
        with pytest.raises(ValueError):
            parse_variant_env(bad)
    for name in (MAX_IN_FLIGHT_ENV, NESTED_MAX_IN_FLIGHT_ENV):
        with pytest.raises(ValueError, match="not allowed"):
            parse_variant_env([f"{name}=64"])


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
    """CP-1: a 0xff in the log tail used to raise out of spawn_cell and end the whole A/B."""
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


def test_a_relative_battery_path_reaches_the_workers_absolute(tmp_path: Path, monkeypatch):
    """Workers run with cwd=REPO: the relative --prompts the usage line shows must not reach them as-is."""
    seen: list[Path] = []

    def record(battery_path, out, item, arm, opts):
        seen.append(Path(battery_path))
        return _row(item.id, arm, 0.5, tier=item.tier)

    monkeypatch.chdir(Path(BATTERY).parent)
    run_ab(Path(BATTERY).name, tmp_path, AbOptions(variant_env={"K": "v"}, limit=1), run_cell_fn=record)
    assert seen and all(p.is_absolute() and p.is_file() for p in seen)


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
    """A pin that dies in a 503 storm records the pair infra_failed and the loop goes on (EVAL.md §7)."""
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
