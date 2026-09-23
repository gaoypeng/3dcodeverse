"""compare_backends.flag_degraded: a harness run that waited on the provider is flagged, not trusted."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._compare_report import CellResult  # noqa: E402
from bench.compare_backends import (  # noqa: E402
    CompareDeps,
    CompareOptions,
    flag_degraded,
    parse_arm,
    run_cell,
)
from bench.run_bench import Battery  # noqa: E402
from codeverse3d.contracts.common import Usage  # noqa: E402
from codeverse3d.contracts.run import RoundRecord, RunRecord, RunStatus  # noqa: E402
from tests.conftest import BATTERY, GOOD, FakeEvaluator  # noqa: E402


def _track(status: RunStatus, stop_reason: str, rounds: int, cost: float, aborted: int = 0):
    def run(spec, ws, resume):
        ws.src.mkdir(parents=True, exist_ok=True)
        (ws.root / "src" / "model.py").write_text(GOOD.format(score=0.6))
        rec = RunRecord(spec=spec, workspace=str(ws.root), status=status,
                        rounds=[RoundRecord(index=i, kind="baseline" if i == 0 else "refine") for i in range(rounds)],
                        total_usage=Usage(cost_usd=cost),
                        extra={"stop_reason": stop_reason, "aborted_rounds": [{"index": rounds}] * aborted})
        ws.write_json(ws.record_path, rec)
        return rec
    return run


@pytest.mark.parametrize("status,stop,rounds,cost,aborted,expect", [
    (RunStatus.BUDGET, "budget", 1, 1.2, 1, True),    # the storm case: clock stop after 1 round
    (RunStatus.BUDGET, "budget", 2, 1.5, 1, False),   # iterated twice: cut, but not degraded
    (RunStatus.MAX_ROUNDS, "max_rounds", 1, 0.8, 0, False),  # ran every round it was given: fine
])
def test_flag_degraded_rule(tmp_path, status, stop, rounds, cost, aborted, expect):
    battery = Battery.load(BATTERY)
    opts = CompareOptions(judge="gemini:x", loop_judge="gemini:x", degraded_min_wall_s=0)
    deps = CompareDeps(FakeEvaluator(), run_track=_track(status, stop, rounds, cost, aborted))
    r = run_cell(battery, battery.prompts[0], parse_arm("harness:gemini-cli:gemini-3.6-flash"), tmp_path, opts, deps)
    assert r.status == "scored" and r.score is not None
    assert (r.harness_stop_reason, r.harness_rounds, r.harness_aborted_rounds) == (stop, rounds, aborted)
    assert r.degraded is expect, r
    if expect:
        assert "ceiling stop" in r.degraded_reason and f"having spent ${cost:.2f}" in r.degraded_reason


def test_degraded_needs_the_wall_clock_floor():
    """A 2-minute harness run that stopped on `budget` with one round is not a storm victim."""
    r = CellResult(prompt_id="p", arm="harness:x", kind="harness", status="scored", score=0.5, harness_rounds=1,
                   harness_stop_reason="budget", gen_cost_usd=0.5, wall_s=120)
    flag_degraded(r, CompareOptions(judge="g:x"))
    assert not r.degraded
    r.wall_s = 45 * 60
    flag_degraded(r, CompareOptions(judge="g:x"))
    assert r.degraded
