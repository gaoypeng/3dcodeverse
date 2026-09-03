"""``bench/session_stats.py``: the tool-error / cache / cost view a battery is read through.

The numbers docs/COST.md §30 reports came from this view computed by hand, and the
after-side counts were inflated by the ``run/telemetry/trajectories`` symlink — the very
trap that section warns about.  These tests pin the parsing and the dedupe."""

from __future__ import annotations

import json
from pathlib import Path

from bench.session_stats import _sessions, report, round_costs, token_rates, tool_rates


def _session(run: Path, label: str, *, tools: dict[str, tuple[int, int]], prompt: int,
             cached: int, requests: int) -> None:
    d = run / "trajectories" / label
    d.mkdir(parents=True, exist_ok=True)
    (d / "stdout.json").write_text(json.dumps({"stats": {
        "models": {"gemini-3.7-flash": {"tokens": {"prompt": prompt, "cached": cached},
                                        "api": {"totalRequests": requests}}},
        "tools": {"byName": {f"mcp_3dcv_{name}": {"count": c, "fail": f}
                             for name, (c, f) in tools.items()} | {"read_file": {"count": 9, "fail": 9}}},
    }}))


def _run(root: Path, name: str, costs: list[float]) -> Path:
    run = root / name / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "record.json").write_text(json.dumps(
        {"rounds": [{"index": i, "usage": {"cost_usd": c}} for i, c in enumerate(costs)]}))
    return run


def test_the_telemetry_symlink_is_counted_once(tmp_path: Path) -> None:
    run = _run(tmp_path, "cell_a", [1.0, 2.0])
    _session(run, "baseline_r00", tools={"build": (10, 1)}, prompt=1000, cached=700, requests=5)
    (run / "telemetry").mkdir()
    (run / "telemetry" / "trajectories").symlink_to(run / "trajectories", target_is_directory=True)

    assert len(_sessions(tmp_path)) == 1                     # not 2, though both paths exist
    calls, failed = tool_rates(_sessions(tmp_path))
    assert calls["build"] == 10 and failed["build"] == 1     # doubling here is what corrupted §30
    assert token_rates(_sessions(tmp_path))["requests"] == 5


def test_rates_and_costs_over_two_sessions(tmp_path: Path) -> None:
    run = _run(tmp_path, "cell_a", [1.0, 3.0, 5.0])
    _session(run, "baseline_r00", tools={"build": (10, 0), "joint_sweep": (4, 2)},
             prompt=1000, cached=700, requests=5)
    _session(run, "refine_r01", tools={"build": (6, 1)}, prompt=1000, cached=900, requests=5)

    calls, failed = tool_rates(_sessions(tmp_path))
    assert calls == {"build": 16, "joint_sweep": 4} and failed == {"build": 1, "joint_sweep": 2}
    tok = token_rates(_sessions(tmp_path))
    assert tok["cache_hit"] == 0.8 and tok["uncached_per_request"] == 40  # (2000-1600)/10
    assert round_costs(tmp_path) == [1.0, 3.0, 5.0]
    assert "| 3.000 |" in report([tmp_path])                 # the median round, not the mean


def test_a_dead_session_is_skipped_not_fatal(tmp_path: Path) -> None:
    run = _run(tmp_path, "cell_a", [2.0])
    _session(run, "baseline_r00", tools={"build": (3, 0)}, prompt=100, cached=50, requests=1)
    dead = run / "trajectories" / "refine_r01"
    dead.mkdir(parents=True)
    (dead / "stdout.json").write_text("")                    # the CLI was killed before it printed

    assert len(_sessions(tmp_path)) == 2
    calls, _ = tool_rates(_sessions(tmp_path))
    assert calls == {"build": 3}
