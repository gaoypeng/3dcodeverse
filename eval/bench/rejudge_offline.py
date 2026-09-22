"""Re-aggregate every stored judge verdict through today's scoring code — no model calls.

Every static-object verdict on disk carries its raw samples (``Judgment.raw`` →
``samples``), and ``rounds/rNN.json`` carries the gates, renders and console errors
that round was scored against.  So the whole scoring pipeline downstream of the model
— majority vote, the measured-absent veto, defect penalties, the cap ladder, pass —
can be replayed from disk through :func:`codeverse3d.judges.rubrics.aggregate_samples`
and diffed against what the run recorded.

Two uses:

* **identity** — run it on an unchanged tree: every verdict must reproduce to 1e-9.
  That is the acceptance test for this tool, and the guard that says the corpus is
  still readable.  Only verdicts stamped with today's ``rubrics.SCORING_VERSION`` are
  held to it; one scored under an older version (or an older rubric hash) is DRIFT —
  reported, not failed: the scoring moved, the tool did not.  Which means the guard
  is DORMANT on today's corpus: every verdict on disk was written before the stamp
  (version 0), so nothing is held until a version-``SCORING_VERSION`` run is recorded.
  An empty held set is said out loud and exits 2 — a green line that checked nothing
  is what a guard must never print — unless ``--allow-empty-identity`` is passed.
* **impact** — change ``rubrics.py`` or a rubric YAML, run it again: every moved
  verdict, with its before/after caps and defects, the shift in pass rate, in the
  0.600 spike, in σ and in pearson(gate errors, overall).  That is how a scoring
  change is measured BEFORE a single dollar is spent re-judging (docs/EVAL.md §8:
  a battery cannot resolve a 0.03 change; 420 stored rounds can).

Cost: seconds of CPU.  It never writes into a run.

CLI::

    python bench/rejudge_offline.py bench/out --rubric static_object_v1 --out scratch/replay
    python bench/rejudge_offline.py bench/out --rubric static_object_v1 --identity   # exit 1 on any drift, 2 when nothing was held
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse3d (harness/) + the `bench` package (eval/)

from bench.stats import correlation  # noqa: E402
from codeverse3d.addons.calibration import _run_label  # noqa: E402
from codeverse3d.contracts.artifacts import GateReport, Severity  # noqa: E402
from codeverse3d.contracts.plan import AcceptanceItem  # noqa: E402
from codeverse3d.contracts.run import RoundRecord  # noqa: E402
from codeverse3d.judges.rubrics import (  # noqa: E402
    SCORING_VERSION,
    JudgeOutput,
    Rubric,
    aggregate_samples,
    load_rubric,
)
from codeverse3d.proc import read_json_or_none  # noqa: E402

TOL = 1e-9


class ReplayRow(BaseModel):
    run: str
    round: int
    kind: str
    rubric_hash_stored: str
    rubric_hash_now: str
    scoring_version_stored: int = Field(default=0, description="raw.scoring_version the run wrote; 0 = before the stamp")
    scoring_version_now: int = SCORING_VERSION
    gate_errors: int
    stored: float
    replay: float
    delta: float
    stored_passed: bool
    replay_passed: bool
    caps_before: list[str]
    caps_after: list[str]
    defects_before: list[str]
    defects_after: list[str]
    overridden: list[str] = Field(default_factory=list, description="defects the measured-absent veto switched off on replay")
    error: str = ""


class ReplayReport(BaseModel):
    rubric: str
    n: int
    n_errors: int
    n_moved: int
    n_rubric_drift: int = Field(description="verdicts whose stored rubric hash differs from today's — drift, not a tool fault")
    n_scoring_drift: int = Field(default=0, description="verdicts stamped with an older SCORING_VERSION (or none) — drift, not a tool fault")
    mean_delta: float
    median_delta: float
    max_abs_delta: float
    pass_rate_before: float
    pass_rate_after: float
    spike_600_before: int
    spike_600_after: int
    sd_before: float
    sd_after: float
    pearson_errors_before: float | None
    pearson_errors_after: float | None
    cap_counts_before: dict[str, int]
    cap_counts_after: dict[str, int]
    defect_counts_before: dict[str, int]
    defect_counts_after: dict[str, int]
    overridden_counts: dict[str, int] = Field(default_factory=dict,
                                              description="defect id → verdicts where the measured-absent veto switched it off on replay")
    rows: list[ReplayRow]

    def to_markdown(self) -> str:
        f = lambda v: "n/a" if v is None else f"{v:+.3f}"  # noqa: E731
        moved = [r for r in self.rows if abs(r.delta) > TOL]
        lines = [
            f"# offline replay — {self.rubric}",
            "",
            f"n={self.n} verdicts ({self.n_errors} unreadable, {self.n_rubric_drift} scored under another rubric hash, "
            f"{self.n_scoring_drift} under an older scoring version than {SCORING_VERSION})",
            f"moved: **{self.n_moved}**  mean Δ {self.mean_delta:+.4f}  median Δ {self.median_delta:+.4f}  max |Δ| {self.max_abs_delta:.4f}",
            "vetoed on replay (measured absent): " + (", ".join(f"{k} {v}" for k, v in sorted(self.overridden_counts.items())) or "none"),
            f"pass rate {self.pass_rate_before:.1%} → {self.pass_rate_after:.1%}   "
            f"scores pinned at 0.600: {self.spike_600_before} → {self.spike_600_after}   "
            f"σ {self.sd_before:.3f} → {self.sd_after:.3f}   pearson(gate errors, overall) {f(self.pearson_errors_before)} → {f(self.pearson_errors_after)}",
            "",
            "| cap rule | before | after |", "|---|---|---|",
        ]
        for k in sorted(set(self.cap_counts_before) | set(self.cap_counts_after)):
            lines.append(f"| {k} | {self.cap_counts_before.get(k, 0)} | {self.cap_counts_after.get(k, 0)} |")
        lines += ["", "| defect | before | after |", "|---|---|---|"]
        for k in sorted(set(self.defect_counts_before) | set(self.defect_counts_after)):
            lines.append(f"| {k} | {self.defect_counts_before.get(k, 0)} | {self.defect_counts_after.get(k, 0)} |")
        if moved:
            lines += ["", f"## moved verdicts ({len(moved)})", "",
                      "| run | round | sv | gate err | stored | replay | Δ | caps before → after | overridden |", "|---|---|---|---|---|---|---|---|---|"]
            for r in sorted(moved, key=lambda r: -abs(r.delta))[:200]:
                lines.append(f"| {r.run} | r{r.round:02d} | {r.scoring_version_stored} | {r.gate_errors} | {r.stored:.3f} | {r.replay:.3f} | {r.delta:+.3f} | "
                             f"{', '.join(r.caps_before) or '-'} → {', '.join(r.caps_after) or '-'} | {', '.join(r.overridden) or '-'} |")
        return "\n".join(lines)


# --------------------------------------------------------------------------- discovery
def round_files(roots: list[Path]) -> list[Path]:
    """Every ``rounds/rNN.json`` under the roots, once each (symlinked layouts deduped)."""
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        for p in sorted(Path(root).rglob("rounds/r[0-9][0-9].json")):
            real = p.resolve()
            if real in seen:
                continue
            seen.add(real)
            out.append(p)
    return out


def _acceptance_items(run_dir: Path) -> list[AcceptanceItem]:
    """The plan's acceptance list; a candidate sub-workspace inherits its parent's plan."""
    d = run_dir
    for _ in range(4):
        plan = read_json_or_none(d / "plan.json")
        if plan:
            return [AcceptanceItem.model_validate(a) for a in (plan.get("acceptance") or [])]
        d = d.parent
    return []


# --------------------------------------------------------------------------- replay
def replay_round(path: Path, rubric: Rubric | None = None) -> ReplayRow | None:
    """Replay one stored round; ``None`` when the round carries no judged samples."""
    rec = RoundRecord.model_validate_json(path.read_text())
    stored = rec.judgment
    if stored is None or not stored.raw:
        return None
    raw = json.loads(stored.raw)
    samples = [JudgeOutput.model_validate(s) for s in raw.get("samples") or []]
    if not samples:
        return None
    run_dir = path.parent.parent
    rub = rubric or load_rubric(stored.rubric)
    if rub.name != stored.rubric:
        return None
    gates: list[GateReport] = list(rec.gates)
    views = list(rec.renders.views) if rec.renders else []
    console = list(rec.renders.console_errors) if rec.renders else []
    now = aggregate_samples(
        rub, samples, gates=gates, acceptance_items=_acceptance_items(run_dir),
        console_errors=console, views=views, judge_backend=stored.judge_backend,
        n_requested=raw.get("n_requested"), sample_errors=raw.get("sample_errors"),
        judge_prompt_hash=raw.get("judge_prompt_hash", ""),
    )
    raw_now = json.loads(now.raw)
    gate_errors = sum(1 for g in gates for f in g.findings if f.severity == Severity.ERROR)
    return ReplayRow(
        run=_run_label(run_dir), round=rec.index, kind=rec.kind,
        rubric_hash_stored=raw.get("rubric_hash", ""), rubric_hash_now=raw_now.get("rubric_hash", ""),
        scoring_version_stored=int(raw.get("scoring_version", 0)), scoring_version_now=int(raw_now.get("scoring_version", 0)),
        gate_errors=gate_errors,
        stored=stored.overall, replay=now.overall, delta=round(now.overall - stored.overall, 6),
        stored_passed=stored.passed, replay_passed=now.passed,
        caps_before=[c["rule"] for c in (raw.get("caps") or {}).get("caps_applied", [])],
        caps_after=[c["rule"] for c in (raw_now.get("caps") or {}).get("caps_applied", [])],
        defects_before=[d for d, on in (raw.get("defects") or {}).items() if on],
        defects_after=[d for d, on in (raw_now.get("defects") or {}).items() if on],
        overridden=list(raw_now.get("overridden") or []),
    )


def replay_corpus(roots: list[Path], *, rubric_name: str, rubric: Rubric | None = None,
                  kinds: set[str] | None = None) -> ReplayReport:
    rows: list[ReplayRow] = []
    n_err = 0
    for p in round_files(roots):
        try:
            row = replay_round(p, rubric)
        except Exception as e:  # noqa: BLE001 — one corrupt round must not stop the corpus
            n_err += 1
            rows.append(ReplayRow(run=str(p), round=-1, kind="?", rubric_hash_stored="", rubric_hash_now="",
                                  gate_errors=0, stored=0.0, replay=0.0, delta=0.0, stored_passed=False,
                                  replay_passed=False, caps_before=[], caps_after=[], defects_before=[],
                                  defects_after=[], error=f"{type(e).__name__}: {e}"))
            continue
        if row is None:
            continue
        rec_rubric = json.loads(p.read_text()).get("judgment", {}).get("rubric")
        if rec_rubric != rubric_name:
            continue
        if kinds and row.kind not in kinds:
            continue
        rows.append(row)
    ok = [r for r in rows if not r.error]
    deltas = [r.delta for r in ok]
    before = [r.stored for r in ok]
    after = [r.replay for r in ok]
    errs = [float(r.gate_errors) for r in ok]
    caps_b = Counter(c for r in ok for c in r.caps_before)
    caps_a = Counter(c for r in ok for c in r.caps_after)
    def_b = Counter(d for r in ok for d in r.defects_before)
    def_a = Counter(d for r in ok for d in r.defects_after)
    return ReplayReport(
        rubric=rubric_name, n=len(ok), n_errors=n_err,
        n_moved=sum(1 for d in deltas if abs(d) > TOL),
        n_rubric_drift=sum(1 for r in ok if r.rubric_hash_stored and r.rubric_hash_stored != r.rubric_hash_now),
        n_scoring_drift=sum(1 for r in ok if r.scoring_version_stored != SCORING_VERSION),
        mean_delta=round(statistics.fmean(deltas), 6) if deltas else 0.0,
        median_delta=round(statistics.median(deltas), 6) if deltas else 0.0,
        max_abs_delta=round(max((abs(d) for d in deltas), default=0.0), 6),
        pass_rate_before=(sum(r.stored_passed for r in ok) / len(ok)) if ok else 0.0,
        pass_rate_after=(sum(r.replay_passed for r in ok) / len(ok)) if ok else 0.0,
        spike_600_before=sum(1 for v in before if abs(v - 0.6) < 1e-6),
        spike_600_after=sum(1 for v in after if abs(v - 0.6) < 1e-6),
        sd_before=round(statistics.pstdev(before), 4) if len(before) > 1 else 0.0,
        sd_after=round(statistics.pstdev(after), 4) if len(after) > 1 else 0.0,
        pearson_errors_before=correlation(errs, before), pearson_errors_after=correlation(errs, after),
        cap_counts_before=dict(caps_b), cap_counts_after=dict(caps_a),
        defect_counts_before=dict(def_b), defect_counts_after=dict(def_a),
        overridden_counts=dict(Counter(d for r in ok for d in r.overridden)),
        rows=rows,
    )


# --------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("roots", nargs="+", type=Path)
    ap.add_argument("--rubric", default="static_object_v1", help="rubric NAME the stored verdicts must carry")
    ap.add_argument("--rubric-file", type=Path, default=None,
                    help="score with THIS yaml instead of the installed rubric of the same name (a variant under test)")
    ap.add_argument("--kinds", default="", help="comma list of round kinds to keep (default all, incl. candidate)")
    ap.add_argument("--out", type=Path, default=None, help="directory for replay.json + replay.md")
    ap.add_argument("--identity", action="store_true",
                    help="assert nothing moved: exit 1 on any |Δ| > 1e-9 outside rubric drift; "
                         "exit 2 when no verdict carries today's scoring version (the guard is dormant)")
    ap.add_argument("--allow-empty-identity", action="store_true",
                    help="with --identity: exit 0 even when no verdict was held (the warning still prints)")
    a = ap.parse_args(argv)
    rubric = load_rubric(str(a.rubric_file)) if a.rubric_file else None
    kinds = {k for k in a.kinds.split(",") if k} or None
    rep = replay_corpus(a.roots, rubric_name=a.rubric, rubric=rubric, kinds=kinds)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "replay.json").write_text(rep.model_dump_json(indent=1))
        (a.out / "replay.md").write_text(rep.to_markdown())
    print(rep.to_markdown().split("\n## moved")[0])
    if a.identity:
        current = [r for r in rep.rows if not r.error
                   and r.scoring_version_stored == SCORING_VERSION and r.rubric_hash_stored == r.rubric_hash_now]
        bad = [r for r in current if abs(r.delta) > TOL]
        if bad:
            print(f"\nIDENTITY FAILED: {len(bad)} verdict(s) under scoring version {SCORING_VERSION} "
                  "and today's rubric hash do not reproduce:", file=sys.stderr)
            for r in bad[:20]:
                print(f"  {r.run} r{r.round:02d}: stored {r.stored:.4f} replay {r.replay:.4f}", file=sys.stderr)
            return 1
        if not current:
            # "identity OK: 0 of 419" once printed green over a corpus written entirely
            # before the version stamp — a guard that holds nothing has checked nothing.
            print(f"\nWARNING: identity guard is DORMANT: no verdict on disk carries scoring version "
                  f"{SCORING_VERSION} under today's rubric hash; nothing was checked "
                  f"({rep.n} verdicts, {rep.n_scoring_drift} under an older scoring version, "
                  f"{rep.n_rubric_drift} under another rubric hash)", file=sys.stderr)
            return 0 if a.allow_empty_identity else 2
        print(f"\nidentity OK: {len(current)} of {rep.n} verdicts held to it reproduce "
              f"({rep.n_scoring_drift} under an older scoring version, {rep.n_rubric_drift} under another rubric hash: drift, not failure)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
