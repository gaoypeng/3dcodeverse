"""Shared CLI plumbing: Rich formatting (run summaries, tables, observations, doctor
rows), the run-mutation mutex, workspace opening / creation and the cost-quality dial
resolution every command goes through."""

from __future__ import annotations

import hashlib
import math
import shutil
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
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
    """One row per round (``select.round_rows``: a degraded verdict counts as unjudged);
    "passed" is the judge's own verdict for THAT round."""
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
    """The run in one panel — its stop reason, the baseline and the round ``addons/select``
    picks (never a pass or a fail: a run has none) — and a row per round."""
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
        f"picked={fmt_score(s.picked_score)} (round {s.picked_round}, by {s.method})  rounds={s.rounds}  "
        f"cost={fmt_usd(record.total_usage.cost_usd)}",
    ]
    if record.error:
        lines.append(f"[red]error: {record.error}[/red]")
    if ws_root is not None:
        ws = Workspace(ws_root)
        lines.append(f"workspace: {ws_root}")
        picked = next((r for r in record.rounds if r.index == s.picked_round), None)
        if picked is not None and picked.renders is not None and picked.renders.contact_sheet:
            # rebase, never print the stored string: record.json holds the ABSOLUTE path
            # of the host that produced the run, so a moved/archived run printed a sheet
            # that does not exist while the real one sat under this root.
            lines.append(f"sheet: {ws.rebase(picked.renders.contact_sheet)}")
        if ws.deliverable_manifest_path.is_file():
            lines.append(f"deliverable: {ws.deliverable}")
    console.print(Panel("\n".join(lines), title="run", expand=False))
    if record.rounds:
        console.print(rounds_table(select.round_rows(root, record=record), s.picked_round))


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
    # three states, not two: a FAIL verdict is an answer the tool computed, an error is a
    # tool that could not run (the same split the MCP server reports as is_error)
    state, style = ("ok", "green") if obs.ok else (("error", "red") if obs.failed else ("FAIL", "yellow"))
    console.print(Panel(obs.text, title=f"[{style}]{state}[/{style}] ({obs.duration_ms} ms)", expand=False))
    if obs.numbers:
        console.print(kv_table("numbers", obs.numbers))
    for img in obs.images:
        console.print(f"image: {img}")


def doctor_table(rows: list[tuple[str, str, str]]) -> Table:
    """rows: (check, status OK|WARN|FAIL, detail)."""
    t = Table(title="3dcode doctor")
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
    """Typed, already-reported failure → exit code."""

    def __init__(self, message: str, code: int = 1):
        err(message)
        super().__init__(code=code)


#: the evaluation lives NEXT TO the harness (``<repo>/eval``) and is never imported by it;
#: the gallery only looks for the batteries' run directories under ``eval/bench/out``
EVAL_ROOT = REPO_ROOT.parent / "eval"


def runs_root(runs_dir: Path | None) -> Path:
    return Path(runs_dir) if runs_dir is not None else Path(get_settings().runs_dir)


def open_workspace(slug: str, runs_dir: Path | None = None) -> Workspace:
    """``slug`` may be a run name under runs_dir or a path to a run directory."""
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
    """The run mutex (``runlock.exclusive``) around anything that writes into a run
    directory, reported as a typed exit.  ``target`` is a Workspace or a run root."""
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
    """Create (or ``--force``-replace) a run directory.  The caller holds the run mutex —
    the rmtree below would otherwise wipe a live holder's workspace."""
    ws = Workspace(root)
    if ws.root.exists() and any(ws.root.iterdir()):
        if not force:
            raise CliError(f"{ws.root} already exists; use --force to overwrite or --slug for a new name")
        shutil.rmtree(ws.root)
    ws.create()
    return ws


def parse_kv_floats(items: list[str], flag: str) -> dict[str, float]:
    """``key=value`` pairs of positive, finite floats (``--dim``: metres).  ``height=0``,
    ``-1``, ``nan``, ``inf`` and a nameless ``=1`` used to be accepted, frozen on the spec and
    handed to the planner, the contract gate and the judge as a stated dimension."""
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
    """``--max-minutes`` must leave the run some time: 0 planned (paid), then stopped at the
    first clock check with no round — a ``budget`` run with nothing in it.  ``inf``/``nan`` is no clock at
    all: the session deadline cannot be computed from it."""
    if max_minutes is not None and max_minutes <= 0:
        raise CliError(f"--max-minutes must be above 0 (got {max_minutes:g}): a run with no time builds nothing", code=2)
    if max_minutes is not None and not math.isfinite(max_minutes):
        raise CliError(f"--max-minutes must be a finite number of minutes (got {max_minutes:g})", code=2)


def check_backends(backends: Any) -> None:
    """Refuse a backend id this build cannot construct, before a run directory exists.

    The ids were only resolved when a stage first asked for the model or agent, so an
    unknown ``--generator`` (``antigravity:...``, a typo) surfaced AFTER the paid plan
    stage, as a traceback and an orphan run.  The parsers are the constructors' own."""
    from codeverse3d.agents.registry import parse_agent_id
    from codeverse3d.models.registry import parse_model_id
    from codeverse3d.tracks.generation import is_single_shot, single_shot_model_id

    for role in ("generator", "planner", "judge", "captioner"):
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


# --------------------------------------------------------------------------- cost profile
def active_profile(settings: Any | None = None) -> Any:
    """The :class:`~codeverse3d.cost.profiles.Profile` the current settings name."""
    from codeverse3d.cost.profiles import get_profile

    return get_profile((settings or get_settings()).profile)


@dataclass(frozen=True)
class ResolvedDial:
    """Every profile-controlled value, after the profile and the user's own
    settings/flags have been folded together.  This is what a run actually gets."""

    profile: str
    generator: str
    planner: str
    judge: str
    captioner: str
    judge_samples: int
    judge_max_px: int
    judge_montages: int
    judge_detail_crops: int
    agent_max_turns: int
    rounds: int
    candidates: int
    texture: bool
    max_minutes: float


def resolve_dial(
    settings: Any | None = None,
    profile_flag: str | None = None,
    *,
    rounds: int | None = None,
    candidates: int | None = None,
    max_minutes: float | None = None,
    texture: bool = False,
) -> ResolvedDial:
    """The single resolver for the cost dial — used by ``3dcode make`` and by the tests.

    ``--profile X`` (``profile_flag``) *forces* the dial over anything the user
    stated in ``config.yaml`` / ``C3D_*``; ``C3D_PROFILE=X`` set the same dial
    as a *default* when :func:`codeverse3d.config.get_settings` built the settings.
    With nothing else stated the two paths therefore resolve **identically** —
    that equality is what ``tests/cost/test_profiles.py`` asserts, and it
    is the bug this function exists to prevent: the run shape (candidates, the
    texture pass) used to be read off the flag and so applied to only one path.

    An explicit CLI flag (``--rounds`` / ``--candidates`` / ``--max-minutes`` /
    ``--texture``) always wins over both.
    """
    settings = settings or get_settings()
    try:
        prof = settings.apply_profile(profile_flag, force=True) if profile_flag else active_profile(settings)
    except ValueError as e:  # e.g. `--profile bogus`: a typed error, not a raw traceback
        raise CliError(str(e), code=2) from e
    return ResolvedDial(
        profile=prof.name,
        generator=settings.default_generator,
        planner=settings.default_planner,
        judge=settings.default_judge,
        captioner=settings.default_captioner,
        judge_samples=int(settings.judge.samples),
        judge_max_px=int(settings.judge.max_px),
        judge_montages=int(settings.judge.montages),
        judge_detail_crops=int(settings.judge.detail_crops),
        agent_max_turns=int(settings.limits.agent_max_turns),
        rounds=prof.rounds if rounds is None else int(rounds),
        candidates=int(settings.default_candidates if candidates is None else candidates),
        texture=bool(texture or prof.texture),
        max_minutes=prof.max_minutes if max_minutes is None else float(max_minutes),
    )


def round_policy_options(spec: Spec, settings: Any | None = None) -> dict[str, Any]:
    """``get_track(...)`` options the active profile implies.

    Only the judge sample count needs a ``RoundPolicy`` (rounds travel on
    ``spec.budget``, best-of-N on ``spec.options``), so a run at the default
    ``n=1`` gets **no** policy and keeps the track's own."""
    settings = settings or get_settings()
    samples = int(getattr(settings.judge, "samples", 1) or 1)
    if samples <= 1:
        return {}
    from codeverse3d.orchestrator import RoundPolicy

    return {"policy": RoundPolicy(max_rounds=spec.budget.max_rounds, judge_samples=samples)}


