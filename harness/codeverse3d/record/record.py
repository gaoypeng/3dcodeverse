"""Run records: finalise / load / iterate.

``finalize_record`` is the last step of every track run: it fills the
environment (tool versions + harness git sha), reads the run's total off its cost ledger
(``telemetry/cost.jsonl``, the one record of money), writes ``telemetry/`` (see
docs/RUN_LAYOUT.md) and writes ``record.json`` atomically.  ``deliverable/`` is not
the run's: which round to hand over is ``codeverse3d.addons.select``'s question
(2026-09-22).  Telemetry is best-effort: a run is never failed by it.
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
from typing import Any, NamedTuple

from codeverse3d import __version__
from codeverse3d.config import get_settings
from codeverse3d.contracts.run import RoundRecord, RunId, RunRecord
from codeverse3d.cost.ledger import existing_ledger_path, ledger_usage, load_ledger
from codeverse3d.proc import version_line
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

_VERSION_TIMEOUT_S = 30


class RecordError(RuntimeError):
    """A record.json is missing or invalid."""


# --------------------------------------------------------------------------- environment
def _cmd_first_line(cmd: list[str]) -> str:
    rc, line = version_line(cmd, timeout=_VERSION_TIMEOUT_S)
    return line or f"exit {rc}"


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
    pkg_root = here.parents[2] if here.parent.name == "record" else here.parent
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
    try:
        pkg = get_settings().runtime_js_dir() / "node_modules" / name / "package.json"
    except RuntimeError:  # no runtime_js: a provenance field, never the end of a finished run
        return ""
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
        "codeverse3d": __version__,
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
    return None if j is None or j.degraded else j


def effective_score(r: RoundRecord) -> float | None:
    """``r.score`` with degraded judgments filtered out (None = unscored)."""
    j = effective_judgment(r)
    return j.overall if j is not None else None


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
    """Compact per-round digest (the dataset sample's ``rounds_summary``).

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
        "minutes": round(r.minutes, 2),
        "complexity": cx.get("index") if cx else None,
    }


def fill_derived(record: RunRecord) -> RunRecord:
    """Fill finished_at/environment when absent and the complexity block.  No best round,
    baseline or final score: which round counts is ``addons.select``'s question (2026-09-22)."""
    if record.finished_at is None:
        record.finished_at = datetime.now(UTC)
    env = dict(environment_versions())
    env.update(record.environment)  # the track's own entries win
    record.environment = env
    block = complexity_block(record)
    if block is not None:
        record.extra["complexity"] = block
    return record


def complexity_block(record: RunRecord) -> dict[str, Any] | None:
    """``record.extra["complexity"]``: the complexity vector of the last measured round —
    the build ``src/`` and ``artifacts/`` end at — plus what the plan asked for and the
    per-round trail.  ``plan_parts`` / ``parts_per_plan_part`` say whether the build
    reached the plan's ambition or collapsed it (eval/docs/COMPLEXITY.md).  A reader that
    wants the picked round's vector reads that round's own (:func:`round_complexity`)."""
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
    """Read the run's total off its ledger, materialise ``telemetry/`` and mirror it onto the
    record.  ``record.total_usage`` is the sum of ``telemetry/cost.jsonl`` at list price — every
    billed row once: the planner, an aborted round, a retried session, a round-trip the
    provider billed and the call discarded, and post-run work (a pick's texture pass) once it
    joined the ledger.  A run with no ledger keeps what it has.

    Best-effort by contract: a failure is logged and the run still gets its
    ``record.json`` (the old layout is always enough to read a run)."""
    from codeverse3d.record.telemetry import build_telemetry

    if existing_ledger_path(ws.root) is not None:
        record.total_usage = ledger_usage(load_ledger(ws.root))
    try:
        ws.ensure_layout()
    except OSError as e:  # read-only mount, exotic filesystem
        log.warning("run layout not created in %s: %s", ws.root, e)
        return
    try:
        record.telemetry = build_telemetry(ws, record)
    except Exception as e:  # noqa: BLE001 - never fail a finished run over accounting
        log.warning("telemetry not written for %s: %s", ws.root, e)


def finalize_record(ws: Workspace, record: RunRecord) -> Path:
    """Fill derived fields + environment, write ``telemetry/`` and ``record.json``."""
    record.workspace = record.workspace or str(ws.root)
    fill_derived(record)
    package_run(ws, record)
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
#: run-layout directories that hold a SUB-workspace (a scene asset candidate, a rejected
#: best-of-N candidate): their files belong to that sub-run, not to the run above them.
#: ``addons.costreport.audit.find_runs`` and the bench survey scripts skip them by this one name.
SUBRUN_DIRS: frozenset[str] = frozenset({"_cand", "_assets"})


def unique_files(root: Path | str, name: str) -> list[Path]:
    """Every file called ``name`` under ``root``, once per file on disk.

    Battery trees symlink each other's cells and every run has
    ``run/telemetry/trajectories -> run/trajectories``, so a walk must say what it does
    about symlinks: this one follows them (``recurse_symlinks=True``) and collapses the
    two paths of one file by its resolved path.  Sub-workspaces (:data:`SUBRUN_DIRS`) are
    skipped.  The one walker for every survey that counts sessions, records or artefacts —
    each hand-rolled copy of it has at some point counted a file twice."""
    seen: dict[Path, Path] = {}
    root = Path(root)
    for p in sorted(root.rglob(name, recurse_symlinks=True)):
        if SUBRUN_DIRS & set(p.relative_to(root).parts):
            continue
        seen.setdefault(p.resolve(), p)
    return sorted(seen.values())


def is_run_dir(p: Path) -> bool:
    return p.is_dir() and (p / "record.json").is_file()


def battery_label(root: Path | str) -> str:
    """Label for a scan root: the battery name for ``eval/bench/out/<battery>/runs``,
    else the directory's own name (the same rule the gallery sections use)."""
    root = Path(root)
    if root.name == "runs" and root.parent.name and root.parent.parent.name == "out":
        return root.parent.name
    return root.name or str(root)


def run_id_for(root: Path | str, run_dir: Path) -> RunId:
    """The :class:`~codeverse3d.contracts.run.RunId` of ``run_dir`` as found under scan
    root ``root``.  Identity is minted HERE, at discovery time — the Workspace
    resolves its root and forgets where the scan started, so it cannot do this."""
    return RunId(battery=battery_label(root), rel=run_dir.relative_to(Path(root)).as_posix())


class FoundRun(NamedTuple):
    """One discovered run: workspace + parsed record + identity within the scan root."""

    ws: Workspace
    record: RunRecord
    run_id: RunId


def find_run_dirs(root: Path | str, *, predicate: Callable[[Path], bool] = is_run_dir,
                  max_depth: int = RUN_SEARCH_DEPTH) -> list[Path]:
    """Run directories under ``root``, sorted.

    Direct children win when there are any — that is the ``3dcode`` runs root and the
    run_bench ``runs/`` layout, and it keeps the common case a single ``iterdir()``.
    Only when there are none do we descend, which is what makes a compare_backends or
    ab_plan BATTERY directory work: those hold their runs four and five levels down, so
    every exporter used to scan one level, find nothing and report "0 runs" as a success.
    A directory that IS a run is never descended into (a run holds candidate workspaces
    of its own).  A run reached through more than one path (``eval/bench/out``'s batteries
    symlink each other's cells) is listed once, at the path it physically lives at.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    direct = [d for d in root.iterdir() if predicate(d)]
    if direct:
        return _once(root, direct)
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
    return _once(root, found)


def _once(root: Path, dirs: list[Path]) -> list[Path]:
    """``dirs`` sorted, one path per directory on disk: the physical path beats an alias."""
    real = root.resolve()
    best: dict[Path, Path] = {}
    for d in sorted(dirs):
        physical = d.resolve()
        if physical not in best or physical == real / d.relative_to(root):
            best[physical] = d
    return sorted(best.values())


def skip_unreadable(run_dir: Path, error: Exception) -> None:
    """``iter_runs(on_error=...)`` for a reader that indexes what it can: one unreadable
    record.json is a warning, not the end of the whole pass."""
    log.warning("skipping %s: %s", run_dir, error)


def iter_runs(
    runs_dir: Path | str, *, on_error: Callable[[Path, Exception], None] | None = None
) -> Iterator[FoundRun]:
    """Yield a :class:`FoundRun` ``(ws, record, run_id)`` for every run directory
    holding a record.json.

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
        yield FoundRun(ws, rec, run_id_for(root, d))
