"""Matrix runner: cells, resume, pairwise arena, report (offline, fakes)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._compare_report import (  # noqa: E402
    CellResult,
    PairRow,
    arm_stats,
    load_jsonl,
    pair_stats,
)
from bench._fixed_eval import acceptance_from_spec  # noqa: E402
from bench.compare_backends import (  # noqa: E402
    CompareDeps,
    CompareOptions,
    main,
    parse_arm,
    parse_arms,
    run_matrix,
    spec_for,
)
from bench.run_bench import Battery  # noqa: E402
from tests.compare_bench.conftest import (  # noqa: E402
    BAD,
    BATTERY,
    GOOD,
    FakeBackend,
    FakeEvaluator,
    FakePairwise,
    fake_run_track,
)

ARMS = "harness:gemini-cli:gemini-3.6-flash,oneshot:claude-code,oneshot+repair:codex,oneshot:gemini:gemini-3.7-flash"


def test_battery_compare_v1_is_valid():
    b = Battery.load(BATTERY)
    assert b.name == "compare_v1" and b.track.value == "static_object" and b.language.value == "blender"
    assert len(b.prompts) == 8 and len({p.id for p in b.prompts}) == 8
    assert all(len(p.must_have) >= 3 for p in b.prompts)
    assert {p.tier for p in b.prompts} == {"easy", "medium", "hard"}


def test_parse_arms():
    arms = parse_arms(ARMS)
    assert [a.kind for a in arms] == ["harness", "oneshot", "oneshot+repair", "oneshot"]
    assert arms[0].target == "gemini-cli:gemini-3.6-flash" and arms[2].target == "codex"
    assert arms[2].slug == "oneshot_plus_repair_codex"
    for bad in ("harness", "foo:bar", "oneshot:gemini-cli:x", "oneshot:nope"):
        with pytest.raises(ValueError):
            parse_arm(bad)
    with pytest.raises(ValueError):
        parse_arms("oneshot:codex,oneshot:codex")


def test_spec_for_and_acceptance():
    b = Battery.load(BATTERY)
    opts = CompareOptions(judge="gemini:gemini-3.1-pro-preview", loop_judge="gemini:gemini-3.7-flash")
    harness = spec_for(b, b.prompts[0], parse_arm("harness:gemini-cli:gemini-3.7-flash"), opts)
    one = spec_for(b, b.prompts[0], parse_arm("oneshot:codex"), opts)
    assert harness.backends.generator == "gemini-cli:gemini-3.7-flash" and harness.backends.judge == "gemini:gemini-3.7-flash"
    assert harness.budget.max_rounds == 3
    assert one.backends.generator == "single-shot:codex" and one.constraints.must_have == b.prompts[0].must_have
    acc = acceptance_from_spec(one)
    assert [a.id for a in acc] == ["must_1", "must_2", "must_3"] and all(a.priority == "must" for a in acc)


def _deps(ev: FakeEvaluator, backends: dict[str, FakeBackend], run_track) -> CompareDeps:
    pw = {}

    def _pw(model_id: str):
        pw.setdefault(model_id, FakePairwise(model_id))
        return pw[model_id]

    deps = CompareDeps(ev, run_track=run_track, oneshot_backend=lambda t: backends[t], pairwise_judge=_pw)
    deps.pw = pw  # type: ignore[attr-defined]
    return deps


def test_matrix_end_to_end_with_fakes(tmp_path: Path):
    ev = FakeEvaluator()
    backends = {
        "claude-code": FakeBackend(["```python\n" + GOOD.format(score=0.6) + "```"]),
        "codex": FakeBackend([BAD, BAD, GOOD.format(score=0.75)]),          # repair arm: 2 retries fix it
        "gemini:gemini-3.7-flash": FakeBackend(["I refuse."]),              # unparseable → score 0
    }
    run_track = fake_run_track(0.9)
    deps = _deps(ev, backends, run_track)
    out = tmp_path / "cmp"
    opts = CompareOptions(judge="gemini:fixed", limit=2, parallel=2, repair_attempts=2)
    rows = run_matrix(BATTERY, out, parse_arms(ARMS), opts, deps)
    assert len(rows) == 8 and (out / "matrix.json").is_file()
    by = {(r.prompt_id, r.arm): r for r in rows}
    h = by[("cmp_easy_stool", "harness:gemini-cli:gemini-3.6-flash")]
    assert h.status == "scored" and h.score == pytest.approx(0.9) and h.passed and h.build_ok
    assert h.gen_cost_usd == pytest.approx(0.9) and h.harness_status == "passed" and h.harness_loop_score == pytest.approx(0.8)
    assert h.judge_cost_usd == pytest.approx(0.01) and h.tris == 900 and Path(h.sheet).is_file()
    assert (Path(h.workspace) / "run" / "src" / "model.py").is_file() and (Path(h.workspace) / "eval" / "eval.json").is_file()
    assert (Path(h.workspace) / "eval" / "src" / "parts" / "legs.py").is_file()  # whole src/ tree is evaluated
    c = by[("cmp_easy_stool", "oneshot:claude-code")]
    assert c.status == "scored" and c.score == pytest.approx(0.6) and c.passed is False and c.attempts == 1
    assert c.gen_cost_usd == pytest.approx(0.02) and c.kind == "oneshot"
    x = by[("cmp_easy_stool", "oneshot+repair:codex")]
    assert x.status == "scored" and x.score == pytest.approx(0.75) and x.attempts == 3 and x.gen_cost_usd == pytest.approx(0.06)
    g = by[("cmp_easy_stool", "oneshot:gemini:gemini-3.7-flash")]
    assert g.status == "no_code" and g.score == 0.0 and g.passed is False and not g.build_ok and "unparseable" in g.error
    # the repair prompts carried the error report
    repairs = [c for c in backends["codex"].calls if "BUILD FAILED" in c]
    assert len(repairs) == 4 and all("BOOM" in c and "PREVIOUS `src/model.py`" in c for c in repairs)
    # the fixed evaluator ran once per cell that had code (6 of 8)
    assert len(ev.evaluated) == 6
    # pairwise: 1 harness arm × 3 one-shot arms × 2 prompts = 6 rows; the no-code arm is auto-decided
    pairs = load_jsonl(out / "pairwise.jsonl", PairRow)
    assert len(pairs) == 6
    auto = [p for p in pairs if p.arm_b == "oneshot:gemini:gemini-3.7-flash"]
    assert all(p.winner == "a" and not p.judged for p in auto)
    judged = [p for p in pairs if p.judged]
    assert len(judged) == 4 and all(p.winner == "a" and p.cost_usd == pytest.approx(0.03) for p in judged)
    assert deps.pw["gemini:fixed"].calls == 4
    # report
    md = (out / "report.md").read_text()
    assert "fixed judge: **gemini:fixed**" in md
    # the arm row is n | dropped | over budget | mean: the two loss columns stay blank when
    # nothing was lost, but must always be PRESENT so a drop can never hide (docs/EVAL.md §7)
    assert "| arm | kind | n | dropped | over budget | mean |" in md, md
    assert "| harness:gemini-cli:gemini-3.6-flash | harness | 2 |  |  | 0.900 |" in md, md
    assert "| cmp_easy_stool | easy | 0.90✓ | 0.60 | 0.75✓ | 0.00 ✗build |" in md
    assert "| harness:gemini-cli:gemini-3.6-flash | oneshot:claude-code | 2 | 2 | 0 | 0 | 100% |" in md
    page = (out / "report.html").read_text()
    assert "report_assets/cmp_easy_stool__harness_gemini-cli_gemini-3.6-flash.png" in page
    assert len(list((out / "report_assets").glob("*.png"))) == 6

    # ---- resume: nothing re-runs, nothing re-judged, results identical
    run_track2 = fake_run_track(0.1)
    deps2 = _deps(FakeEvaluator(), {k: FakeBackend(["x"]) for k in backends}, run_track2)
    rows2 = run_matrix(BATTERY, out, parse_arms(ARMS), opts, deps2)
    assert len(rows2) == 8 and not run_track2.calls and deps2.evaluator.evaluated == [] and not deps2.pw
    assert {(r.prompt_id, r.arm): r.score for r in rows2} == {(r.prompt_id, r.arm): r.score for r in rows}
    # ---- adding a prompt (limit 3) only runs the new cells
    rows3 = run_matrix(BATTERY, out, parse_arms(ARMS), opts.model_copy(update={"limit": 3}), deps2)
    assert len(rows3) == 12 and run_track2.calls == ["compare_v1/cmp_med_dining_chair"]
    assert len(load_jsonl(out / "pairwise.jsonl", PairRow)) == 9


def test_cell_errors_are_recorded_not_raised(tmp_path: Path):
    class Exploding:
        def build(self, ws):
            raise RuntimeError("blender missing")

        def evaluate(self, ws, spec):
            raise RuntimeError("blender missing")

    def failing_track(spec, ws, resume):
        raise RuntimeError("planner down")

    deps = CompareDeps(Exploding(), run_track=failing_track, oneshot_backend=lambda t: FakeBackend([GOOD.format(score=0.5)]),
                       pairwise_judge=FakePairwise)
    rows = run_matrix(BATTERY, tmp_path / "o", parse_arms("harness:gemini-cli:gemini-3.6-flash,oneshot:codex"),
                      CompareOptions(limit=1, parallel=1), deps)
    by = {r.arm: r for r in rows}
    assert by["harness:gemini-cli:gemini-3.6-flash"].status == "error" and "planner down" in by["harness:gemini-cli:gemini-3.6-flash"].error
    assert by["oneshot:codex"].status == "error" and "blender missing" in by["oneshot:codex"].error
    assert (tmp_path / "o" / "report.md").is_file()


def test_stats_math():
    rows = [CellResult(prompt_id="p1", arm="a", kind="harness", score=0.8, passed=True, build_ok=True, gen_cost_usd=1.0, wall_s=60),
            CellResult(prompt_id="p2", arm="a", kind="harness", score=0.0, passed=False, build_ok=False, gen_cost_usd=0.5, wall_s=120),
            CellResult(prompt_id="p1", arm="b", kind="oneshot", score=None, status="judge_error", build_ok=True)]
    s = {x.arm: x for x in arm_stats(rows)}
    assert s["a"].mean_score == pytest.approx(0.4) and s["a"].pass_rate == 0.5 and s["a"].build_ok_rate == 0.5
    assert s["a"].mean_gen_usd == 0.75 and s["a"].mean_minutes == 1.5
    assert s["b"].mean_score is None and s["b"].errors == 1
    ps = pair_stats([PairRow(prompt_id="p1", arm_a="a", arm_b="b", winner="a", confidence=0.9),
                     PairRow(prompt_id="p2", arm_a="a", arm_b="b", winner="tie", confidence=0.3),
                     PairRow(prompt_id="p3", arm_a="a", arm_b="b", winner="b", confidence=0.6)])[0]
    assert (ps.wins_a, ps.wins_b, ps.ties, ps.win_rate_a) == (1, 1, 1, 0.5) and ps.mean_confidence == pytest.approx(0.6)


def test_cli_report_only(tmp_path: Path):
    out = tmp_path / "o"
    out.mkdir()
    (out / "results.jsonl").write_text(CellResult(prompt_id="p", arm="oneshot:codex", kind="oneshot", score=0.3,
                                                  build_ok=True, status="scored").model_dump_json() + "\n")
    assert main(["--prompts", str(BATTERY), "--arms", "oneshot:codex", "--out", str(out), "--report-only"]) == 0
    assert "oneshot:codex" in (out / "report.md").read_text()
    assert json.loads((out / "results.jsonl").read_text().splitlines()[0])["score"] == 0.3


def test_redo_status_reuses_recorded_answers(tmp_path: Path):
    """A redo of failed cells re-evaluates without a second generation call; a failed generation is not cached."""
    ev = FakeEvaluator()
    be = FakeBackend([BAD])
    deps = CompareDeps(ev, run_track=fake_run_track(), oneshot_backend=lambda t: be, pairwise_judge=FakePairwise)
    out = tmp_path / "o"
    opts = CompareOptions(limit=1, parallel=1, pairwise=False)
    rows = run_matrix(BATTERY, out, parse_arms("oneshot:codex"), opts, deps)
    assert rows[0].status == "build_failed" and len(be.calls) == 1
    assert (out / "cells" / "cmp_easy_stool" / "oneshot_codex" / "gen" / "attempt0" / "result.json").is_file()
    rows2 = run_matrix(BATTERY, out, parse_arms("oneshot:codex"), opts.model_copy(update={"redo_status": ["build_failed"]}), deps)
    assert rows2[0].status == "build_failed" and len(be.calls) == 1 and len(ev.evaluated) == 2  # re-evaluated, not re-generated
    empty = FakeBackend([""])
    deps3 = CompareDeps(ev, run_track=fake_run_track(), oneshot_backend=lambda t: empty, pairwise_judge=FakePairwise)
    rows3 = run_matrix(BATTERY, out, parse_arms("oneshot:claude-code"), opts, deps3)
    r3 = next(r for r in rows3 if r.arm == "oneshot:claude-code")
    assert r3.status == "no_code" and "empty" in r3.error
    assert not (out / "cells" / "cmp_easy_stool" / "oneshot_claude-code" / "gen" / "attempt0" / "result.json").exists()


def test_every_cell_and_its_harness_run_open_a_ledger(tmp_path: Path):
    """compare_backends is a run-producing path: its cells must be priced into the
    tree, not into the per-process fallback log (docs/COST.md §12).  The harness arm
    gets a nested ledger for its own run; the cell's own ledger holds the spend that
    sits OUTSIDE that run (the fixed evaluator's judge) — the §6 gap."""
    from codeverse.contracts.chat import ChatMessage, ChatRequest, ChatResponse
    from codeverse.contracts.common import Usage
    from codeverse.cost.instrument import MeteredChatModel
    from codeverse.cost.ledger import load_ledger

    class FakeChat:
        provider, model, id = "gemini", "gemini-3.7-flash", "gemini:gemini-3.7-flash"

        def supports_vision(self) -> bool:
            return True

        def generate(self, request: ChatRequest) -> ChatResponse:
            return ChatResponse(text="ok", usage=Usage(backend="gemini", model="gemini-3.7-flash",
                                                       input_tokens=1000, output_tokens=10))

    def bill(label: str) -> None:
        MeteredChatModel(FakeChat()).generate(ChatRequest(messages=[ChatMessage.user("x")], label=label))

    ev = FakeEvaluator()
    inner_eval = ev.evaluate

    def evaluate(ws, spec):          # the fixed evaluator buys a verdict
        bill("judge:static_object_v1:r00:s0")
        return inner_eval(ws, spec)

    ev.evaluate = evaluate           # type: ignore[method-assign]
    inner_track = fake_run_track(0.9)

    def run_track(spec, ws, resume):  # the harness run spends inside its own ledger
        bill("api-agent:baseline:t0")
        return inner_track(spec, ws, resume)

    deps = _deps(ev, {"claude-code": FakeBackend(["```python\n" + GOOD.format(score=0.6) + "```"])}, run_track)
    out = tmp_path / "cmp"
    opts = CompareOptions(judge="gemini:fixed", limit=1, parallel=2, pairwise=False)
    rows = run_matrix(BATTERY, out, parse_arms("harness:gemini-cli:gemini-3.6-flash,oneshot:claude-code"),
                      opts, deps)
    assert len(rows) == 2
    cells = {r.arm: Path(r.workspace) for r in rows}
    harness = cells["harness:gemini-cli:gemini-3.6-flash"]
    assert [r.label for r in load_ledger(harness / "run")] == ["api-agent:baseline:t0"]
    assert [r.label for r in load_ledger(harness)] == ["judge:static_object_v1:r00:s0"]
    oneshot = cells["oneshot:claude-code"]
    assert not (oneshot / "run").exists()
    assert [r.label for r in load_ledger(oneshot)] == ["judge:static_object_v1:r00:s0"]
