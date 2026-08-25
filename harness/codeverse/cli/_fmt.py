"""Rich formatting for the CLI: run summaries, tables, observations, doctor rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codeverse.contracts.run import RunRecord
from codeverse.workspace import Workspace

console = Console(emoji=False)
err_console = Console(stderr=True, style="bold red", emoji=False)


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


def rounds_table(record: RunRecord) -> Table:
    t = Table(title="rounds (* = best)", show_lines=False)
    for col in ("#", "kind", "build", "gate err", "score", "passed", "cost", "secs", "commit"):
        t.add_column(col, justify="right" if col in ("#", "gate err", "score", "cost", "secs") else "left")
    for r in record.rounds:
        build = "-" if r.build is None else ("ok" if r.build.ok else "[red]FAIL[/red]")
        j = r.judgment
        passed = "-" if j is None else ("[green]yes[/green]" if j.passed else "no")
        best = "*" if record.best_round is not None and r.index == record.best_round else ""
        t.add_row(f"{r.index}{best}", r.kind, build, str(sum(len(g.errors) for g in r.gates)), fmt_score(r.score),
                  passed, fmt_usd(r.usage.cost_usd), f"{r.duration_s:.0f}", r.commit[:8])
    return t


def print_record_summary(record: RunRecord, ws_root: Path | None = None) -> None:
    spec = record.spec
    status_col = {"passed": "green", "failed": "red", "budget": "yellow", "plateau": "yellow"}.get(record.status.value, "cyan")
    lines = [
        f"[bold]{spec.prompt}[/bold]",
        f"track={spec.track.value}  language={spec.language.value}  generator={spec.backends.generator}",
        f"status=[{status_col}]{record.status.value}[/{status_col}]  baseline={fmt_score(record.baseline_score)}  "
        f"best={fmt_score(record.final_score)} (round {record.best_round})  rounds={len(record.rounds)}  "
        f"cost={fmt_usd(record.total_usage.cost_usd)}",
    ]
    if record.error:
        lines.append(f"[red]error: {record.error}[/red]")
    if ws_root is not None:
        lines.append(f"workspace: {ws_root}")
        best = next((r for r in record.rounds if r.index == record.best_round), None)
        if best is not None and best.renders is not None and best.renders.contact_sheet:
            # rebase, never print the stored string: record.json holds the ABSOLUTE path
            # of the host that produced the run, so a moved/archived run printed a sheet
            # that does not exist while the real one sat under this root.
            lines.append(f"sheet: {Workspace(ws_root).rebase(best.renders.contact_sheet)}")
        glb = ws_root / "artifacts" / "object.glb"
        if glb.is_file():
            lines.append(f"glb: {glb}")
    console.print(Panel("\n".join(lines), title="run", expand=False))
    if record.rounds:
        console.print(rounds_table(record))


def kv_table(title: str, rows: dict[str, Any]) -> Table:
    t = Table(title=title, show_header=False)
    t.add_column("key", style="bold")
    t.add_column("value")
    for k, v in rows.items():
        t.add_row(str(k), str(v))
    return t


def print_observation(obs: Any, *, as_json: bool) -> None:
    if as_json:
        console.print_json(obs.model_dump_json())
        return
    style = "green" if obs.ok else "red"
    console.print(Panel(obs.text, title=f"[{style}]{'ok' if obs.ok else 'error'}[/{style}] ({obs.duration_ms} ms)", expand=False))
    if obs.numbers:
        console.print(kv_table("numbers", obs.numbers))
    for img in obs.images:
        console.print(f"image: {img}")


def doctor_table(rows: list[tuple[str, str, str]]) -> Table:
    """rows: (check, status OK|WARN|FAIL, detail)."""
    t = Table(title="3dcv doctor")
    t.add_column("check", style="bold")
    t.add_column("status")
    t.add_column("detail")
    col = {"OK": "green", "WARN": "yellow", "FAIL": "red", "SKIP": "dim"}
    for name, status, detail in rows:
        t.add_row(name, f"[{col.get(status, 'white')}]{status}[/{col.get(status, 'white')}]", detail)
    return t
