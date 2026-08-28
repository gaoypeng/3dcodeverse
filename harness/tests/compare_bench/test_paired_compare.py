"""bench/paired_compare.py: paired Δ, t-interval and sign test over a compare battery."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._compare_report import CellResult  # noqa: E402
from bench.paired_compare import analyse, latest_cells, main, paired, t975  # noqa: E402

HA, OA = "harness:gemini-cli:gemini-3.6-flash", "oneshot:gemini:x"


def _cell(pid: str, arm: str, score: float | None, *, tier: str = "hard", status: str = "scored",
          passed: bool = False, build_ok: bool = True) -> CellResult:
    return CellResult(prompt_id=pid, tier=tier, arm=arm, kind="harness" if arm.startswith("harness") else "oneshot",
                      status=status, score=score, passed=passed, build_ok=build_ok)


def test_paired_stats_ci_sign_test_and_drops():
    rows = []
    # 6 prompts: harness better on 5, worse on 1 → sign test 5 vs 1 (p = 0.2188)
    for i, (h, o) in enumerate([(0.8, 0.6), (0.7, 0.5), (0.9, 0.6), (0.75, 0.7), (0.6, 0.65), (0.85, 0.5)]):
        rows += [_cell(f"p{i}", HA, h, passed=h >= 0.72), _cell(f"p{i}", OA, o)]
    # a provider outage on one arm drops the pair (not scored 0); an unscored budget cell drops it too
    rows += [_cell("p6", HA, None, status="infra_failed"), _cell("p6", OA, 0.4)]
    rows += [_cell("p7", HA, None, status="budget_exhausted"), _cell("p7", OA, 0.4)]
    # the journal is append-only: a re-run of p0's harness cell supersedes the first row
    rows += [_cell("p0", HA, 0.82, passed=True)]
    st = paired(latest_cells(rows), HA, OA)
    assert st.n == 6 and st.dropped_infra == 1 and st.dropped_unscored == 1
    assert st.deltas["p0"] == 0.22 and st.wins == 5 and st.losses == 1 and st.ties == 0
    assert st.sign_p == 0.2188
    assert st.mean_delta == round((0.22 + 0.2 + 0.3 + 0.05 - 0.05 + 0.35) / 6, 4)
    assert st.ci95_low is not None and st.ci95_high is not None and st.ci95_low < st.mean_delta < st.ci95_high
    assert st.ci95_high - st.mean_delta == round(t975(5) * st.se_delta, 4) or abs((st.ci95_high - st.mean_delta) - t975(5) * st.se_delta) < 1e-3
    assert st.verdict == "supported" and st.pass_rate_harness == round(4 / 6, 4) and st.pass_rate_oneshot == 0.0


def test_interval_crossing_zero_is_unsupported_and_tiers_split():
    rows = []
    for i, (h, o, t) in enumerate([(0.8, 0.7, "easy"), (0.6, 0.7, "easy"), (0.9, 0.5, "hard"), (0.5, 0.8, "hard")]):
        rows += [_cell(f"p{i}", HA, h, tier=t), _cell(f"p{i}", OA, o, tier=t)]
    stats = analyse(rows)
    by = {(s.tier): s for s in stats}
    assert set(by) == {"all", "easy", "hard"}
    assert by["all"].n == 4 and by["all"].verdict == "unsupported" and by["all"].ci95_low < 0 < by["all"].ci95_high
    assert by["easy"].n == 2 and by["hard"].n == 2 and by["hard"].wins == 1 and by["hard"].losses == 1


def test_t_quantiles_are_monotone_and_end_at_normal():
    assert t975(1) > t975(5) > t975(30) > t975(59) > t975(1000) == 1.96
    assert t975(0) != t975(0)  # nan for no degrees of freedom


def test_cli_writes_paired_md_and_json(tmp_path):
    rows = [_cell("p0", HA, 0.8), _cell("p0", OA, 0.5), _cell("p1", HA, 0.7), _cell("p1", OA, 0.6)]
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r.model_dump(mode="json")) for r in rows) + "\n")
    assert main([str(tmp_path)]) == 0
    md = (tmp_path / "paired.md").read_text()
    assert "| harness arm |" in md and HA in md and "**" in md
    data = json.loads((tmp_path / "paired.json").read_text())
    assert data["paired"][0]["n"] == 2 and data["paired"][0]["wins"] == 2
    assert md.count("## judge-free") == 1 and {g["arm"] for g in data["gates"]} == {HA, OA}


def test_degraded_harness_cells_are_reported_and_can_be_excluded():
    """A storm-degraded harness cell (flagged by compare_backends.flag_degraded) stays in the
    headline row but is counted, and an `all −degraded` row repeats the comparison without it."""
    rows = []
    for i, (h, o, deg) in enumerate([(0.9, 0.5, False), (0.8, 0.6, False), (0.3, 0.6, True), (0.2, 0.5, True)]):
        hc = _cell(f"p{i}", HA, h)
        hc.degraded, hc.degraded_reason = deg, "ceiling stop after 0 completed round(s) in 45 min" if deg else ""
        rows += [hc, _cell(f"p{i}", OA, o)]
    stats = {s.tier: s for s in analyse(rows)}
    assert stats["all"].n == 4 and stats["all"].degraded_kept == 2 and stats["all"].dropped_degraded == 0
    assert stats["all"].mean_delta == round((0.4 + 0.2 - 0.3 - 0.3) / 4, 4)
    excl = stats["all −degraded"]
    assert excl.n == 2 and excl.dropped_degraded == 2 and excl.degraded_kept == 0
    assert excl.mean_delta == round((0.4 + 0.2) / 2, 4) and excl.wins == 2
    # no degraded cells → no extra row
    plain = analyse([_cell("q", HA, 0.7), _cell("q", OA, 0.5)])
    assert "all −degraded" not in {s.tier for s in plain}

