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


def test_the_coupling_survey_counts_degrees_of_freedom_not_joints(tmp_path: Path) -> None:
    """``bench/coupling_stats.py`` reads the mechanism claim off the recorded URDFs: what
    the sampler used to drive (every movable joint) against what it drives now (the
    independent ones).  Both are properties of the file, so no re-run is needed."""
    from bench.coupling_stats import per_prompt, report, survey

    urdf = """<?xml version="1.0"?>
<robot name="rig">
  <link name="base"/><link name="a"/><link name="b"/>
  <joint name="drive" type="revolute"><parent link="base"/><child link="a"/>
    <axis xyz="0 0 1"/><limit lower="0" upper="1" effort="1" velocity="1"/></joint>
  <joint name="follow" type="revolute"><parent link="a"/><child link="b"/>
    <axis xyz="0 0 1"/><limit lower="0" upper="1" effort="1" velocity="1"/>
    <mimic joint="drive" multiplier="1" offset="0"/></joint>
</robot>
"""
    cell = tmp_path / "cells" / "art_hard_umbrella" / "arm" / "run" / "artifacts"
    cell.mkdir(parents=True)
    (cell / "robot.urdf").write_text(urdf)
    (tmp_path / "cells" / "plain" / "arm" / "run" / "artifacts").mkdir(parents=True)
    (tmp_path / "cells" / "plain" / "arm" / "run" / "artifacts" / "robot.urdf").write_text(
        urdf.replace('<mimic joint="drive" multiplier="1" offset="0"/>', ""))

    s = survey(tmp_path)
    assert s["urdfs"] == 2 and s["prompts"] == 2 and s["coupled_prompts"] == 1
    assert s["coupled"] == [(2, 1)]                       # 2 movable joints, 1 degree of freedom
    assert "| 2 | 2 | 1 | 1 | 2 | 1 | 1 |" in report([tmp_path])
    assert "| art_hard_umbrella | 1 | 2 | 1 | 1 |" in per_prompt(tmp_path)
