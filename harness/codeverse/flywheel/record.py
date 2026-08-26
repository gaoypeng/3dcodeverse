"""Run records: finalise / load / iterate.

``finalize_record`` is the last step of every track run: it fills the
environment (tool versions + harness git sha), derives best/baseline/final
scores and totals when the track left them empty, adds a compact per-round
summary under ``record.extra["rounds_summary"]``, packages the run
(``deliverable/`` + ``telemetry/``, see docs/RUN_LAYOUT.md) and writes
``record.json`` atomically.  Packaging is best-effort: a run is never failed
by it.
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
from codeverse.flywheel.code_quality import code_quality_block
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


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _harness_git_sha(pkg_file: Path | None = None) -> str:
    """Commit of the harness checkout this package runs from (``""`` outside a checkout).

    The repository root is wherever git finds it — ``harness/`` is a subdirectory of
    the 3dcodeverse repo, so ``.git`` lives one level ABOVE the package (the old
    ``parents[2] / ".git"`` probe never matched and every record shipped an empty sha).
    Two guards keep a wheel or a venv copy from borrowing an unrelated repo's sha:
    the file itself must be tracked by the repo git resolves to, and a modified
    working tree is marked ``-dirty`` so a score is never attributed to a clean commit
    it did not run on.
    """
    here = (pkg_file or Path(__file__)).resolve()
    tracked = _git(["ls-files", "--error-unmatch", here.name], here.parent)
    if tracked is None or tracked.returncode != 0:
        return ""
    head = _git(["rev-parse", "HEAD"], here.parent)
    if head is None or head.returncode != 0 or not head.stdout.strip():
        return ""
    sha = head.stdout.strip()
    # dirty = any tracked file under the package tree (harness/) modified; untracked files ignored
    pkg_root = here.parents[1] if here.parent.name == "flywheel" else here.parent
    status = _git(["status", "--porcelain", "--untracked-files=no", "--", str(pkg_root)], here.parent)
    if status is not None and status.returncode == 0 and status.stdout.strip():
        sha += "-dirty"
    return sha


def _chrome_version() -> str:
    """Chrome build from the puppeteer cache directory name (offline, no launch)."""
    cache = Path.home() / ".cache" / "puppeteer" / "chrome"
    if not cache.is_dir():
        return ""
    builds = sorted(p.name for p in cache.iterdir() if p.is_dir())
    return builds[-1] if builds else ""


def _node_pkg_version(name: str) -> str:
    pkg = get_settings().runtime_js_dir() / "node_modules" / name / "package.json"
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
    env["three"] = _node_pkg_version("three")
    env["puppeteer"] = _node_pkg_version("puppeteer")
    env["chrome"] = _chrome_version()
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


def round_complexity(r: RoundRecord) -> dict[str, Any] | None:
    """The round's objective complexity vector, as measured on its own build.

    ``spatial/measure.py`` stores it in ``Measurement.extra["complexity"]``; a
    round measured before that existed (or one whose build failed) has none."""
    m = r.measurement
    block = (m.extra.get("complexity") if m is not None else None)
    return block if isinstance(block, dict) else None


def round_summary(r: RoundRecord) -> dict[str, Any]:
    """Compact per-round digest (for record.extra, dashboards and the CLI).

    A degraded judgment shows up as score/passed None + ``judge_degraded``."""
    j = effective_judgment(r)
    cx = round_complexity(r)
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
        "complexity": cx.get("index") if cx else None,
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
    block = complexity_block(record)
    if block is not None:
        record.extra["complexity"] = block
    return record


def complexity_block(record: RunRecord) -> dict[str, Any] | None:
    """``record.extra["complexity"]``: the delivered artifact's complexity vector
    plus what the plan asked for.

    The vector is the one measured on the BEST round — the round whose code is
    restored and rebuilt at finalise, so it describes the artifact actually
    shipped.  ``plan_parts`` / ``parts_per_plan_part`` say whether the build
    reached the plan's ambition or collapsed it (docs/COMPLEXITY.md)."""
    best = next((r for r in record.rounds if r.index == record.best_round), None)
    cx = round_complexity(best) if best is not None else None
    if cx is None:
        cx = next((c for c in (round_complexity(r) for r in reversed(record.rounds)) if c), None)
    if cx is None:
        return None
    plan_parts = len(record.plan.parts) if record.plan is not None else 0
    block = dict(cx)
    block["plan_parts"] = plan_parts
    block["parts_per_plan_part"] = (
        round(int(cx.get("part_count") or 0) / plan_parts, 3) if plan_parts else None
    )
    trail = [c["index"] for c in (round_complexity(r) for r in record.rounds) if c]
    block["by_round"] = trail
    return block


# --------------------------------------------------------------------------- io
def package_run(ws: Workspace, record: RunRecord) -> None:
    """Materialise ``deliverable/`` + ``telemetry/`` and mirror them onto the record.

    Best-effort by contract: a packaging failure is logged and the run still
    gets its ``record.json`` (the old layout is always enough to read a run)."""
    from codeverse.flywheel.deliverable import build_deliverable
    from codeverse.flywheel.telemetry import build_telemetry

    try:
        ws.ensure_layout()
    except OSError as e:  # read-only mount, exotic filesystem
        log.warning("run layout not created in %s: %s", ws.root, e)
        return
    try:
        record.deliverable = build_deliverable(ws, record)
    except Exception as e:  # noqa: BLE001 - never fail a finished run over packaging
        log.warning("deliverable not built for %s: %s", ws.root, e)
    try:
        record.telemetry = build_telemetry(ws, record)
    except Exception as e:  # noqa: BLE001
        log.warning("telemetry not written for %s: %s", ws.root, e)


def finalize_record(ws: Workspace, record: RunRecord, *, package: bool = True) -> Path:
    """Fill derived fields + environment, package the run and write ``record.json``."""
    record.workspace = record.workspace or str(ws.root)
    fill_derived(record)
    if package:
        package_run(ws, record)
    # the delivered CODE's own vector, next to the delivered ARTIFACT's (complexity):
    # the judge scored the picture, the gates the geometry; this scores what the
    # flywheel will actually learn from (flywheel/code_quality.py)
    cq = code_quality_block(ws, record)
    if cq is not None:
        record.extra["code_quality"] = cq
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


# Battery layouts nest their runs at these depths below the battery root: run_bench
# ``runs/<id>`` = 2, compare_backends ``cells/<id>/<arm>/run`` = 4, ab_plan
# ``arms/<arm>/cells/<id>/<slug>/run`` = 6.  Seven covers all three with a margin and
# stops a mistyped root from walking a whole home directory.
RUN_SEARCH_DEPTH = 7

#: subdirectories that mark a bench battery directory rather than a plain runs root
BATTERY_MARKERS = ("runs", "cells", "arms")


def is_run_dir(p: Path) -> bool:
    return p.is_dir() and (p / "record.json").is_file()


def find_run_dirs(root: Path | str, *, predicate: Callable[[Path], bool] = is_run_dir,
                  max_depth: int = RUN_SEARCH_DEPTH) -> list[Path]:
    """Run directories under ``root``, sorted.

    Direct children win when there are any — that is the ``3dcv`` runs root and the
    run_bench ``runs/`` layout, and it keeps the common case a single ``iterdir()``.
    Only when there are none do we descend, which is what makes a compare_backends or
    ab_plan BATTERY directory work: those hold their runs four and five levels down, so
    every exporter used to scan one level, find nothing and report "0 runs" as a success.
    A directory that IS a run is never descended into (a run holds candidate workspaces
    of its own).
    """
    root = Path(root)
    if not root.is_dir():
        return []
    direct = sorted(d for d in root.iterdir() if predicate(d))
    if direct:
        return direct
    found: list[Path] = []
    frontier = [d for d in sorted(root.iterdir()) if d.is_dir()]  # depth 1
    for _ in range(max_depth):
        if not frontier:
            break
        nxt: list[Path] = []
        for d in frontier:
            if predicate(d):
                found.append(d)
                continue  # a run's own subdirectories are not runs
            nxt.extend(c for c in sorted(d.iterdir()) if c.is_dir())
        frontier = nxt
    return sorted(found)


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
    children = find_run_dirs(root)
    battery_dirs = [m for m in BATTERY_MARKERS if (root / m).is_dir()]
    if not children and battery_dirs:
        # Silence must not look like an empty dataset.  This guard used to test only for
        # ``<root>/runs`` — true for the run_bench layout, false for compare_backends
        # (cells/) and ab_plan (arms/), i.e. exactly today's batteries, which therefore
        # reported "0 runs" as a success.  find_run_dirs now FINDS all three layouts, so
        # reaching here means a battery directory that really is empty; an ordinary runs
        # root with no runs in it stays a legitimate empty result.
        raise FileNotFoundError(
            f"no run directories under {root} — it looks like a battery directory "
            f"({', '.join(battery_dirs)}/) but holds no record.json within "
            f"{RUN_SEARCH_DEPTH} levels")
    for d in children:
        ws = Workspace(d)
        try:
            rec = load_record(ws)
        except RecordError as e:
            if on_error is None:
                raise
            on_error(d, e)
            continue
        yield ws, rec
