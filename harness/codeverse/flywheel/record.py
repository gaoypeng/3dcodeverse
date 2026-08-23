"""Run records: finalise / load / iterate.

``finalize_record`` is the last step of every track run: it fills the
environment (tool versions + harness git sha), derives best/baseline/final
scores and totals when the track left them empty, adds a compact per-round
summary under ``record.extra["rounds_summary"]`` and writes ``record.json``
atomically.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from codeverse import __version__
from codeverse.config import get_settings
from codeverse.contracts.common import Usage
from codeverse.contracts.run import RoundRecord, RunRecord
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

_VERSION_TIMEOUT_S = 30


class RecordError(RuntimeError):
    """A record.json is missing or invalid."""


# --------------------------------------------------------------------------- environment
def _cmd_first_line(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_VERSION_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"error: {type(e).__name__}"
    out = (proc.stdout or proc.stderr).strip().splitlines()
    return out[0].strip() if out else f"exit {proc.returncode}"


def _harness_git_sha() -> str:
    repo = Path(__file__).resolve().parents[2]
    if not (repo / ".git").exists():
        return ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _three_version() -> str:
    pkg = get_settings().runtime_js_dir() / "node_modules" / "three" / "package.json"
    if not pkg.is_file():
        return ""
    try:
        return str(json.loads(pkg.read_text()).get("version", ""))
    except (OSError, ValueError):
        return ""


@lru_cache(maxsize=1)
def environment_versions() -> dict[str, str]:
    """Tool versions + harness provenance.  Cached per process (Blender is slow to ask)."""
    s = get_settings()
    env: dict[str, str] = {
        "python": platform.python_version(),
        "codeverse": __version__,
        "platform": platform.platform(),
        "host": platform.node(),
        "harness_git_sha": _harness_git_sha(),
    }
    blender = s.resolve_blender()
    env["blender"] = _cmd_first_line([blender, "--version"]) if blender else ""
    env["blender_path"] = blender
    env["node"] = _cmd_first_line([s.binaries.node, "--version"])
    env["three"] = _three_version()
    return env


# --------------------------------------------------------------------------- derived fields
def effective_judgment(r: RoundRecord):
    """The round's judgment, or ``None`` when it is a degraded (judge-outage)
    verdict — the flywheel must treat those as 'no score', never as a 0.0."""
    j = r.judgment
    if j is None:
        return None
    from codeverse.judges.scoring import is_degraded

    return None if is_degraded(j) else j


def effective_score(r: RoundRecord) -> float | None:
    """``r.score`` with degraded judgments filtered out (None = unscored)."""
    j = effective_judgment(r)
    return j.overall if j is not None else None


def best_round_index(record: RunRecord) -> int | None:
    """The round to export: ``record.best_round`` when valid, else the highest
    judged score (ties → fewer gate errors), else the last round with a
    successful build, else the last round.  Degraded judgments count as unjudged."""
    by_index = {r.index: r for r in record.rounds}
    if record.best_round is not None and record.best_round in by_index:
        return record.best_round
    if not record.rounds:
        return None
    judged = [r for r in record.rounds if effective_judgment(r) is not None]
    if judged:
        best = max(judged, key=lambda r: (effective_score(r), -_n_gate_errors(r), r.index))  # type: ignore[arg-type]
        return best.index
    built = [r for r in record.rounds if r.build is not None and r.build.ok]
    if built:
        return built[-1].index
    return record.rounds[-1].index


def _n_gate_errors(r: RoundRecord) -> int:
    return sum(len(g.errors) for g in r.gates)


def round_summary(r: RoundRecord) -> dict[str, Any]:
    """Compact per-round digest (for record.extra, dashboards and the CLI).

    A degraded judgment shows up as score/passed None + ``judge_degraded``."""
    j = effective_judgment(r)
    return {
        "index": r.index,
        "kind": r.kind,
        "commit": r.commit[:12],
        "agent_backend": r.agent_backend,
        "build_ok": None if r.build is None else r.build.ok,
        "gate_errors": _n_gate_errors(r),
        "score": j.overall if j else None,
        "passed": j.passed if j else None,
        "issues": len(j.issues) if j else 0,
        "judge_degraded": r.judgment is not None and j is None,
        "cost_usd": round(r.usage.cost_usd, 6),
        "duration_s": round(r.duration_s, 1),
    }


def _sum_usage(rounds: list[RoundRecord]) -> Usage:
    """Round usage already includes the round's judge call (tracks/steps.py) — do not add it twice."""
    total = Usage()
    for r in rounds:
        total = total + r.usage
    return total


def fill_derived(record: RunRecord) -> RunRecord:
    """Fill best/baseline/final/total_usage/finished_at/environment when absent."""
    if record.best_round is None:
        record.best_round = best_round_index(record)
    scored = [r for r in record.rounds if effective_score(r) is not None]
    if record.baseline_score is None and scored:
        record.baseline_score = effective_score(scored[0])
    if record.final_score is None and record.best_round is not None:
        best = next((r for r in record.rounds if r.index == record.best_round), None)
        record.final_score = effective_score(best) if best else None
    if record.total_usage.cost_usd == 0 and record.total_usage.input_tokens == 0 and record.rounds:
        record.total_usage = _sum_usage(record.rounds)
    if record.finished_at is None:
        record.finished_at = datetime.now(UTC)
    env = dict(environment_versions())
    env.update(record.environment)  # the track's own entries win
    record.environment = env
    record.extra["rounds_summary"] = [round_summary(r) for r in record.rounds]
    return record


# --------------------------------------------------------------------------- io
def finalize_record(ws: Workspace, record: RunRecord) -> Path:
    """Fill derived fields + environment and write ``record.json`` atomically."""
    record.workspace = record.workspace or str(ws.root)
    fill_derived(record)
    ws.write_json(ws.record_path, record)
    return ws.record_path


def load_record(ws: Workspace | Path | str) -> RunRecord:
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    if not ws.record_path.is_file():
        raise RecordError(f"no record.json in {ws.root}")
    try:
        return RunRecord.model_validate_json(ws.record_path.read_text())
    except Exception as e:  # pydantic ValidationError / JSON error
        raise RecordError(f"invalid record.json in {ws.root}: {e}") from e


def iter_runs(
    runs_dir: Path | str, *, on_error: Callable[[Path, Exception], None] | None = None
) -> Iterator[tuple[Workspace, RunRecord]]:
    """Yield ``(Workspace, RunRecord)`` for every run directory holding a record.json.

    Invalid records raise ``RecordError`` unless ``on_error`` is given, in which
    case it is called and iteration continues (exporters collect skip reasons).
    """
    root = Path(runs_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"runs dir not found: {root}")
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "record.json").is_file():
            continue
        ws = Workspace(d)
        try:
            rec = load_record(ws)
        except RecordError as e:
            if on_error is None:
                raise
            on_error(d, e)
            continue
        yield ws, rec
