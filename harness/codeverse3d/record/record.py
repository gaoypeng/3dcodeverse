"""Run records: finalise / load / iterate.

``finalize_record`` fills the environment, reads the run's total off its cost ledger, writes
``record.json`` (telemetry included) atomically.  Telemetry is best-effort and never fails a run.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from codeverse3d import __version__
from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import Judgment
from codeverse3d.contracts.run import RoundRecord, RunId, RunRecord
from codeverse3d.cost.ledger import existing_ledger_path, ledger_usage, load_ledger
from codeverse3d.proc import read_json_or_none, version_line
from codeverse3d.spatial.node import node_modules_dir, puppeteer_cache_dir
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


class RecordError(RuntimeError):
    """A record.json is missing or invalid."""


# --------------------------------------------------------------------------- environment
def _cmd_first_line(cmd: list[str]) -> str:
    rc, line = version_line(cmd)
    return line or f"exit {rc}"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _harness_git_sha(pkg_file: Path | None = None) -> str:
    """Commit of the harness checkout this package runs from (``""`` outside a checkout).

    The file itself must be tracked (a wheel or venv copy must not borrow an unrelated repo's
    sha); a modified tree is marked ``-dirty``.
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
    cache = puppeteer_cache_dir() / "chrome"
    if not cache.is_dir():
        return ""
    builds = sorted(p.name for p in cache.iterdir() if p.is_dir())
    return builds[-1] if builds else ""


def _node_pkg_version(name: str) -> str:
    try:
        pkg = node_modules_dir() / name / "package.json"
    except RuntimeError:  # no runtime_js: provenance only
        return ""
    return str((read_json_or_none(pkg) or {}).get("version", ""))


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
    """The round's judgment, or ``None`` when degraded (never a 0.0 score)."""
    j = r.judgment
    return None if j is None or j.degraded else j


def previous_verdict(rounds: Iterable[RoundRecord | None], index: int) -> Judgment | None:
    """Round ``index - 1``'s effective judgment (None when unjudged), never an older round's."""
    prev = next((r for r in rounds if r is not None and r.index == index - 1), None)
    return effective_judgment(prev) if prev is not None else None


def rubric_of(record: RunRecord) -> str:
    """The run's rubric: the stamped one, else its last judged round's, else ``judges.base.run_rubric``."""
    from codeverse3d.judges.base import run_rubric

    judged = (r.judgment.rubric for r in reversed(record.rounds) if r.judgment is not None and r.judgment.rubric)
    return str(record.extra.get("rubric") or "") or next(judged, "") or run_rubric(record.spec)


def stop_reason(record: RunRecord) -> str:
    """Why the run stopped: the stamped ``stop_reason``, else its status."""
    return str(record.extra.get("stop_reason") or record.status.value)


def effective_score(r: RoundRecord) -> float | None:
    """``r.score`` with degraded judgments filtered out (None = unscored)."""
    j = effective_judgment(r)
    return j.overall if j is not None else None


def _n_gate_errors(r: RoundRecord) -> int:
    return sum(len(g.errors) for g in r.gates)


def round_complexity(r: RoundRecord) -> dict[str, Any] | None:
    """The round's objective complexity vector (``Measurement.extra["complexity"]``), or None."""
    m = r.measurement
    block = (m.extra.get("complexity") if m is not None else None)
    return block if isinstance(block, dict) else None


def round_summary(r: RoundRecord) -> dict[str, Any]:
    """Compact per-round digest; a degraded judgment shows as score/passed None + ``judge_degraded``."""
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
    """Fill finished_at/environment when absent and the complexity block."""
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
    """``record.extra["complexity"]``: the last measured round's vector, plus plan parts and the per-round trail."""
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
    """Read the run's total off its ledger (``record.total_usage`` = its sum at list price).

    Best-effort: a failure is logged and the run still gets its ``record.json``."""
    from codeverse3d.record.telemetry import build_telemetry

    if existing_ledger_path(ws.root) is not None:
        record.total_usage = ledger_usage(load_ledger(ws.root))
    try:
        ws.ensure_layout()
    except OSError as e:  # read-only mount
        log.warning("run layout not created in %s: %s", ws.root, e)
        return
    try:
        record.telemetry = build_telemetry(ws, record)
    except Exception as e:  # noqa: BLE001 - never fail a finished run over accounting
        log.warning("telemetry not written for %s: %s", ws.root, e)


def repackage(ws: Workspace) -> RunRecord | None:
    """Re-package a finished run's ``record.json`` after its ledger grew; None when there is no ledger or record."""
    if existing_ledger_path(ws.root) is None:
        return None
    try:
        record = load_record(ws)
    except RecordError:
        return None
    package_run(ws, record)
    ws.write_json(ws.record_path, record)
    return record


@contextmanager
def join_post_run(ws: Workspace) -> Iterator[None]:
    """Work on a finished run that may spend money: rows join its ledger, and the record is
    re-packaged on the way out (even on error) so ``total_usage`` stays the ledger's sum."""
    from codeverse3d.cost.instrument import run_ledger

    try:
        with run_ledger(ws.root, run=ws.root.name, create=False):
            yield
    finally:
        repackage(ws)


def finalize_record(ws: Workspace, record: RunRecord) -> Path:
    """Fill derived fields + environment, build the telemetry block and write ``record.json``."""
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
    except Exception as e:
        raise RecordError(f"invalid record.json in {ws.root}: {e}") from e


# deepest battery layout is 6 levels; 7 also stops a mistyped root from walking a home directory
RUN_SEARCH_DEPTH = 7

#: subdirectories that mark a bench battery directory rather than a plain runs root
BATTERY_MARKERS = ("runs", "cells", "arms")
#: directories holding a sub-workspace (asset / best-of-N candidate): their files are not the parent run's
SUBRUN_DIRS: frozenset[str] = frozenset({"_cand", "_assets"})


def unique_files(root: Path | str, name: str, *, subruns: bool = False) -> list[Path]:
    """Every file called ``name`` under ``root``, once per file on disk.

    Follows symlinks and collapses duplicates by resolved path; sub-workspaces
    (:data:`SUBRUN_DIRS`) are skipped unless ``subruns``."""
    seen: dict[Path, Path] = {}
    root = Path(root)
    for p in sorted(root.rglob(name, recurse_symlinks=True)):
        if not subruns and SUBRUN_DIRS & set(p.relative_to(root).parts):
            continue
        seen.setdefault(p.resolve(), p)
    return sorted(seen.values())


def is_run_dir(p: Path) -> bool:
    return p.is_dir() and (p / "record.json").is_file()


def battery_label(root: Path | str) -> str:
    """Label for a scan root: the battery name for ``.../out/<battery>/runs``, else the directory name."""
    root = Path(root)
    if root.name == "runs" and root.parent.name and root.parent.parent.name == "out":
        return root.parent.name
    return root.name or str(root)


def run_id_for(root: Path | str, run_dir: Path) -> RunId:
    """The ``RunId`` of ``run_dir`` under scan root ``root`` (minted here: a Workspace forgets the scan root)."""
    return RunId(battery=battery_label(root), rel=run_dir.relative_to(Path(root)).as_posix())


class FoundRun(NamedTuple):

    ws: Workspace
    record: RunRecord
    run_id: RunId


def find_run_dirs(root: Path | str, *, predicate: Callable[[Path], bool] = is_run_dir,
                  max_depth: int = RUN_SEARCH_DEPTH) -> list[Path]:
    """Run directories under ``root``, sorted.

    Direct children win; only when there are none does it descend (battery layouts).
    A run is never descended into, and one reached through several paths is listed once, at its physical path.
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
    """``iter_runs(on_error=...)`` handler: log and skip an unreadable record.json."""
    log.warning("skipping %s: %s", run_dir, error)


def iter_runs(
    runs_dir: Path | str, *, on_error: Callable[[Path, Exception], None] | None = None
) -> Iterator[FoundRun]:
    """Yield a :class:`FoundRun` for every run directory holding a record.json.

    Invalid records raise ``RecordError`` unless ``on_error`` is given.
    """
    root = Path(runs_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"runs dir not found: {root}")
    children = find_run_dirs(root)
    battery_dirs = [m for m in BATTERY_MARKERS if (root / m).is_dir()]
    if not children and battery_dirs:
        # a battery directory that holds no run must not look like an empty dataset
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
