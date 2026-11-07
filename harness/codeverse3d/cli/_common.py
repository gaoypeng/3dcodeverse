"""Shared CLI plumbing: Rich formatting, the run mutex and workspace opening / creation."""

from __future__ import annotations

import hashlib
import math
import shutil
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codeverse3d.config import get_settings
from codeverse3d.contracts.run import RunRecord
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import slugify
from codeverse3d.workspace import Workspace

if TYPE_CHECKING:
    from codeverse3d.addons.select import RoundRow

console = Console(emoji=False)
err_console = Console(stderr=True, style="bold red", emoji=False)

RunsDirOpt = Annotated[Path | None, typer.Option("--runs-dir", help="runs root (default: settings.runs_dir)")]


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


def rounds_table(rows: list[RoundRow], picked: int | None) -> Table:
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
    from codeverse3d.addons import select

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
        ws = Workspace(ws_root)
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
    # FAIL is a computed verdict; error is a tool that could not run
    state, style = ("ok", "green") if obs.ok else (("error", "red") if obs.failed else ("FAIL", "yellow"))
    console.print(Panel(obs.text, title=f"[{style}]{state}[/{style}] ({obs.duration_ms} ms)", expand=False))
    if obs.numbers:
        console.print(kv_table("numbers", obs.numbers))
    for img in obs.images:
        console.print(f"image: {img}")


def doctor_table(rows: list[tuple[str, str, str]]) -> Table:
    t = Table(title="3dcodeverse doctor")
    t.add_column("check", style="bold")
    t.add_column("status")
    t.add_column("detail")
    col = {"OK": "green", "WARN": "yellow", "FAIL": "red", "SKIP": "dim"}
    for name, status, detail in rows:
        t.add_row(name, f"[{col.get(status, 'white')}]{status}[/{col.get(status, 'white')}]", detail)
    return t


# ===================================================================== _common
REPO_ROOT = Path(__file__).resolve().parents[2]


class CliError(typer.Exit):

    def __init__(self, message: str, code: int = 1):
        err(message)
        super().__init__(code=code)


#: ``<repo>/eval``, never imported by the harness
EVAL_ROOT = REPO_ROOT.parent / "eval"


def runs_root(runs_dir: Path | None) -> Path:
    return Path(runs_dir) if runs_dir is not None else Path(get_settings().runs_dir)


def open_workspace(slug: str, runs_dir: Path | None = None) -> Workspace:
    p = Path(slug)
    if p.is_dir() and (p / "spec.json").is_file():
        return Workspace(p)
    ws = Workspace(runs_root(runs_dir) / slug)
    if not ws.exists():
        raise CliError(f"run not found: {ws.root} (no spec.json)")
    return ws


def load_spec(ws: Workspace) -> Spec:
    try:
        return Spec.model_validate_json(ws.spec_path.read_text())
    except Exception as e:
        raise CliError(f"invalid spec.json in {ws.root}: {e}") from e


def make_slug(prompt: str, track: str, language: str, explicit: str | None = None) -> str:
    if explicit:
        return slugify(explicit, 64)
    h = hashlib.sha256(f"{prompt.strip()}|{track}|{language}".encode()).hexdigest()[:8]
    return f"{slugify(prompt, 40)}_{h}"


@contextmanager
def mutating(target: Any, *, what: str, action: str = "enter") -> Iterator[None]:
    """The run mutex around anything that writes into a run directory; ``target`` is a
    Workspace or a run root."""
    from codeverse3d.proc import RunLocked, exclusive

    root = target if isinstance(target, (str, Path)) else target.root  # NB: Path.root is "/"
    stack = ExitStack()
    try:
        stack.enter_context(exclusive(root, what=what, action=action))
    except RunLocked as e:
        raise CliError(str(e), code=2) from None
    with stack:
        yield


def create_workspace(root: Path, *, force: bool) -> Workspace:
    """Create (or ``--force``-replace) a run directory; the caller holds the run mutex."""
    ws = Workspace(root)
    if ws.root.exists() and any(ws.root.iterdir()):
        if not force:
            raise CliError(f"{ws.root} already exists; use --force to overwrite or --slug for a new name")
        shutil.rmtree(ws.root)
    ws.create()
    return ws


def parse_kv_floats(items: list[str], flag: str) -> dict[str, float]:
    """``key=value`` pairs of positive, finite floats (``--dim``: metres)."""
    out: dict[str, float] = {}
    for it in items:
        if "=" not in it:
            raise CliError(f"{flag} expects key=value, got {it!r}")
        k, v = (s.strip() for s in it.split("=", 1))
        if not k:
            raise CliError(f"{flag} expects key=value, got {it!r} (no key)")
        try:
            out[k] = float(v)
        except ValueError as e:
            raise CliError(f"{flag} {k}: not a number: {v!r}") from e
        if not math.isfinite(out[k]) or out[k] <= 0:
            raise CliError(f"{flag} {k}: must be a positive number, got {v!r}")
    return out


def check_minutes(max_minutes: float | None) -> None:
    """``--max-minutes`` must be a finite number above 0."""
    if max_minutes is not None and max_minutes <= 0:
        raise CliError(f"--max-minutes must be above 0 (got {max_minutes:g}): a run with no time builds nothing", code=2)
    if max_minutes is not None and not math.isfinite(max_minutes):
        raise CliError(f"--max-minutes must be a finite number of minutes (got {max_minutes:g})", code=2)


def check_backends(backends: Any) -> None:
    """Refuse a backend id this build cannot construct, before a run directory exists."""
    from codeverse3d.agents.registry import parse_agent_id
    from codeverse3d.models.registry import parse_model_id
    from codeverse3d.tracks.generation import is_single_shot, single_shot_model_id

    for role in ("generator", "planner", "judge"):
        value = getattr(backends, role)
        try:
            if role != "generator":
                parse_model_id(value)
            elif is_single_shot(value):
                parse_model_id(single_shot_model_id(value))
            else:
                parse_agent_id(value)
        except ValueError as e:
            raise CliError(f"--{role} {value!r}: {e}", code=2) from None



