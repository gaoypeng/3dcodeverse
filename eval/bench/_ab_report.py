"""Pairing, verdict and ``summary.md`` for ``bench/ab_plan.py``.

An A/B of a plan/brief change is decided on PAIRED deltas, one per prompt, because
the two arms of a prompt ran side by side in the same weather and against the same
fixed judge — that is the whole point of the rig.  Anything that breaks the pair
(an outage on either side, a budget blow-out, a judge error) removes the prompt from
the decision instead of leaning it: a missing arm is a missing measurement, not a
zero (``docs/EVAL.md`` §7).

The verdict rule is deliberately blunt and stated once, in :func:`verdict_of`, so a
report can never be argued into "keep" by hand.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from bench._compare_report import CellResult
from bench._jsonl import latest
from bench.stats import mean_ci, n_to_resolve, sign_test

CONTROL = "control"
VARIANT = "variant"
ARMS = (CONTROL, VARIANT)

#: a paired delta at or below this is a regression on that prompt.  Wider than the
#: fixed judge's own sample noise (n_samples 2 on pro, ~±0.02), so one unlucky verdict
#: is not a regression but a real loss on one prompt is.
REGRESSION_DELTA = -0.03
#: mean paired delta needed to call the change a win / a loss (symmetric)
KEEP_DELTA = 0.02
REVERT_DELTA = -0.02
#: this many regressing prompts is a revert even when the mean looks fine
REVERT_REGRESSIONS = 2
# The keep/revert rule above is the protocol (docs/EVAL.md §8) and a report must not be
# able to argue itself out of it — but the rule alone is dangerously confident.  Measured
# 2026-08-24 with two A/A runs — arms identical by construction — on the SAME prompt
# (`ctrl_med_dining_chair`, rounds 1):
#
#     0.591 vs 0.934  ->  delta +0.344  ->  verdict **keep**
#     0.700 vs 0.600  ->  delta -0.100  ->  verdict **revert**
#
# Nothing was under test either time, and the rule returned opposite decisions.  The
# generator is stochastic; a paired delta carries the spread of two independent
# generations, not the judge's ±0.02 sampling noise.  So every summary states the paired
# 95 % t-interval beside the verdict (``bench/stats.py``), reported, never enforced, and
# says plainly when the threshold is inside it.


class PairOutcome(BaseModel):
    prompt_id: str
    tier: str = ""
    control: CellResult | None = None
    variant: CellResult | None = None
    paired: bool = Field(default=False, description="both arms produced a fixed-judge score")
    reason: str = Field(default="", description="why the prompt is NOT paired (empty when it is)")
    delta: float | None = Field(default=None, description="variant - control, only when paired")

    @property
    def regression(self) -> bool:
        return self.paired and self.delta is not None and self.delta <= REGRESSION_DELTA


class Verdict(BaseModel):
    decision: str = Field(description="keep | revert | inconclusive")
    n_pairs: int
    mean_delta: float | None
    median_delta: float | None
    regressions: list[str] = Field(default_factory=list, description="prompt ids with delta <= REGRESSION_DELTA")
    reason: str = ""
    sd_delta: float | None = Field(default=None, description="stdev of the paired deltas — the rig's own noise (None at n<2)")
    se_delta: float | None = Field(default=None, description="standard error of the mean delta, sd/sqrt(n)")
    ci_half: float | None = Field(default=None, description="half-width of the 95 % t-interval, t(0.975, n-1) * se")
    separated: bool = Field(default=False, description="the 95 % t-interval excludes zero: the decision is outside the noise")
    n_for_power: int | None = Field(default=None, description="pairs this spread needs for the t-interval to fit inside KEEP_DELTA")
    n_up: int = Field(default=0, description="pairs where the variant scored higher (delta > 0)")
    n_down: int = Field(default=0, description="pairs where the variant scored lower (delta < 0)")
    sign_p: float | None = Field(default=None, description="two-sided exact sign test on n_up vs n_down; None with no non-zero deltas")

    @property
    def caution(self) -> str:
        """The sentence a reader needs beside a decision that the data cannot support."""
        if self.n_pairs == 0:
            return ""
        if self.se_delta is None:
            return ("one pair cannot separate a change from run-to-run noise: two A/A runs of this rig "
                    "(identical arms, same prompt) measured +0.344 and -0.100 — 'keep' and 'revert' from "
                    "nothing at all.")
        if self.separated:
            return ""
        need = f"~{self.n_for_power} paired prompts" if self.n_for_power else "more paired prompts"
        return (f"NOT separated from noise: the 95 % t-interval (±{self.ci_half:.3f}) includes zero. "
                f"At this spread {need} would be needed to resolve {KEEP_DELTA:+.2f}. Treat the decision as a screen.")


def _scored(r: CellResult | None) -> bool:
    return r is not None and r.score is not None


def pair_up(rows: list[CellResult], prompt_order: list[tuple[str, str]] | None = None) -> list[PairOutcome]:
    """Fold (prompt, arm) cells into one :class:`PairOutcome` per prompt.

    ``prompt_order`` (id, tier) fixes the row order of the report; only prompts that
    have at least one cell appear (a prompt never selected is not "unpaired"), and
    prompts absent from the order are appended first-seen.  Latest row wins per
    (prompt, arm) — the results file is append-only and a redo re-appends.
    """
    by = latest(rows)
    present = {r.prompt_id for r in rows}
    ids: list[tuple[str, str]] = [(i, t) for i, t in (prompt_order or []) if i in present]
    seen = {i for i, _ in ids}
    for r in rows:
        if r.prompt_id not in seen:
            ids.append((r.prompt_id, r.tier))
            seen.add(r.prompt_id)
    out: list[PairOutcome] = []
    for pid, tier in ids:
        c, v = by.get((pid, CONTROL)), by.get((pid, VARIANT))
        p = PairOutcome(prompt_id=pid, tier=tier, control=c, variant=v)
        if _scored(c) and _scored(v):
            p.paired, p.delta = True, round(v.score - c.score, 4)  # type: ignore[union-attr]
        else:
            p.reason = "; ".join(f"{name} {'missing' if r is None else r.status}"
                                 for name, r in ((CONTROL, c), (VARIANT, v)) if not _scored(r))
        out.append(p)
    return out


def verdict_of(pairs: list[PairOutcome]) -> Verdict:
    """keep iff mean delta >= +0.02 AND no regression; revert iff mean delta <= -0.02
    OR >= 2 regressions; otherwise inconclusive.  No pairs is inconclusive, never keep."""
    deltas = [p.delta for p in pairs if p.paired and p.delta is not None]
    regressions = [p.prompt_id for p in pairs if p.regression]
    if not deltas:
        return Verdict(decision="inconclusive", n_pairs=0, mean_delta=None, median_delta=None,
                       reason="no prompt has both arms scored")
    mean, median = round(statistics.fmean(deltas), 4), round(statistics.median(deltas), 4)
    # the noise block: reported, never enforced.  The SIGN of each delta is far cheaper to
    # move than the mean — 7 of 8 in one direction is p = 0.07, a bar an eight-prompt
    # battery can clear where a ±0.02 mean at a paired sd near 0.23 never will.
    ci = mean_ci(deltas)
    up, down, sign_p = sign_test(deltas)
    v = Verdict(decision="inconclusive", n_pairs=len(deltas), mean_delta=mean, median_delta=median,
                regressions=regressions, n_up=up, n_down=down, sign_p=sign_p, separated=ci.separated)
    if ci.sd is not None:  # one pair has no spread to state
        v.sd_delta, v.se_delta, v.ci_half = round(ci.sd, 4), round(ci.se, 4), round(ci.half, 4)
        v.n_for_power = n_to_resolve(ci.sd, KEEP_DELTA)
    if mean <= REVERT_DELTA or len(regressions) >= REVERT_REGRESSIONS:
        v.decision, v.reason = "revert", (f"mean delta {mean:+.3f} <= {REVERT_DELTA:+.2f}" if mean <= REVERT_DELTA
                                          else f"{len(regressions)} regressions (>= {REVERT_REGRESSIONS})")
    elif mean >= KEEP_DELTA and not regressions:
        v.decision, v.reason = "keep", f"mean delta {mean:+.3f} >= {KEEP_DELTA:+.2f} and no regression"
    else:
        v.reason = (f"mean delta {mean:+.3f} inside ({REVERT_DELTA:+.2f}, {KEEP_DELTA:+.2f})" if not regressions
                    else f"mean delta {mean:+.3f} but {len(regressions)} regression(s): {', '.join(regressions)}")
    return v


class ArmSummary(BaseModel):
    arm: str
    n: int
    n_scored: int
    n_infra_failed: int
    n_budget_exhausted: int
    n_other_unscored: int
    mean_score: float | None
    median_score: float | None
    cost_usd: float = Field(description="generation + fixed-judge spend over every cell that ran")
    mean_minutes: float


def arm_summary(rows: list[CellResult], arm: str) -> ArmSummary:
    rs = [r for r in rows if r.arm == arm]
    ran = [r for r in rs if r.status != "infra_failed"]
    scores = [r.score for r in ran if r.score is not None]
    return ArmSummary(
        arm=arm, n=len(rs), n_scored=len(scores), n_infra_failed=len(rs) - len(ran),
        n_budget_exhausted=sum(r.status == "budget_exhausted" for r in ran),
        n_other_unscored=sum(r.score is None and r.status != "budget_exhausted" for r in ran),
        mean_score=round(statistics.fmean(scores), 4) if scores else None,
        median_score=round(statistics.median(scores), 4) if scores else None,
        cost_usd=round(sum(r.gen_cost_usd + r.judge_cost_usd for r in ran), 4),
        mean_minutes=round(statistics.fmean(r.wall_s for r in ran) / 60, 2) if ran else 0.0,
    )


def _fmt(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x:+.3f}" if signed else f"{x:.3f}"


def _cell(r: CellResult | None) -> str:
    if r is None:
        return "missing"
    return _fmt(r.score) if r.score is not None else r.status


def render_summary(pairs: list[PairOutcome], rows: list[CellResult], *, title: str, variant_env: dict[str, str],
                   generator: str, judge: str, rounds: int, aa: bool = False) -> str:
    """The Markdown report; pure so tests can read it without a filesystem.

    ``aa`` marks a calibration run whose arms are deliberately identical: there the
    measured delta IS the noise floor, and any decision word would be a lie.
    """
    v = verdict_of(pairs)
    arms = [arm_summary(rows, a) for a in ARMS]
    lines = [f"# A/{'A' if aa else 'B'}: {title}", ""]
    if aa:
        lines += ["> **A/A calibration — the arms are identical.** Every delta below is pure run-to-run",
                  "> noise; the verdict word is printed only to show what this rig would have concluded",
                  "> from nothing.  Use the spread as the floor an A/B has to clear.", ""]
    lines += [
        f"variant env: `{' '.join(f'{k}={val}' for k, val in variant_env.items()) or '(none)'}`  ",
        f"generator: `{generator}` · fixed judge: `{judge}` · rounds: {rounds} · "
        f"written {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
        f"## Verdict: **{v.decision}**", "",
        f"{v.reason}.  Paired prompts: {v.n_pairs} · mean delta {_fmt(v.mean_delta, True)} · "
        f"median delta {_fmt(v.median_delta, True)} · regressions (delta <= {REGRESSION_DELTA:+.2f}): "
        f"{', '.join(v.regressions) or 'none'}", "",
        "## Confidence", "",
        f"paired sd {_fmt(v.sd_delta)} · SE {_fmt(v.se_delta)} · 95 % t-interval "
        f"{_fmt(v.mean_delta, True)} ± {_fmt(v.ci_half)} · "
        f"separated from noise: {'yes' if v.separated else 'NO'}",
        f"sign consistency: {v.n_up} up / {v.n_down} down · exact two-sided sign test p = "
        f"{'—' if v.sign_p is None else f'{v.sign_p:.3f}'} — the signal an 8-prompt battery can actually carry",
        *([f"\n**{v.caution}**"] if v.caution else []), "",
        "## Arms", "",
        "| arm | n | scored | infra_failed | budget_exhausted | other unscored | mean | median | cost $ | mean min |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in arms:
        lines.append(f"| {a.arm} | {a.n} | {a.n_scored} | {a.n_infra_failed} | {a.n_budget_exhausted} | "
                     f"{a.n_other_unscored} | {_fmt(a.mean_score)} | {_fmt(a.median_score)} | {a.cost_usd:.2f} | {a.mean_minutes:.1f} |")
    lines += ["", "## Per prompt", "", "| prompt | tier | control | variant | delta | note |", "|---|---|---|---|---|---|"]
    for p in pairs:
        note = "REGRESSION" if p.regression else ("" if p.paired else f"unpaired: {p.reason}")
        lines.append(f"| {p.prompt_id} | {p.tier} | {_cell(p.control)} | {_cell(p.variant)} | {_fmt(p.delta, True)} | {note} |")
    dropped = sum(a.n_infra_failed for a in arms)
    lines += ["", f"n_infra_failed: {dropped}" + (" — re-run with `--redo-status infra_failed` before trusting the verdict"
                                                 if dropped else ""), "",
              "Rule: keep iff mean delta >= +0.02 and no regression; revert iff mean delta <= -0.02 or >= 2 regressions; "
              "else inconclusive.  A prompt counts only when BOTH arms were scored by the fixed judge.", ""]
    return "\n".join(lines)


def write_report(out: Path, rows: list[CellResult], prompt_order: list[tuple[str, str]], **meta: object) -> Verdict:
    """Write ``pairs.json`` + ``summary.md`` under ``out`` and return the verdict."""
    pairs = pair_up(rows, prompt_order)
    v = verdict_of(pairs)
    (out / "pairs.json").write_text(
        json.dumps({"verdict": v.model_dump(mode="json"),
                                  "pairs": [p.model_dump(mode="json") for p in pairs]}, indent=2))
    (out / "summary.md").write_text(render_summary(pairs, rows, **meta))  # type: ignore[arg-type]
    return v


__all__ = ["ARMS", "CONTROL", "KEEP_DELTA", "REGRESSION_DELTA", "REVERT_DELTA",
           "REVERT_REGRESSIONS", "VARIANT",
           "ArmSummary", "PairOutcome", "Verdict", "arm_summary", "pair_up", "render_summary", "verdict_of",
           "write_report"]
