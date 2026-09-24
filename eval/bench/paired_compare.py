"""Paired analysis of a ``compare_backends`` battery: harness arm − one-shot arm per prompt.

``bench/_compare_report.py`` tabulates arms; this answers the paper's question — *is the
harness lift separated from noise?* — the way the retired compare report framed it: every
comparison carries its paired standard error, a 95 % confidence interval and the exact
two-sided sign test, and the verdict is ``bench.stats.decide`` — the one paired rule every
report uses (N84): ``better`` / ``worse`` only when the interval excludes zero, else
``inconclusive`` however good the mean looks.

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
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parents[2] / "harness"   # the harness tree these scripts evaluate
for _p in (REPO, Path(__file__).resolve().parents[1]):      # its codeverse3d + the `bench` package (eval/)
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bench._ab_report import _fmt as _f  # noqa: E402
from bench._compare_report import CellResult  # noqa: E402
from bench._jsonl import latest, read_jsonl  # noqa: E402
from bench.stats import MIN_PAIRS, decide, mean_ci, sign_test  # noqa: E402


class PairedStats(BaseModel):
    """One (harness arm, one-shot arm[, tier]) comparison."""

    harness_arm: str
    oneshot_arm: str
    tier: str = Field(default="all")
    n: int = Field(description="prompts with a score on BOTH arms")
    dropped_infra: int = Field(default=0, description="pairs lost to a provider outage on either arm")
    dropped_unscored: int = Field(default=0, description="pairs lost to an unscored, non-outage cell")
    dropped_degraded: int = Field(default=0, description="pairs excluded because the harness cell is storm-degraded (exclude_degraded=True)")
    degraded_kept: int = Field(default=0, description="pairs kept whose harness cell is storm-degraded (exclude_degraded=False)")
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
    verdict: str = Field(default="", description="harness better | worse | inconclusive (stats.decide) | too few pairs")
    deltas: dict[str, float] = Field(default_factory=dict, description="prompt id → harness − one-shot")


def _usable(r: CellResult | None) -> tuple[bool, str]:
    if r is None:
        return False, "missing"
    if r.status == "infra_failed":
        return False, "infra"
    if r.score is None:
        return False, "unscored"
    return True, ""


def paired(cells: dict[tuple[str, str], CellResult], harness_arm: str, oneshot_arm: str, *,
           tier: str = "all", exclude_degraded: bool = False) -> PairedStats:
    """One comparison over ``cells`` — ``bench/_jsonl.latest``: (prompt, arm) → last row."""
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
        if h.degraded or o.degraded:
            if exclude_degraded:
                st.dropped_degraded += 1
                continue
            st.degraded_kept += 1
        hs.append(h.score)
        os_.append(o.score)
        st.deltas[p] = round(h.score - o.score, 4)
        if h.passed is not None:  # a bench-run row carries no pass/fail (2026-09-22): no rate
            hp.append(h.passed)
        if o.passed is not None:
            op.append(o.passed)
        hb.append(h.build_ok)
        ob.append(o.build_ok)
    st.n = len(hs)
    if st.n == 0:
        st.verdict = "too few pairs"
        return st
    ci = mean_ci(list(st.deltas.values()))
    st.mean_harness = round(statistics.fmean(hs), 4)
    st.mean_oneshot = round(statistics.fmean(os_), 4)
    st.mean_delta = round(ci.mean, 4)
    st.wins, st.losses, st.sign_p = sign_test(list(st.deltas.values()))
    st.ties = st.n - st.wins - st.losses
    st.pass_rate_harness = round(sum(hp) / len(hp), 4) if hp else None
    st.pass_rate_oneshot = round(sum(op) / len(op), 4) if op else None
    st.build_ok_harness = round(sum(hb) / st.n, 4)
    st.build_ok_oneshot = round(sum(ob) / st.n, 4)
    d = decide(st.deltas)
    st.verdict = "too few pairs" if d.too_few else d.outcome
    if ci.half is not None:
        st.sd_delta, st.se_delta = round(ci.sd, 4), round(ci.se, 4)
        st.ci95_low, st.ci95_high = round(st.mean_delta - ci.half, 4), round(st.mean_delta + ci.half, 4)
    return st


def analyse(rows: list[CellResult]) -> list[PairedStats]:
    cells = latest(rows)
    arms = sorted({a for (_, a) in cells})
    harness = [a for a in arms if a.startswith("harness:")]
    oneshot = [a for a in arms if a.startswith("oneshot")]
    tiers = sorted({r.tier for r in cells.values() if r.tier}, key=lambda t: ("easy", "medium", "hard").index(t) if t in ("easy", "medium", "hard") else 9)
    out: list[PairedStats] = []
    any_degraded = any(r.degraded for r in cells.values())
    for ha in harness:
        for oa in oneshot:
            out.append(paired(cells, ha, oa))
            if any_degraded:  # the same comparison without the cells that measured the weather
                st = paired(cells, ha, oa, exclude_degraded=True)
                st.tier = "all −degraded"
                out.append(st)
            for t in tiers:
                out.append(paired(cells, ha, oa, tier=t))
    return out


class ArmGateStats(BaseModel):
    """Judge-free numbers per arm: what the deterministic gates and the build say."""

    arm: str
    n: int
    build_ok_rate: float
    mean_gate_errors: float | None
    zero_gate_error_rate: float | None
    mean_tris: float | None
    degraded: int = 0


def gate_stats(cells: dict[tuple[str, str], CellResult]) -> list[ArmGateStats]:
    by: dict[str, list[CellResult]] = defaultdict(list)
    for (_, arm), r in cells.items():
        if r.status != "infra_failed":
            by[arm].append(r)
    out = []
    for arm, rs in sorted(by.items(), key=lambda kv: (not kv[0].startswith("harness:"), kv[0])):
        built = [r for r in rs if r.build_ok]
        out.append(ArmGateStats(
            arm=arm, n=len(rs), build_ok_rate=round(sum(1 for r in rs if r.build_ok) / len(rs), 4) if rs else 0.0,
            mean_gate_errors=round(statistics.fmean(len(r.gate_errors) for r in built), 3) if built else None,
            zero_gate_error_rate=round(sum(1 for r in built if not r.gate_errors) / len(built), 4) if built else None,
            mean_tris=round(statistics.fmean(r.tris for r in built if r.tris is not None), 0)
            if any(r.tris is not None for r in built) else None,
            degraded=sum(1 for r in rs if r.degraded),
        ))
    return out


def render_gate_markdown(gs: list[ArmGateStats]) -> str:
    lines = ["", "## judge-free (build + deterministic gates, cells that ran)", "",
             "| arm | n | build ok | mean gate errors (built) | zero-gate-error (built) | mean tris | degraded |",
             "|---|---|---|---|---|---|---|"]
    for g in gs:
        lines.append(f"| {g.arm} | {g.n} | {g.build_ok_rate:.0%} | {_f(g.mean_gate_errors)} | "
                     f"{'—' if g.zero_gate_error_rate is None else format(g.zero_gate_error_rate, '.0%')} | "
                     f"{'—' if g.mean_tris is None else int(g.mean_tris)} | {g.degraded or ''} |")
    return "\n".join(lines) + "\n"


def render_markdown(stats: list[PairedStats], title: str, gates: list[ArmGateStats] | None = None) -> str:
    lines = [f"# paired harness − one-shot — {title}", "",
             "Mean Δ with its paired 95 % t-interval and the exact two-sided sign test over prompts scored on BOTH arms; "
             "`infra` = pairs dropped to a provider outage, `unscored` = pairs dropped to a cell with no score for any other reason. "
             "The verdict (`bench/stats.decide`) is **better** / **worse** only when the interval excludes zero over at least "
             f"{MIN_PAIRS} pairs.", "",
             "`degraded` = pairs whose harness cell is storm-degraded (kept in every row except `all −degraded`, where they are excluded).", "",
             "| harness arm | one-shot arm | tier | n | infra | unscored | degraded | harness | one-shot | Δ | 95 % CI | W/L/T | sign p | pass h/o | build h/o | verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in stats:
        ci = f"[{_f(s.ci95_low, True)}, {_f(s.ci95_high, True)}]" if s.ci95_low is not None else "—"
        deg = f"{s.dropped_degraded} excl." if s.dropped_degraded else (str(s.degraded_kept) if s.degraded_kept else "")
        lines.append(
            f"| {s.harness_arm} | {s.oneshot_arm} | {s.tier} | {s.n} | {s.dropped_infra} | {s.dropped_unscored} | {deg} | "
            f"{_f(s.mean_harness)} | {_f(s.mean_oneshot)} | {_f(s.mean_delta, True)} | {ci} | {s.wins}/{s.losses}/{s.ties} | "
            f"{_f(s.sign_p)} | {_f(s.pass_rate_harness)}/{_f(s.pass_rate_oneshot)} | {_f(s.build_ok_harness)}/{_f(s.build_ok_oneshot)} | "
            f"**{s.verdict}** |")
    out = "\n".join(lines) + "\n"
    if gates:
        out += render_gate_markdown(gates)
    return out


def rows_from_bench_run(out_dir: Path, arm: str) -> list[CellResult]:
    """A ``bench run`` battery read as cells of one arm.

    ``compare_backends`` writes one journal with an ``arm`` column; ``bench run`` writes a
    directory per arm with ``id`` / ``score_picked`` (``score_final`` before 2026-09-22).  Two of those directories are a paired
    comparison — same prompts, one thing different — and this lets the statistics below
    (paired CI, exact sign test, the one ``stats.decide`` rule) be
    the same for both shapes rather than recomputed by hand.
    """
    rows: list[CellResult] = []
    for raw in latest(read_jsonl(out_dir / "results.jsonl"), key=lambda r: r["id"]).values():
        # a bench-run row records no build flag: a cell with a verdict was built and judged,
        # one without (score None) was not — so `build_ok` here means "judged", and
        # `gen_cost_usd` is the run's WHOLE cost (plan + loop judge), as the field says.  A
        # row written before 2026-09-22 still has its old `passed`; a new one has none.
        score = raw.get("score_picked", raw.get("score_final"))
        rows.append(CellResult(prompt_id=raw["id"], arm=arm, tier=raw.get("tier", ""),
                               score=score, status=raw.get("status", ""),
                               passed=raw.get("passed"), build_ok=score is not None,
                               gen_cost_usd=float(raw.get("cost_usd") or 0.0), kind="harness"))
    return rows


def arm_names(a: Path, b: Path) -> tuple[str, str]:
    """Two distinct labels for two battery directories: the basenames, or — when those
    collide (``x/out`` vs ``y/out``) — enough of the path to tell them apart.  Two arms
    with one name would silently pair every cell with itself."""
    a, b = a.resolve(), b.resolve()
    if a == b:
        raise SystemExit(f"--against names the same battery as out_dir: {a}")
    pa, pb = list(a.parts), list(b.parts)
    depth = 1
    while depth < min(len(pa), len(pb)) and pa[-depth:] == pb[-depth:]:
        depth += 1
    return "/".join(pa[-depth:]), "/".join(pb[-depth:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out_dir", help="a compare_backends output directory (holds results.jsonl)")
    ap.add_argument("--against", type=Path, default=None,
                    help="a SECOND `bench run` output directory: pair the two by prompt id "
                         "(out_dir is then also read as a `bench run` battery)")
    ns = ap.parse_args(argv)
    out = Path(ns.out_dir)
    if ns.against is not None:
        name_a, name_b = arm_names(out, ns.against)
        rows = rows_from_bench_run(out, name_a) + rows_from_bench_run(ns.against, name_b)
        stats = [paired(latest(rows), name_a, name_b)]
        md = render_markdown(stats, f"{name_a} vs {name_b}")
        (out / "paired.md").write_text(md)
        print(md)
        return 0
    rows = read_jsonl(out / "results.jsonl", CellResult)
    stats = analyse(rows)
    gates = gate_stats(latest(rows))
    md = render_markdown(stats, out.name, gates)
    (out / "paired.md").write_text(md)
    (out / "paired.json").write_text(json.dumps({"paired": [s.model_dump(mode="json") for s in stats],
                                                 "gates": [g.model_dump(mode="json") for g in gates]}, indent=2))
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
