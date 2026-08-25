"""Paired analysis of a ``compare_backends`` battery: harness arm − one-shot arm per prompt.

``bench/_compare_report.py`` tabulates arms; this answers the paper's question — *is the
harness lift separated from noise?* — the way ``docs/report.html`` §6 states it: every
comparison carries its paired standard error, a 95 % confidence interval and the exact
two-sided sign test, and a comparison whose interval crosses zero is labelled
``unsupported`` however good the mean looks.

    python bench/paired_compare.py bench/out/compare_v4            # writes paired.md + paired.json

Pairing rules (the same ones the arm table uses): the append-only ``results.jsonl`` is
deduplicated per (prompt, arm) with the LAST row winning; a cell that ended
``infra_failed`` (provider outage) tests nothing about the model and drops the pair; a
failed build scored 0 by the fixed judge stays in (that is a real outcome); a cell with
no score for any other reason (budget exhausted with no artifact) drops the pair and is
counted.  Tiers are reported separately because the lift is expected to shrink as
prompts get easier.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # `python bench/paired_compare.py` from anywhere
    sys.path.insert(0, str(REPO))

from bench._ab_report import _sign_test  # noqa: E402
from bench._compare_report import CellResult  # noqa: E402
from bench._jsonl import read_jsonl  # noqa: E402

# two-sided 97.5 % Student-t quantiles by degrees of freedom (df 1..30, then 40/60/120, ∞);
# a table, not scipy: the [urdf] extra is optional and this must run on a [dev] install
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
         10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
         18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
         26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980}


def t975(df: int) -> float:
    if df <= 0:
        return math.nan
    if df in _T975:
        return _T975[df]
    for bound in (40, 60, 120):
        if df < bound:
            return _T975[bound]
    return 1.960


class PairedStats(BaseModel):
    """One (harness arm, one-shot arm[, tier]) comparison."""

    harness_arm: str
    oneshot_arm: str
    tier: str = Field(default="all")
    n: int = Field(description="prompts with a score on BOTH arms")
    dropped_infra: int = Field(default=0, description="pairs lost to a provider outage on either arm")
    dropped_unscored: int = Field(default=0, description="pairs lost to an unscored, non-outage cell")
    mean_harness: float | None = None
    mean_oneshot: float | None = None
    mean_delta: float | None = None
    sd_delta: float | None = None
    se_delta: float | None = None
    ci95_low: float | None = None
    ci95_high: float | None = None
    wins: int = 0
    losses: int = 0
    ties: int = 0
    sign_p: float | None = None
    pass_rate_harness: float | None = None
    pass_rate_oneshot: float | None = None
    build_ok_harness: float | None = None
    build_ok_oneshot: float | None = None
    verdict: str = Field(default="", description="supported | unsupported | too few pairs")
    deltas: dict[str, float] = Field(default_factory=dict, description="prompt id → harness − one-shot")


def latest_cells(rows: list[CellResult]) -> dict[tuple[str, str], CellResult]:
    """(prompt_id, arm) → last recorded row (the journal is append-only; a re-run supersedes)."""
    out: dict[tuple[str, str], CellResult] = {}
    for r in rows:
        out[(r.prompt_id, r.arm)] = r
    return out


def _usable(r: CellResult | None) -> tuple[bool, str]:
    if r is None:
        return False, "missing"
    if r.status == "infra_failed":
        return False, "infra"
    if r.score is None:
        return False, "unscored"
    return True, ""


def paired(cells: dict[tuple[str, str], CellResult], harness_arm: str, oneshot_arm: str, *,
           tier: str = "all") -> PairedStats:
    prompts = sorted({p for (p, a) in cells if a in (harness_arm, oneshot_arm)})
    st = PairedStats(harness_arm=harness_arm, oneshot_arm=oneshot_arm, tier=tier, n=0)
    hs: list[float] = []
    os_: list[float] = []
    hp: list[bool] = []
    op: list[bool] = []
    hb: list[bool] = []
    ob: list[bool] = []
    for p in prompts:
        h, o = cells.get((p, harness_arm)), cells.get((p, oneshot_arm))
        if tier != "all" and (h or o).tier != tier:
            continue
        ok_h, why_h = _usable(h)
        ok_o, why_o = _usable(o)
        if not (ok_h and ok_o):
            if "infra" in (why_h, why_o):
                st.dropped_infra += 1
            elif "missing" not in (why_h, why_o):  # a cell that has not run yet is not a drop
                st.dropped_unscored += 1
            continue
        assert h is not None and o is not None and h.score is not None and o.score is not None
        hs.append(h.score)
        os_.append(o.score)
        st.deltas[p] = round(h.score - o.score, 4)
        hp.append(bool(h.passed))
        op.append(bool(o.passed))
        hb.append(h.build_ok)
        ob.append(o.build_ok)
    st.n = len(hs)
    if st.n == 0:
        st.verdict = "too few pairs"
        return st
    deltas = list(st.deltas.values())
    st.mean_harness = round(statistics.fmean(hs), 4)
    st.mean_oneshot = round(statistics.fmean(os_), 4)
    st.mean_delta = round(statistics.fmean(deltas), 4)
    st.wins = sum(d > 0 for d in deltas)
    st.losses = sum(d < 0 for d in deltas)
    st.ties = st.n - st.wins - st.losses
    st.sign_p = _sign_test(deltas)["sign_p"]  # type: ignore[assignment]
    st.pass_rate_harness = round(sum(hp) / st.n, 4)
    st.pass_rate_oneshot = round(sum(op) / st.n, 4)
    st.build_ok_harness = round(sum(hb) / st.n, 4)
    st.build_ok_oneshot = round(sum(ob) / st.n, 4)
    if st.n >= 2:
        sd = statistics.stdev(deltas)
        se = sd / math.sqrt(st.n)
        half = t975(st.n - 1) * se
        st.sd_delta, st.se_delta = round(sd, 4), round(se, 4)
        st.ci95_low, st.ci95_high = round(st.mean_delta - half, 4), round(st.mean_delta + half, 4)
        st.verdict = "supported" if (st.ci95_low > 0 or st.ci95_high < 0) else "unsupported"
    else:
        st.verdict = "too few pairs"
    return st


def analyse(rows: list[CellResult]) -> list[PairedStats]:
    cells = latest_cells(rows)
    arms = sorted({a for (_, a) in cells})
    harness = [a for a in arms if a.startswith("harness:")]
    oneshot = [a for a in arms if a.startswith("oneshot")]
    tiers = sorted({r.tier for r in cells.values() if r.tier}, key=lambda t: ("easy", "medium", "hard").index(t) if t in ("easy", "medium", "hard") else 9)
    out: list[PairedStats] = []
    for ha in harness:
        for oa in oneshot:
            out.append(paired(cells, ha, oa))
            for t in tiers:
                out.append(paired(cells, ha, oa, tier=t))
    return out


def _f(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x:+.3f}" if signed else f"{x:.3f}"


def render_markdown(stats: list[PairedStats], title: str) -> str:
    lines = [f"# paired harness − one-shot — {title}", "",
             "Mean Δ with its paired 95 % t-interval and the exact two-sided sign test over prompts scored on BOTH arms; "
             "`infra` = pairs dropped to a provider outage, `unscored` = pairs dropped to a cell with no score for any other reason. "
             "A comparison is **supported** only when the interval excludes zero.", "",
             "| harness arm | one-shot arm | tier | n | infra | unscored | harness | one-shot | Δ | 95 % CI | W/L/T | sign p | pass h/o | build h/o | verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in stats:
        ci = f"[{_f(s.ci95_low, True)}, {_f(s.ci95_high, True)}]" if s.ci95_low is not None else "—"
        lines.append(
            f"| {s.harness_arm} | {s.oneshot_arm} | {s.tier} | {s.n} | {s.dropped_infra} | {s.dropped_unscored} | "
            f"{_f(s.mean_harness)} | {_f(s.mean_oneshot)} | {_f(s.mean_delta, True)} | {ci} | {s.wins}/{s.losses}/{s.ties} | "
            f"{_f(s.sign_p)} | {_f(s.pass_rate_harness)}/{_f(s.pass_rate_oneshot)} | {_f(s.build_ok_harness)}/{_f(s.build_ok_oneshot)} | "
            f"**{s.verdict}** |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out_dir", help="a compare_backends output directory (holds results.jsonl)")
    ns = ap.parse_args(argv)
    out = Path(ns.out_dir)
    rows = read_jsonl(out / "results.jsonl", CellResult)
    stats = analyse(rows)
    md = render_markdown(stats, out.name)
    (out / "paired.md").write_text(md)
    (out / "paired.json").write_text(json.dumps([s.model_dump(mode="json") for s in stats], indent=2))
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
