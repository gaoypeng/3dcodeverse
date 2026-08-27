"""Move an existing run directory onto the three-bucket layout, in place.

``migrate_runs(runs_dir)`` walks every run under ``runs_dir`` and, for each:

1. creates the buckets + navigation aliases (``deliverable/``, ``telemetry/``,
   ``evidence`` → ``artifacts``, ``telemetry/{events.jsonl,run_state.json,stages,
   trajectories}`` → the root files) — nothing is moved, so every path stored in
   an old ``record.json`` keeps resolving;
2. builds ``deliverable/`` from the best round and ``telemetry/`` from the
   trajectories + judge verdicts + events already on disk;
3. adds the ``telemetry`` / ``deliverable`` blocks to ``record.json``.

It never deletes evidence, never rewrites ``src/`` and never touches the git
history.  Running it twice is a no-op (second pass reports ``up_to_date``).
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.flywheel.record import RecordError, load_record, package_run
from codeverse.runlock import RunLocked, exclusive
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

MIGRATED = "migrated"
UP_TO_DATE = "up_to_date"
SKIPPED = "skipped"
FAILED = "failed"


class RunMigration(BaseModel):
    """What happened to one run directory."""

    run: str
    status: str = UP_TO_DATE
    layout: dict[str, str] = Field(default_factory=dict, description="path → created | relinked | kept | skipped:…")
    gitignore: bool = False
    deliverable_files: int | None = None
    deliverable_bytes: int = 0
    telemetry_rows: int | None = None
    record_updated: bool = False
    reason: str = ""


class MigrationReport(BaseModel):
    runs_dir: str
    dry_run: bool = False
    n_runs: int = 0
    n_migrated: int = 0
    n_up_to_date: int = 0
    n_skipped: int = 0
    n_failed: int = 0
    runs: list[RunMigration] = Field(default_factory=list)

    def add(self, m: RunMigration) -> RunMigration:
        self.runs.append(m)
        self.n_runs += 1
        setattr(self, f"n_{m.status}", getattr(self, f"n_{m.status}") + 1)
        return m


def migrate_run(ws: Workspace, *, dry_run: bool = False) -> RunMigration:
    """Reorganise one run in place; idempotent, additive, never destructive.

    Under the run mutex unless ``dry_run`` (it relinks the layout and rewrites
    ``record.json``); a run someone is using is reported SKIPPED, not migrated."""
    m = RunMigration(run=str(ws.root))
    if not ws.exists():
        m.status = SKIPPED
        m.reason = "no spec.json"
        return m
    stack = ExitStack()
    try:
        if not dry_run:
            stack.enter_context(exclusive(ws.root, what=f"3dcv migrate-runs {ws.root.name}"))
    except RunLocked as e:
        m.status, m.reason = SKIPPED, str(e)
        return m
    with stack:
        try:
            m.layout = ws.ensure_layout(dry_run=dry_run)
            m.gitignore = ws.ensure_gitignore(dry_run=dry_run)
        except OSError as e:
            m.status = FAILED
            m.reason = f"{type(e).__name__}: {e}"
            return m
        changed = bool(m.layout) or m.gitignore
        try:
            record = load_record(ws)
        except RecordError as e:
            m.status = MIGRATED if changed else UP_TO_DATE
            m.reason = str(e)  # layout only: an unfinished run has no record yet
            return m
        if dry_run:
            m.telemetry_rows = _row_count(ws, record)
            m.deliverable_files = None
            m.status = MIGRATED if (changed or record.telemetry is None or record.deliverable is None) else UP_TO_DATE
            return m
        before = ws.record_path.read_bytes()
        package_run(ws, record)
        if record.deliverable is not None:
            m.deliverable_files = len(record.deliverable.files)
            m.deliverable_bytes = record.deliverable.total_bytes
        if record.telemetry is not None and record.telemetry.cost is not None:
            m.telemetry_rows = record.telemetry.cost.n_calls
        ws.write_json(ws.record_path, record)
        m.record_updated = ws.record_path.read_bytes() != before
        m.status = MIGRATED if (changed or m.record_updated) else UP_TO_DATE
        return m


def _row_count(ws: Workspace, record: object) -> int | None:
    """Per-call rows the ledger would write (dry-run reporting only)."""
    from codeverse.flywheel.telemetry import ledger_rows

    try:
        rows, _source = ledger_rows(ws)
    except Exception as e:  # noqa: BLE001
        log.debug("row count failed for %s: %s", ws.root, e)
        return None
    return len(rows)


def iter_run_dirs(runs_dir: Path | str) -> list[Path]:
    """Run directories under ``runs_dir`` (a run = a dir with ``spec.json``);
    ``runs_dir`` itself when it is a single run."""
    root = Path(runs_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"runs dir not found: {root}")
    if (root / "spec.json").is_file():
        return [root]
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "spec.json").is_file())


def migrate_runs(runs_dir: Path | str, *, dry_run: bool = False) -> MigrationReport:
    """Migrate every run under ``runs_dir``; returns a per-run report."""
    rep = MigrationReport(runs_dir=str(runs_dir), dry_run=dry_run)
    for d in iter_run_dirs(runs_dir):
        try:
            rep.add(migrate_run(Workspace(d), dry_run=dry_run))
        except Exception as e:  # noqa: BLE001 - one bad run must not stop the batch
            log.warning("migration failed for %s: %s", d, e)
            rep.add(RunMigration(run=str(d), status=FAILED, reason=f"{type(e).__name__}: {e}"))
    return rep
