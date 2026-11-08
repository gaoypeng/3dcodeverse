"""Reading the recorded runs: ``load_record`` / ``iter_runs`` and the derived per-round views."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

from codeverse3d.contracts.artifacts import Judgment
from codeverse3d.workspace import Workspace

from c3d_flywheel.legacy import RoundRecord, RunId, RunRecord

log = logging.getLogger(__name__)


class RecordError(RuntimeError):
    """A record.json is missing or invalid."""


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
    """The run's rubric: the stamped one, else its last judged round's, else ``""``."""
    judged = (r.judgment.rubric for r in reversed(record.rounds) if r.judgment is not None and r.judgment.rubric)
    return str(record.extra.get("rubric") or "") or next(judged, "")


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
