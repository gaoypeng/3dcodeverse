"""``3dcv show`` + ``3dcv migrate-runs`` — the run directory, read and reorganised.

``show`` prints one run as three clearly separated sections (docs/RUN_LAYOUT.md):

* **DELIVERABLE** — what the run produced and where to find it;
* **QUALITY EVIDENCE** — why we believe it is good (score, rubric, gates, acceptance);
* **COST & SETTINGS** — tokens and dollars per stage, model ids, thinking levels,
  rubric hash, budget vs spent, wall clock.

Both commands work on the new layout and on runs written before it: the cost /
settings block is computed on the fly when ``telemetry/`` is not there yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from codeverse.cli import _common as C
from codeverse.cli._common import console, fmt_score, fmt_usd, kv_table, ok, warn
from codeverse.contracts.run import (
    CostSummary,
    DeliverableFile,
    RunDeliverable,
    RunRecord,
    SettingsSnapshot,
)
from codeverse.workspace import Workspace

RunsDirOpt = Annotated[Path | None, typer.Option("--runs-dir", help="runs root (default: settings.runs_dir)")]


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024.0
    return f"{size:,.1f} GB"


def _section(title: str) -> None:
    console.rule(f"[bold]{title}[/bold]", align="left")


# --------------------------------------------------------------------------- (a) deliverable
MAX_ROWS_PER_ROLE = 6


def _deliverable_table(d: RunDeliverable) -> Table:
    """One row per file, except roles with many files (frame stacks, link meshes,
    a multi-file source tree) which collapse into a single counted row."""
    t = Table(title="files (deliverable/)", show_lines=False)
    for col in ("role", "path", "size"):
        t.add_column(col, justify="right" if col == "size" else "left")
    by_role: dict[str, list[DeliverableFile]] = {}
    for f in sorted(d.files, key=lambda f: (f.role, f.path)):
        by_role.setdefault(f.role, []).append(f)
    for role, files in by_role.items():
        if len(files) <= MAX_ROWS_PER_ROLE:
            for f in files:
                t.add_row(role, f.path, _human_bytes(f.bytes))
            continue
        dirs = {f.path.rsplit("/", 1)[0] for f in files}
        head = f"{dirs.pop()}/  " if len(dirs) == 1 else ""
        t.add_row(role, f"{head}({len(files)} files)", _human_bytes(sum(f.bytes for f in files)))
    return t


def _legacy_deliverable_rows(ws: Workspace) -> dict[str, Any]:
    """Old layout: point at the physical artifacts instead of ``deliverable/``."""
    rows: dict[str, Any] = {}
    for name in ("object.glb", "object.stl", "object.step", "robot.urdf", "object_textured.glb", "preview.gif"):
        p = ws.artifacts / name
        if p.is_file():
            rows[name] = f"{p}  ({_human_bytes(p.stat().st_size)})"
    if ws.src.is_dir():
        rows["src/"] = str(ws.src)
    return rows


def print_deliverable(ws: Workspace, record: RunRecord) -> None:
    from codeverse.flywheel.deliverable import load_deliverable

    _section("DELIVERABLE — what the run produced")
    spec = record.spec
    head = {"prompt": spec.prompt, "track": spec.track.value, "language": spec.language.value,
            "status": record.status.value, "best round": record.best_round,
            "score": fmt_score(record.final_score), "workspace": ws.root}
    d = load_deliverable(ws, record)
    if d is not None:
        head["code"] = f"{d.entry or 'deliverable/src/'} @ {d.commit[:12] or '-'} ({d.code_source})"
        head["total"] = f"{len(d.files)} files, {_human_bytes(d.total_bytes)}"
    console.print(kv_table("deliverable", head))
    if d is not None:
        console.print(_deliverable_table(d))
        for name, why in d.skipped.items():
            warn(f"not packaged: {name} — {why}")
    else:
        console.print(kv_table("artifacts (old layout — run `3dcv migrate-runs` to build deliverable/)",
                               _legacy_deliverable_rows(ws)))


# --------------------------------------------------------------------------- (b) evidence
def print_evidence(ws: Workspace, record: RunRecord) -> None:
    from codeverse.flywheel.record import effective_judgment
    from codeverse.flywheel.sample import best_round_record, gate_error_summary

    _section("QUALITY EVIDENCE — why we believe it")
    rnd = best_round_record(record)
    j = effective_judgment(rnd) if rnd is not None else None
    gates = gate_error_summary(rnd)
    rows: dict[str, Any] = {
        "baseline → best": f"{fmt_score(record.baseline_score)} → {fmt_score(record.final_score)}",
        "rubric": (j.rubric if j else "") or str(record.extra.get("rubric") or "-"),
        "passed": "-" if j is None else j.passed,
        "gate errors": f"{sum(gates.values())} ({', '.join(f'{g}:{n}' for g, n in gates.items() if n) or 'none'})",
        "stop reason": str(record.extra.get("stop_reason") or "-"),
    }
    if j is not None:
        acc = j.acceptance_results or {}
        rows["acceptance"] = f"{sum(1 for v in acc.values() if v)}/{len(acc)} met" if acc else "-"
        rows["judge summary"] = j.summary[:300]
    if rnd is not None and rnd.renders is not None and rnd.renders.contact_sheet:
        # rebase, never print the stored string: record.json holds the ABSOLUTE path the
        # renderer wrote, so an archived / rsynced / moved run made `3dcv show` print a
        # sheet under the ORIGINAL root — a path that is not there, next to an object.glb
        # that resolved correctly (it is recomputed from the workspace), which is what
        # made the breakage silent and partial.  ``_fmt`` does the same for `3dcv status`.
        rows["contact sheet"] = ws.rebase(rnd.renders.contact_sheet)
    if rnd is not None and rnd.measurement is not None:
        mm = rnd.measurement
        rows["measured"] = (f"extents {tuple(round(v, 3) for v in mm.extents)} m · {mm.tri_count} tris · "
                            f"{mm.n_meshes} meshes · ground gap {mm.ground_gap_m:.3f} m")
    rows["evidence dir"] = f"{ws.evidence if ws.evidence.exists() else ws.artifacts} (renders/ gates/ judge/)"
    console.print(kv_table("evidence", rows))
    if j is not None and j.issues:
        for issue in j.issues[:5]:
            console.print(f"  [yellow]{issue.severity}[/yellow] {issue.target}: {issue.detail[:160]}")


# --------------------------------------------------------------------------- (c) cost & settings
def _stage_table(cost: CostSummary) -> Table:
    t = Table(title="cost by stage", show_lines=False)
    for col in ("stage", "calls", "in tok", "out tok", "cached", "USD", "% run", "secs"):
        t.add_column(col, justify="left" if col == "stage" else "right")
    total = cost.total_usd or 1.0
    for s in cost.by_stage:
        t.add_row(s.stage, str(s.calls), f"{s.input_tokens:,}", f"{s.output_tokens:,}", f"{s.cached_tokens:,}",
                  fmt_usd(s.cost_usd), f"{100.0 * s.cost_usd / total:.0f}%", f"{s.seconds:.0f}")
    return t


def _roles_table(settings: SettingsSnapshot) -> Table:
    t = Table(title="models per role", show_lines=False)
    for col in ("role", "model", "thinking", "temp", "samples", "source"):
        t.add_column(col)
    for r in settings.roles:
        t.add_row(r.role, r.model, r.thinking or "-", "-" if r.temperature is None else f"{r.temperature:g}",
                  "-" if r.n_samples is None else str(r.n_samples), r.source)
    return t


def print_cost_and_settings(ws: Workspace, record: RunRecord) -> None:
    from codeverse.flywheel.telemetry import build_telemetry, load_telemetry

    _section("COST & SETTINGS — token price and the key step settings")
    tele = load_telemetry(ws, record)
    computed = False
    if tele is None or tele.cost is None:
        tele = build_telemetry(ws, record, write=False)  # old layout: compute, do not write
        computed = True
    cost, settings = tele.cost, tele.settings
    if cost is not None:
        u = cost.tokens
        budget_line = (f"{fmt_usd(cost.total_usd)} of {fmt_usd(cost.budget_usd)}"
                       f"{'' if cost.budget_used_pct is None else f' ({cost.budget_used_pct:.0f}%)'}")
        console.print(kv_table("cost", {
            "spent / budget": budget_line,
            "wall clock": f"{cost.wall_clock_s / 60:.1f} min of {cost.max_minutes:.0f} min",
            "tokens": f"in {u.input_tokens:,} (cached {u.cached_tokens:,}) · out {u.output_tokens:,} · "
                      f"thoughts {u.thoughts_tokens:,} · {u.tool_calls} tool calls",
            "model calls": cost.n_calls,
            **({"post-run calls": f"{fmt_usd(cost.post_run_usd)} priced outside the run total (texture pass …)"}
               if cost.post_run_usd >= 0.0005 else {}),
            **({"unattributed": f"{fmt_usd(cost.unattributed_usd)} with no per-call row (ledger residual)"}
               if cost.unattributed_usd >= 0.0005 else {}),
            "by role": " · ".join(f"{r} {fmt_usd(v)}" for r, v in sorted(cost.by_role.items(), key=lambda kv: -kv[1])) or "-",
            "by model": " · ".join(f"{m or '(unknown)'} {fmt_usd(v)}" for m, v in sorted(cost.by_model.items(), key=lambda kv: -kv[1])),
            "rounds": " · ".join(f"r{r['index']} {r['kind']} {fmt_usd(r['cost_usd'])}" for r in cost.by_round),
        }))
        console.print(_stage_table(cost))
    if settings is not None:
        console.print(_roles_table(settings))
        console.print(kv_table("settings", {
            "budget": " · ".join(f"{k}={v}" for k, v in settings.budget.items()),
            "candidates": settings.candidates or 1,
            "texture pass": settings.texture,
            "seed": settings.seed,
            "rubric": f"{settings.rubric} (hash {settings.rubric_hash or '-'})",
            "prompt hashes": " · ".join(f"{k}={v}" for k, v in settings.prompt_hashes.items()) or "-",
            "tools": " · ".join(f"{k}={v}" for k, v in settings.tool_versions.items() if v and k != "platform"),
            "harness": f"{settings.harness_version} @ {settings.harness_git_sha[:12] or 'no-sha'}",
            "key pool": settings.key_pool_size,
            "price table": settings.price_table_version or "-",
            "render": " · ".join(f"{k}={v}" for k, v in settings.render.items()),
        }))
    if computed:
        console.print("[dim]computed on the fly (no telemetry/ yet — `3dcv migrate-runs` writes it)[/dim]")
    else:
        console.print(f"[dim]telemetry: {ws.cost_path} · {ws.usage_path} · {ws.settings_path}[/dim]")


# --------------------------------------------------------------------------- commands
def show(
    slug: str,
    runs_dir: RunsDirOpt = None,
    section: Annotated[str, typer.Option("--section", help="all | deliverable | evidence | cost")] = "all",
) -> None:
    """Show one run in three separated sections: DELIVERABLE / QUALITY EVIDENCE / COST & SETTINGS."""
    from codeverse.flywheel.record import RecordError, load_record

    ws = C.open_workspace(slug, runs_dir)
    try:
        record = load_record(ws)
    except RecordError as e:
        raise C.CliError(f"{e} (run `3dcv status {slug}` for a partial view)") from e
    if section not in ("all", "deliverable", "evidence", "cost"):
        raise C.CliError(f"unknown --section {section!r} (all | deliverable | evidence | cost)")
    if section in ("all", "deliverable"):
        print_deliverable(ws, record)
    if section in ("all", "evidence"):
        print_evidence(ws, record)
    if section in ("all", "cost"):
        print_cost_and_settings(ws, record)


def migrate_runs_cmd(
    runs_dir: Annotated[Path, typer.Argument(help="runs root (or a single run directory)")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="report what would change; touch nothing")] = False,
) -> None:
    """Reorganise existing runs in place onto deliverable/ + evidence/ + telemetry/ (idempotent)."""
    from codeverse.flywheel.migrate import MIGRATED, migrate_runs

    try:
        rep = migrate_runs(runs_dir, dry_run=dry_run)
    except FileNotFoundError as e:
        raise C.CliError(str(e)) from e
    t = Table(title=f"{'would migrate' if dry_run else 'migrated'} {rep.runs_dir}")
    for col in ("run", "status", "layout", "deliverable", "telemetry rows", "record", "note"):
        t.add_column(col)
    for m in rep.runs:
        t.add_row(Path(m.run).name, m.status,
                  ", ".join(f"{k}:{v}" for k, v in list(m.layout.items())[:3]) or "-",
                  "-" if m.deliverable_files is None else f"{m.deliverable_files} files / {_human_bytes(m.deliverable_bytes)}",
                  "-" if m.telemetry_rows is None else str(m.telemetry_rows),
                  "updated" if m.record_updated else "-", m.reason[:60])
    console.print(t)
    summary = (f"{rep.n_runs} runs · {rep.n_migrated} {'to migrate' if dry_run else MIGRATED} · "
               f"{rep.n_up_to_date} up to date · {rep.n_skipped} skipped · {rep.n_failed} failed")
    (warn if rep.n_failed else ok)(summary)
    if rep.n_failed:
        raise typer.Exit(code=1)
