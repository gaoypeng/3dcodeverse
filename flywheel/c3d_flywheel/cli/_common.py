"""Shared CLI plumbing: Rich formatting, errors, and opening a recorded run by slug or path."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from c3d_flywheel.legacy import LegacySpec, RunRecord
from c3d_flywheel.runlayout import RunWorkspace

console = Console(emoji=False)
err_console = Console(stderr=True, style="bold red", emoji=False)

#: where a bare slug is looked up when no ``--runs-dir`` is given
DEFAULT_RUNS_DIR = Path("runs")

RunsDirOpt = Annotated[Path | None, typer.Option("--runs-dir", help=f"runs root a bare slug is resolved against (default: {DEFAULT_RUNS_DIR})")]


def err(msg: str) -> None:
    err_console.print(f"error: {msg}")


def ok(msg: str) -> None:
    console.print(f"[green]{msg}[/green]")


def warn(msg: str) -> None:
    console.print(f"[yellow]{msg}[/yellow]")


def fmt_score(s: float | None) -> str:
    return "-" if s is None else f"{s:.3f}"


def fmt_usd(v: float) -> str:
    return f"${v:.4f}"


class CliError(typer.Exit):

    def __init__(self, message: str, code: int = 1):
        err(message)
        super().__init__(code=code)


def runs_root(runs_dir: Path | None) -> Path:
    return Path(runs_dir) if runs_dir is not None else DEFAULT_RUNS_DIR


def open_workspace(slug: str, runs_dir: Path | None = None) -> RunWorkspace:
    """A run named by its directory, or by its slug under the runs root."""
    p = Path(slug)
    if p.is_dir() and (p / "spec.json").is_file():
        return RunWorkspace(p)
    ws = RunWorkspace(runs_root(runs_dir) / slug)
    if not ws.exists():
        raise CliError(f"run not found: {ws.root} (no spec.json)")
    return ws


def load_spec(ws: RunWorkspace) -> LegacySpec:
    try:
        return LegacySpec.model_validate_json(ws.spec_path.read_text())
    except Exception as e:
        raise CliError(f"invalid spec.json in {ws.root}: {e}") from e


def kv_table(title: str, rows: dict[str, Any]) -> Table:
    t = Table(title=title, show_header=False)
    t.add_column("key", style="bold")
    t.add_column("value")
    for k, v in rows.items():
        t.add_row(str(k), str(v))
    return t


def rounds_table(rows: list[Any], picked: int | None) -> Table:
    """``select.RoundRow`` rows; ``*`` marks the picked round."""
    t = Table(title="rounds (* = picked)", show_lines=False)
    for col in ("#", "kind", "build", "gate err", "score", "passed", "cost", "min", "commit"):
        t.add_column(col, justify="right" if col in ("#", "gate err", "score", "cost", "min") else "left")
    for r in rows:
        build = "-" if r.build_ok is None else ("ok" if r.build_ok else "[red]FAIL[/red]")
        passed = "-" if r.passed is None else ("[green]yes[/green]" if r.passed else "no")
        t.add_row(f"{r.index}{'*' if r.index == picked else ''}", r.kind, build, str(r.gate_errors), fmt_score(r.score),
                  passed, fmt_usd(r.cost_usd), f"{r.minutes:.1f}", r.commit[:8])
    return t


def print_record_summary(record: RunRecord, ws_root: Path | None = None) -> None:
    from c3d_flywheel import select

    spec = record.spec
    root = Path(ws_root or record.workspace)
    s = select.summarise(root, record=record)
    status_col = {"failed": "red", "budget": "yellow", "agent_quota": "yellow"}.get(record.status.value, "cyan")
    lines = [
        f"[bold]{spec.prompt}[/bold]",
        f"track={spec.track.value}  language={spec.language.value}  generator={spec.backends.generator}  "
        f"judge={spec.backends.judge}",
        f"stop=[{status_col}]{s.stop_reason}[/{status_col}]  baseline={fmt_score(s.baseline_score)}  "
        f"picked={fmt_score(s.picked_score)} (round {s.round}, "
        f"{f'by {s.method}' if s.picked_round is not None else 'nothing judged: the latest built round with renders'})  rounds={s.rounds}  "
        f"cost={fmt_usd(record.total_usage.cost_usd)}",
    ]
    if record.error:
        lines.append(f"[red]error: {record.error}[/red]")
    if ws_root is not None:
        ws = RunWorkspace(ws_root)
        lines.append(f"workspace: {ws_root}")
        picked = next((r for r in record.rounds if r.index == s.round), None)
        if picked is not None and picked.renders is not None and picked.renders.contact_sheet:
            # record.json stores absolute paths of the producing host; rebase onto this root
            lines.append(f"sheet: {ws.rebase(picked.renders.contact_sheet)}")
        if ws.deliverable_manifest_path.is_file():
            lines.append(f"deliverable: {ws.deliverable}")
    console.print(Panel("\n".join(lines), title="run", expand=False))
    if record.rounds:
        console.print(rounds_table(select.round_rows(root, record=record), s.round))
