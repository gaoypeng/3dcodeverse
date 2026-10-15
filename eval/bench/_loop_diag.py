"""What an iteration's run records say (private: ``bench.loop diagnose / compare / judge-calib``).

One :class:`RunDigest` per run, read off the things a run leaves behind — the bench row
(``results.jsonl``), the typed ``record.json`` (``codeverse3d.record.record.load_record``), the
picked round's gates / verdict / skills, and the deliverable's source — and three readers over a
battery of them:

* :func:`diagnose_dir` — the numbers and a RECOMMENDATIONS list produced by deterministic rules
  (:data:`RULES`): a rate crosses its threshold → one named lever.  No model reads the diagnosis;
  a person does, before editing the variant.
* :func:`compare_dirs` — iteration N against M, paired by prompt id: ``bench.stats.decide`` on the
  picked score (the one paired rule every report uses, EVAL.md §8) PLUS the deterministic-gate veto —
  the floating + sunken + interpenetration ERROR count per run must not rise by more than
  :data:`VETO_RISE` on average, whatever the judge says (the owner's stricter contact standards,
  2026-10-14, are measured by the gate, not perceived by the VLM).
* :func:`calib_summary` — the judge arm: within-run σ (the re-judge noise), between-run σ (the
  spread the judge is asked to resolve), their ratio, and the gate-score correlations.

Gate findings are counted by their TYPED kind (``skills.registry.finding_kind`` →
``"scene_placement/floating"``), never by message text (Law 4).
"""

from __future__ import annotations

import ast
import json
import math
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bench._jsonl import latest, read_jsonl
from bench._records import records
from bench.paired_compare import PairedStats, paired, paired_rows
from bench.run_bench import BenchItemResult
from bench.stats import correlation, decide
from codeverse3d.addons.calibration import CalibrationRow, CalibrationTable
from codeverse3d.addons.select import summarise
from codeverse3d.contracts.artifacts import Severity
from codeverse3d.contracts.run import RoundRecord, RunRecord
from codeverse3d.record.record import RecordError, load_record, stop_reason
from codeverse3d.skills.registry import finding_kind

#: the gate kinds the owner's contact standards are about (spatial/scene_placement.py data.kind)
PLACEMENT_KINDS: tuple[str, ...] = ("scene_placement/floating", "scene_placement/sunken",
                                    "scene_placement/interpenetration")
#: the veto: a variant whose runs gain more than this many placement ERRORs each, on average, is
#: reverted whatever its score — one more floater per run is inside the gate's own run-to-run swing
#: (the same prompt re-run moves 0-1 findings), two is a change in behaviour
VETO_RISE = 1.0

#: `from '../lib/<x>.js'` / `from './lib/<x>.js'` in an agent-written three.js file.  A regex over
#: SOURCE TEXT (the import specifier), the way B1's `library_rows` reads the catalog — not over an id.
_JS_LIB_IMPORT = re.compile(r"""from\s+['"](?:\.\./|\./)+lib/([A-Za-z0-9_]+)\.js['"]""")
#: the harness-owned library directory inside ``src/`` — its own files import each other and must
#: not count as the AGENT reaching the library (HARNESS_OWNED_SRC: ``src/lib/``)
_LIB_DIR = "lib"


# --------------------------------------------------------------------------- one run
class RunDigest(BaseModel):
    """Everything the diagnosis and the comparison read off one run."""

    id: str
    status: str = ""
    stop_reason: str = ""
    score_baseline: float | None = None
    score_picked: float | None = None
    picked_round: int | None = None
    criteria: dict[str, float] = Field(default_factory=dict, description="the picked verdict's per-criterion scores")
    defects: dict[str, bool] = Field(default_factory=dict, description="the picked verdict's defect checklist")
    caps: list[str] = Field(default_factory=list, description="cap rules applied on the picked verdict")
    gate_errors: dict[str, int] = Field(default_factory=dict, description="typed kind → ERROR findings, picked round")
    gate_warns: dict[str, int] = Field(default_factory=dict, description="typed kind → WARN findings, picked round")
    issues: list[tuple[str, str]] = Field(default_factory=list, description="(kind, target word) per judge issue")
    lib_modules: list[str] = Field(default_factory=list, description="distinct lib modules the agent's files import")
    lib_imports: int = Field(default=0, description="import statements of lib modules in the agent's files")
    cost_usd: float = 0.0
    cost_by_stage: dict[str, float] = Field(default_factory=dict)
    minutes: float | None = None
    skills_listed: int = 0
    skills_surfaced: int = 0
    skills_deep: int = 0
    deep_read_rate: float | None = Field(default=None, description="mean over the rounds that could measure it")
    errors: str = ""

    @property
    def placement_errors(self) -> int:
        return sum(self.gate_errors.get(k, 0) for k in PLACEMENT_KINDS)


def _picked(run_dir: Path, rec: RunRecord) -> RoundRecord | None:
    s = summarise(run_dir, record=rec)
    idx = s.round
    return next((r for r in rec.rounds if r.index == idx), None) if idx is not None else None


def _issue_word(target: str) -> str:
    """``ShojiVeranda/ShojiScreen`` → ``ShojiScreen``; ``BlackPine_5`` → ``BlackPine`` (the family, not the
    instance): the pairs are counted across runs, where instance numbers only scatter them."""
    seg = target.strip().split("/")[-1] or target.strip()
    head, _, tail = seg.rpartition("_")
    return head if head and tail.isdigit() else seg


def py_lib_modules(path: Path) -> list[str]:
    """``lib.<x>`` modules one agent-written python file imports (``ast``, never text)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            parts = node.module.split(".")
            if parts[0] == _LIB_DIR:
                out.extend([parts[1]] if len(parts) > 1 else [a.name for a in node.names])
        elif isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                if parts[0] == _LIB_DIR and len(parts) > 1:
                    out.append(parts[1])
    return out


def js_lib_modules(path: Path) -> list[str]:
    """``lib/<x>.js`` modules one agent-written three.js file imports (relative specifiers)."""
    return _JS_LIB_IMPORT.findall(path.read_text(encoding="utf-8", errors="replace"))


def lib_usage(run_dir: Path) -> tuple[list[str], int]:
    """``(distinct modules, import statements)`` over the agent's files under ``deliverable/src`` (the
    packaged round) — ``src/`` when nothing was packaged.  ``src/lib/`` itself is skipped: the library
    importing itself is not the agent using it."""
    src = run_dir / "deliverable" / "src"
    if not src.is_dir():
        src = run_dir / "src"
    if not src.is_dir():
        return [], 0
    found: list[str] = []
    for p in sorted(src.rglob("*")):
        if not p.is_file() or _LIB_DIR in p.relative_to(src).parts[:-1]:
            continue
        if p.suffix == ".py":
            found.extend(py_lib_modules(p))
        elif p.suffix in (".js", ".mjs"):
            found.extend(js_lib_modules(p))
    return sorted(set(found)), len(found)


def digest_run(run_dir: Path, row: BenchItemResult | None = None) -> RunDigest:
    """The digest of one run directory; ``row`` is its bench row when the battery has one."""
    d = RunDigest(id=row.id if row is not None else run_dir.name)
    if row is not None:
        d.status, d.errors = row.status, row.errors
        d.score_baseline, d.score_picked, d.picked_round = row.score_baseline, row.score_picked, row.picked_round
        d.cost_usd, d.minutes = row.cost_usd, row.minutes
    try:
        rec = load_record(run_dir)
    except RecordError as e:
        d.errors = d.errors or str(e)
        return d
    d.stop_reason = stop_reason(rec)
    d.status = d.status or rec.status.value
    d.cost_usd = d.cost_usd or rec.total_usage.cost_usd
    d.minutes = rec.minutes if rec.minutes is not None else d.minutes
    cost = rec.telemetry.cost if rec.telemetry is not None else None
    if cost is not None:
        d.cost_by_stage = {s.stage: round(s.cost_usd, 6) for s in cost.by_stage}
    picked = _picked(run_dir, rec)
    if picked is not None:
        d.picked_round = picked.index
        for g in picked.gates:
            for f in g.findings:
                kind = finding_kind(f)
                if kind is None:
                    continue
                bucket = d.gate_errors if f.severity == Severity.ERROR else d.gate_warns if f.severity == Severity.WARN else None
                if bucket is not None:
                    bucket[kind] = bucket.get(kind, 0) + 1
        j = picked.judgment
        if j is not None and not j.degraded:
            d.score_picked = j.overall if d.score_picked is None else d.score_picked
            d.criteria = dict(j.scores)
            d.issues = [(i.kind, _issue_word(i.target)) for i in j.issues]
            raw = _raw(j.raw)
            d.defects = {k: bool(v) for k, v in (raw.get("defects") or {}).items()}
            d.caps = [str(c.get("rule", "")) for c in (raw.get("caps") or {}).get("caps_applied", []) if c.get("rule")]
    if d.score_baseline is None:
        base = next((r for r in rec.rounds if r.index == 0), None)
        if base is not None and base.judgment is not None and not base.judgment.degraded:
            d.score_baseline = base.judgment.overall
    rates: list[float] = []
    for r in rec.rounds:
        if r.skills is None:
            continue
        d.skills_listed += len(r.skills.listed)
        d.skills_surfaced += len(r.skills.surfaced)
        d.skills_deep += len(r.skills.deep)
        if (rate := r.skills.deep_read_rate) is not None:
            rates.append(rate)
    d.deep_read_rate = round(statistics.fmean(rates), 4) if rates else None
    d.lib_modules, d.lib_imports = lib_usage(run_dir)
    return d


def _raw(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text) if text else {}
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def digests_for(out_lang: Path) -> dict[str, RunDigest]:
    """prompt id → digest for one ``bench.run_bench`` output directory (its rows + its run dirs,
    each run once — ``_records.records``)."""
    rows: dict[str, BenchItemResult] = {}
    jl = out_lang / "results.jsonl"
    if jl.is_file():
        rows = {r.id: r for r in latest(read_jsonl(jl, BenchItemResult)).values()}
    out: dict[str, RunDigest] = {}
    runs_root = out_lang / "runs"
    if runs_root.is_dir():
        for rec_path, data in records(runs_root, track="scene"):
            run_dir = rec_path.parent
            pid = str((data.get("spec") or {}).get("id", run_dir.name)).split("/")[-1]
            # the live run is `runs/<id>`; a redo archives the old tree beside it as `<id>.attemptN`
            # (run_bench.archive_attempt) with the SAME spec id, and sorts after it — never the digest
            if run_dir.name != pid:
                continue
            out[pid] = digest_run(run_dir, rows.get(pid))
    for pid, row in rows.items():  # a row whose run left no record (an error before the plan) still counts
        out.setdefault(pid, digest_run(runs_root / pid, row))
    return out


# --------------------------------------------------------------------------- diagnosis
@dataclass(frozen=True)
class Rule:
    """A deterministic lever: when ``metric`` crosses ``threshold`` (``above`` or below), say ``text``."""

    name: str
    metric: str
    threshold: float
    text: str
    above: bool = True

    def fires(self, value: float | None) -> bool:
        if value is None:
            return False
        return value >= self.threshold if self.above else value < self.threshold


RULES: tuple[Rule, ...] = (
    Rule("floating", "floating_error_rate", 0.25,
         "placement recipe + boost the placement skill: seat every placed thing on height_at / its support "
         "(lib.place.seat); the gate ERRORs at 0.08 m or > 2 floaters per scene"),
    Rule("sunken", "sunken_error_rate", 0.25,
         "seating recipe: assets are buried, not hovering — seat by the evaluated bbox, never by the origin "
         "(sunk ERROR at 0.20 m and 20 % of the height)"),
    Rule("interpenetration", "interpenetration_error_rate", 0.25,
         "clear_of self-check in the zone recipe + spacing in the layout prompt (overlap ERROR at 35 % of the smaller box)"),
    Rule("library", "lib_modules_mean", 3.0,
         "effects catalog presentation: the agent reaches fewer than three library modules per scene", above=False),
    Rule("lighting", "dark_frame_rate", 0.20,
         "lighting recipe: key/fill/exposure numbers per mood + boost the lighting skill (dark_frame caps at 0.55)"),
    Rule("budget", "budget_stop_rate", 0.30,
         "raise --max-minutes or cut the zone prompt: the clock stops the run before its rounds"),
    Rule("weather", "infra_failed_rate", 0.20,
         "provider weather, not the variant: `bench.loop run --resume` the iteration before reading it"),
    Rule("unscored", "unscored_rate", 0.20,
         "runs without a verdict: read the errors column (build / render / judge outage) before the scores"),
    Rule("skills", "deep_read_rate_mean", 0.20,
         "skills are listed but not read: shorten the index, name the chapter in the task text", above=False),
    Rule("must", "missing_must_rate", 0.25,
         "must-have wording: the contract lines fail the plan's acceptance items — the planner is not carrying them"),
    Rule("camera", "camera_error_rate", 0.20,
         "camera brief recipe: fit every authored camera to the content box, never inside geometry"),
    Rule("free_exempt", "free_exempt_rate", 0.20,
         "assets tagged placement='free' to dodge the gate: say in the contract that 'free' is for hanging / airborne things only"),
)


class Recommendation(BaseModel):
    rule: str
    metric: str
    value: float
    threshold: float
    text: str


class Diagnosis(BaseModel):
    """One language of one iteration, diagnosed."""

    language: str
    out_dir: str
    n: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    n_scored: int = 0
    baseline: dict[str, float | None] = Field(default_factory=dict, description="mean / median / sd")
    picked: dict[str, float | None] = Field(default_factory=dict)
    criteria_mean: dict[str, float] = Field(default_factory=dict)
    defect_rates: dict[str, float] = Field(default_factory=dict, description="share of scored runs voting the defect")
    caps_fired: dict[str, int] = Field(default_factory=dict, description="cap rule → runs it fired on")
    gate_error_total: dict[str, int] = Field(default_factory=dict, description="typed kind → ERROR findings over the runs")
    gate_error_runs: dict[str, int] = Field(default_factory=dict, description="typed kind → runs with at least one ERROR")
    gate_warn_total: dict[str, int] = Field(default_factory=dict)
    lib_modules_mean: float | None = None
    lib_module_hist: dict[str, int] = Field(default_factory=dict, description="module → runs importing it")
    cost_total: float = 0.0
    cost_by_stage: dict[str, float] = Field(default_factory=dict)
    minutes_mean: float | None = None
    minutes_total: float = 0.0
    stop_reasons: dict[str, int] = Field(default_factory=dict)
    skills: dict[str, float | int | None] = Field(default_factory=dict)
    top_issues: list[tuple[str, str, int]] = Field(default_factory=list, description="(kind, target word, n), 12 most frequent")
    metrics: dict[str, float | None] = Field(default_factory=dict, description="the rates the rules read")
    recommendations: list[Recommendation] = Field(default_factory=list)
    runs: list[RunDigest] = Field(default_factory=list)


def _stats(xs: Sequence[float]) -> dict[str, float | None]:
    if not xs:
        return {"mean": None, "median": None, "sd": None}
    return {"mean": round(statistics.fmean(xs), 4), "median": round(statistics.median(xs), 4),
            "sd": round(statistics.stdev(xs), 4) if len(xs) > 1 else None}


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 4) if n else None


def diagnose_digests(language: str, out_dir: Path, digests: Iterable[RunDigest]) -> Diagnosis:
    runs = sorted(digests, key=lambda d: d.id)
    diag = Diagnosis(language=language, out_dir=str(out_dir), n=len(runs), runs=runs)
    diag.by_status = dict(Counter(d.status or "(none)" for d in runs))
    evaluated = [d for d in runs if d.status != "infra_failed"]
    scored = [d for d in evaluated if d.score_picked is not None]
    gated = [d for d in evaluated if d.picked_round is not None]
    diag.n_scored = len(scored)
    diag.baseline = _stats([d.score_baseline for d in evaluated if d.score_baseline is not None])
    diag.picked = _stats([d.score_picked for d in scored])
    crit: dict[str, list[float]] = {}
    defects: Counter[str] = Counter()
    caps: Counter[str] = Counter()
    issues: Counter[tuple[str, str]] = Counter()
    for d in scored:
        for k, v in d.criteria.items():
            crit.setdefault(k, []).append(v)
        defects.update(k for k, on in d.defects.items() if on)
        caps.update(set(d.caps))
        issues.update(d.issues)
    diag.criteria_mean = {k: round(statistics.fmean(v), 4) for k, v in sorted(crit.items())}
    diag.defect_rates = {k: _rate(n, len(scored)) or 0.0 for k, n in defects.most_common()}
    diag.caps_fired = dict(caps.most_common())
    diag.top_issues = [(k, w, n) for (k, w), n in issues.most_common(12)]
    err_total: Counter[str] = Counter()
    err_runs: Counter[str] = Counter()
    warn_total: Counter[str] = Counter()
    for d in gated:
        err_total.update(d.gate_errors)
        err_runs.update(d.gate_errors.keys())
        warn_total.update(d.gate_warns)
    diag.gate_error_total, diag.gate_error_runs = dict(err_total.most_common()), dict(err_runs.most_common())
    diag.gate_warn_total = dict(warn_total.most_common())
    with_src = [d for d in evaluated if d.lib_modules or d.lib_imports]
    diag.lib_modules_mean = round(statistics.fmean(len(d.lib_modules) for d in gated), 3) if gated else None
    diag.lib_module_hist = dict(Counter(m for d in with_src for m in d.lib_modules).most_common())
    diag.cost_total = round(sum(d.cost_usd for d in runs), 4)
    stage: Counter[str] = Counter()
    for d in runs:
        stage.update(d.cost_by_stage)
    diag.cost_by_stage = {k: round(v, 4) for k, v in stage.most_common()}
    minutes = [d.minutes for d in evaluated if d.minutes is not None]
    diag.minutes_mean = round(statistics.fmean(minutes), 2) if minutes else None
    diag.minutes_total = round(sum(minutes), 2)
    diag.stop_reasons = dict(Counter(d.stop_reason or "(none)" for d in evaluated).most_common())
    rates = [d.deep_read_rate for d in runs if d.deep_read_rate is not None]
    diag.skills = {"listed": sum(d.skills_listed for d in runs), "surfaced": sum(d.skills_surfaced for d in runs),
                   "deep": sum(d.skills_deep for d in runs),
                   "deep_read_rate_mean": round(statistics.fmean(rates), 4) if rates else None}
    n_eval, n_gated = len(evaluated), len(gated)
    diag.metrics = {
        "floating_error_rate": _rate(err_runs["scene_placement/floating"], n_gated),
        "sunken_error_rate": _rate(err_runs["scene_placement/sunken"], n_gated),
        "interpenetration_error_rate": _rate(err_runs["scene_placement/interpenetration"], n_gated),
        "free_exempt_rate": _rate(sum(1 for d in gated if "scene_placement/free_exempt" in d.gate_errors
                                      or "scene_placement/free_exempt" in d.gate_warns), n_gated),
        "dark_frame_rate": _rate(err_runs["scene_frames/dark_frame"], n_gated),
        "camera_error_rate": _rate(sum(1 for d in gated if any(k.startswith("scene_frames/camera_") for k in d.gate_errors)), n_gated),
        "lib_modules_mean": diag.lib_modules_mean,
        "budget_stop_rate": _rate(sum(1 for d in evaluated if d.stop_reason == "budget"), n_eval),
        "infra_failed_rate": _rate(len(runs) - n_eval, len(runs)),
        "unscored_rate": _rate(n_eval - len(scored), n_eval),
        "deep_read_rate_mean": diag.skills["deep_read_rate_mean"],
        "missing_must_rate": _rate(caps["missing_must_acceptance"], len(scored)),
    }
    diag.recommendations = [
        Recommendation(rule=r.name, metric=r.metric, value=v, threshold=r.threshold, text=r.text)
        for r in RULES if (v := diag.metrics.get(r.metric)) is not None and r.fires(v)
    ]
    return diag


def diagnose_dir(language: str, out_lang: Path) -> Diagnosis:
    """The diagnosis of one ``bench.run_bench`` output directory."""
    return diagnose_digests(language, out_lang, digests_for(out_lang).values())


def _f(x: float | None, fmt: str = ".3f") -> str:
    return "—" if x is None else format(x, fmt)


def _kv_table(title: str, rows: Iterable[tuple[str, Any]], head: tuple[str, str]) -> list[str]:
    out = [f"### {title}", "", f"| {head[0]} | {head[1]} |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in rows]
    return out + [""]


def render_diagnosis(d: Diagnosis) -> str:
    lines = [f"## {d.language} — {d.n} run(s) under `{d.out_dir}`", "",
             "by status: " + ", ".join(f"{k} {n}" for k, n in sorted(d.by_status.items())) + ";  "
             f"scored {d.n_scored};  stop reasons: " + ", ".join(f"{k} {n}" for k, n in d.stop_reasons.items()), "",
             "| score | mean | median | sd |", "|---|---|---|---|",
             f"| baseline | {_f(d.baseline.get('mean'))} | {_f(d.baseline.get('median'))} | {_f(d.baseline.get('sd'))} |",
             f"| picked | {_f(d.picked.get('mean'))} | {_f(d.picked.get('median'))} | {_f(d.picked.get('sd'))} |", ""]
    lines += _kv_table("criteria (mean over scored runs)", ((k, _f(v)) for k, v in d.criteria_mean.items()), ("criterion", "mean"))
    lines += _kv_table("defects (vote rate over scored runs)", ((k, format(v, ".0%")) for k, v in d.defect_rates.items()), ("defect", "rate"))
    lines += _kv_table("caps fired (runs)", d.caps_fired.items(), ("rule", "runs"))
    lines += ["### gates (picked round, typed kind)", "", "| kind | runs with ERROR | ERROR findings | WARN findings |", "|---|---|---|---|"]
    kinds = sorted(set(d.gate_error_total) | set(d.gate_warn_total))
    lines += [f"| {k} | {d.gate_error_runs.get(k, 0)} | {d.gate_error_total.get(k, 0)} | {d.gate_warn_total.get(k, 0)} |" for k in kinds]
    lines += ["", f"### library: {_f(d.lib_modules_mean, '.2f')} modules per scene", ""]
    lines += [f"- {m}: {n}" for m, n in list(d.lib_module_hist.items())[:20]] or ["- (no lib imports found)"]
    lines += ["", f"### cost ${d.cost_total:.2f} total; minutes mean {_f(d.minutes_mean, '.1f')}, total {d.minutes_total:.0f}", ""]
    lines += [f"- {k}: ${v:.2f}" for k, v in d.cost_by_stage.items()]
    s = d.skills
    lines += ["", f"### skills: listed {s.get('listed')}, surfaced {s.get('surfaced')}, deep {s.get('deep')}, "
                  f"deep-read rate {_f(s.get('deep_read_rate_mean'))}", ""]
    lines += ["### judge issues (kind, target word)", ""] + [f"- {n:3}× {k} / {w}" for k, w, n in d.top_issues] + [""]
    lines += ["### RECOMMENDATIONS", ""]
    if d.recommendations:
        lines += [f"- **{r.rule}** ({r.metric} = {r.value:.3g} vs {r.threshold:g}): {r.text}" for r in d.recommendations]
    else:
        lines += ["- (no rule fired)"]
    lines += ["", "| metric | value |", "|---|---|"] + [f"| {k} | {_f(v)} |" for k, v in d.metrics.items()] + [""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- compare
class Comparison(BaseModel):
    """Iteration ``a`` against ``b`` for one language: the score pairs plus the deterministic channels."""

    language: str
    name_a: str
    name_b: str
    paired: PairedStats
    dropped_ids: list[str] = Field(default_factory=list, description="ids on one side only")
    criteria_delta: dict[str, float] = Field(default_factory=dict, description="mean a − b per criterion")
    gate_error_delta: dict[str, int] = Field(default_factory=dict, description="kind → Σ(a − b) ERROR findings over the pairs")
    placement_rise: float | None = Field(default=None, description="mean a − b of the floating+sunken+interpenetration ERRORs per run")
    cost_delta: float | None = Field(default=None, description="mean a − b, USD per run")
    decision: str = Field(default="", description="keep | revert | inconclusive")
    reason: str = ""


def loop_verdict(st: PairedStats, placement_rise: float | None) -> tuple[str, str]:
    """``(decision, reason)``: ``stats.decide`` on the paired picked scores, then the gate veto."""
    d = decide(st.deltas)
    decision = {"better": "keep", "worse": "revert"}.get(d.outcome, "inconclusive")
    reason = d.reason
    if placement_rise is not None and placement_rise > VETO_RISE:
        decision = "revert"
        reason = (f"gate veto: floating+sunken+interpenetration ERRORs rose by {placement_rise:+.2f} per run "
                  f"(limit {VETO_RISE:+.0f}); score said {d.outcome} — {d.reason}")
    return decision, reason


def compare_digests(language: str, name_a: str, name_b: str, st: PairedStats,
                    da: dict[str, RunDigest], db: dict[str, RunDigest]) -> Comparison:
    cmp = Comparison(language=language, name_a=name_a, name_b=name_b, paired=st,
                     dropped_ids=sorted(set(da) ^ set(db)))
    ids = [i for i in st.deltas if i in da and i in db]
    crit: dict[str, list[float]] = {}
    gates: Counter[str] = Counter()
    rises: list[float] = []
    costs: list[float] = []
    for i in ids:
        a, b = da[i], db[i]
        for k in set(a.criteria) & set(b.criteria):
            crit.setdefault(k, []).append(a.criteria[k] - b.criteria[k])
        for k in set(a.gate_errors) | set(b.gate_errors):
            gates[k] += a.gate_errors.get(k, 0) - b.gate_errors.get(k, 0)
        rises.append(a.placement_errors - b.placement_errors)
        costs.append(a.cost_usd - b.cost_usd)
    cmp.criteria_delta = {k: round(statistics.fmean(v), 4) for k, v in sorted(crit.items())}
    cmp.gate_error_delta = {k: v for k, v in sorted(gates.items()) if v}
    cmp.placement_rise = round(statistics.fmean(rises), 4) if rises else None
    cmp.cost_delta = round(statistics.fmean(costs), 4) if costs else None
    cmp.decision, cmp.reason = loop_verdict(st, cmp.placement_rise)
    return cmp


def compare_dirs(language: str, dir_a: Path, dir_b: Path) -> Comparison:
    """Iteration directory ``dir_a`` (the candidate) against ``dir_b`` (the reference), one language."""
    cells, name_a, name_b = paired_rows(dir_a, dir_b)
    st = paired(cells, name_a, name_b)
    return compare_digests(language, name_a, name_b, st, digests_for(dir_a), digests_for(dir_b))


def render_comparison(c: Comparison) -> str:
    st = c.paired
    ci = f"[{_f(st.ci95_low, '+.3f')}, {_f(st.ci95_high, '+.3f')}]" if st.ci95_low is not None else "—"
    lines = [f"## {c.language}: {c.name_a} − {c.name_b}", "",
             f"**{c.decision}** — {c.reason}", "",
             f"pairs {st.n} (dropped: outage {st.dropped_infra}, unscored {st.dropped_unscored}, "
             f"one side only {len(c.dropped_ids)}); mean picked {_f(st.mean_harness)} vs {_f(st.mean_oneshot)}; "
             f"Δ {_f(st.mean_delta, '+.3f')} 95 % t-interval {ci}; W/L/T {st.wins}/{st.losses}/{st.ties}, "
             f"sign p {_f(st.sign_p)}; cost Δ {_f(c.cost_delta, '+.2f')} $/run; "
             f"placement ERRORs Δ {_f(c.placement_rise, '+.2f')} per run (veto above {VETO_RISE:+.0f})", ""]
    if c.dropped_ids:
        lines += ["dropped ids: " + ", ".join(c.dropped_ids), ""]
    lines += _kv_table("Δ per criterion (mean a − b)", ((k, _f(v, "+.3f")) for k, v in c.criteria_delta.items()), ("criterion", "Δ"))
    lines += _kv_table("Δ gate ERROR findings (Σ a − b over the pairs)", ((k, f"{v:+d}") for k, v in c.gate_error_delta.items()), ("kind", "Δ"))
    lines += ["| id | Δ picked |", "|---|---|"] + [f"| {k} | {v:+.3f} |" for k, v in sorted(st.deltas.items())] + [""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- judge calibration
class CalibSummary(BaseModel):
    """The judge arm of one iteration: is the re-judge noise small against the spread between runs?"""

    language: str
    model_id: str
    n_samples: int
    thinking: str
    rounds: int = 0
    errors: int = 0
    within_sd: float | None = Field(default=None, description="rms of the per-round sample σ: the re-judge noise")
    between_sd: float | None = Field(default=None, description="σ of the per-round means: the spread to resolve")
    snr: float | None = Field(default=None, description="between_sd / within_sd")
    pearson_errors_vs_score: float | None = None
    spearman_errors_vs_score: float | None = None
    pearson_stored_vs_new: float | None = Field(default=None, description="the in-run verdict against the re-judge")
    mean_new: float | None = None
    mean_stored: float | None = None
    defects: dict[str, int] = Field(default_factory=dict)
    caps: dict[str, int] = Field(default_factory=dict)
    cost_usd: float = 0.0
    latency_s_mean: float | None = None


def calib_summary(language: str, tables: Sequence[CalibrationTable], *, thinking: str) -> CalibSummary:
    rows: list[CalibrationRow] = [r for t in tables for r in t.rows]
    ok = [r for r in rows if not r.error]
    model_id = tables[0].model_id if tables else ""
    n_samples = tables[0].n_samples if tables else 0
    s = CalibSummary(language=language, model_id=model_id, n_samples=n_samples, thinking=thinking,
                     rounds=len(rows), errors=len(rows) - len(ok))
    if not ok:
        return s
    s.within_sd = round(math.sqrt(statistics.fmean(r.std ** 2 for r in ok)), 4)
    means = [r.mean for r in ok]
    s.between_sd = round(statistics.stdev(means), 4) if len(means) > 1 else None
    s.snr = round(s.between_sd / s.within_sd, 3) if s.between_sd is not None and s.within_sd else None
    gate = [float(r.gate_errors) for r in ok]
    s.pearson_errors_vs_score = _r(correlation(gate, means))
    s.spearman_errors_vs_score = _r(correlation(gate, means, ranked=True))
    stored = [(r.stored_overall, r.mean) for r in ok if r.stored_overall is not None]
    s.pearson_stored_vs_new = _r(correlation([a for a, _ in stored], [b for _, b in stored]))
    s.mean_new = round(statistics.fmean(means), 4)
    s.mean_stored = round(statistics.fmean(a for a, _ in stored), 4) if stored else None
    s.defects = dict(Counter(d for r in ok for d in r.defects_present).most_common())
    s.caps = dict(Counter(c for r in ok for c in r.caps).most_common())
    s.cost_usd = round(sum(r.cost_usd for r in rows), 4)
    s.latency_s_mean = round(statistics.fmean(r.duration_s for r in ok), 1)
    return s


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


def render_calib(s: CalibSummary) -> str:
    lines = [f"## {s.language}: judge {s.model_id}, n_samples {s.n_samples}, thinking {s.thinking}", "",
             f"rounds {s.rounds} (errors {s.errors}); within-sd {_f(s.within_sd)} (re-judge noise), "
             f"between-sd {_f(s.between_sd)}, SNR {_f(s.snr, '.2f')}; mean new {_f(s.mean_new)} vs stored {_f(s.mean_stored)}; "
             f"pearson(gate ERRORs, score) {_f(s.pearson_errors_vs_score, '+.3f')}, spearman {_f(s.spearman_errors_vs_score, '+.3f')}, "
             f"pearson(stored, new) {_f(s.pearson_stored_vs_new, '+.3f')}; cost ${s.cost_usd:.2f}, "
             f"latency {_f(s.latency_s_mean, '.0f')} s/round", ""]
    lines += _kv_table("defects (rounds)", s.defects.items(), ("defect", "rounds"))
    lines += _kv_table("caps (rounds)", s.caps.items(), ("rule", "rounds"))
    return "\n".join(lines)


__all__ = ["PLACEMENT_KINDS", "RULES", "VETO_RISE", "CalibSummary", "Comparison", "Diagnosis", "Recommendation",
           "Rule", "RunDigest", "calib_summary", "compare_digests", "compare_dirs", "diagnose_digests",
           "diagnose_dir", "digest_run", "digests_for", "js_lib_modules", "lib_usage", "loop_verdict",
           "py_lib_modules", "render_calib", "render_comparison", "render_diagnosis"]
