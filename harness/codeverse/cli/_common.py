"""Rich formatting for the CLI: run summaries, tables, observations, doctor rows."""

from __future__ import annotations

import hashlib
import importlib
import shutil
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codeverse.config import get_settings
from codeverse.contracts.run import RunRecord
from codeverse.contracts.spec import Spec
from codeverse.conventions import slugify
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


# ===================================================================== _common
# (merged from codeverse/cli/_common.py, 2026-08-28)
REPO_ROOT = Path(__file__).resolve().parents[2]


class CliError(typer.Exit):
    """Typed, already-reported failure → exit code."""

    def __init__(self, message: str, code: int = 1):
        err(message)
        super().__init__(code=code)


def lazy(module: str, attr: str | None = None) -> Any:
    """Import ``module`` (and ``attr``) at call time; a clear message when the
    sub-package is not implemented yet (packages are developed in parallel)."""
    try:
        mod = importlib.import_module(module)
    except ImportError as e:
        raise CliError(
            f"{module} is not available ({e}). This command needs that sub-package; "
            f"run `3dcv doctor` to see what is installed.", code=2
        ) from e
    if attr is None:
        return mod
    try:
        return getattr(mod, attr)
    except AttributeError as e:
        raise CliError(f"{module}.{attr} is missing ({e}); the sub-package may be incomplete.", code=2) from e


def import_bench() -> Any:
    """``bench`` lives at the repo root (not inside the package); make it importable."""
    try:
        return importlib.import_module("bench")
    except ImportError:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        return lazy("bench")


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
    from codeverse.runlock import RunLocked, exclusive

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
    out: dict[str, float] = {}
    for it in items:
        if "=" not in it:
            raise CliError(f"{flag} expects key=value, got {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = float(v)
        except ValueError as e:
            raise CliError(f"{flag} {k}: not a number: {v!r}") from e
    return out


# --------------------------------------------------------------------------- cost profile
def active_profile(settings: Any | None = None) -> Any:
    """The :class:`~codeverse.cost.profiles.Profile` the current settings name."""
    from codeverse.cost.profiles import get_profile

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
    max_usd: float
    max_minutes: float


def resolve_dial(
    settings: Any | None = None,
    profile_flag: str | None = None,
    *,
    rounds: int | None = None,
    candidates: int | None = None,
    max_usd: float | None = None,
    max_minutes: float | None = None,
    texture: bool = False,
) -> ResolvedDial:
    """The single resolver for the cost dial — used by ``3dcv make`` and by the tests.

    ``--profile X`` (``profile_flag``) *forces* the dial over anything the user
    stated in ``config.yaml`` / ``CV3D_*``; ``CV3D_PROFILE=X`` set the same dial
    as a *default* when :func:`codeverse.config.get_settings` built the settings.
    With nothing else stated the two paths therefore resolve **identically** —
    that equality is what ``tests/cost/test_profiles.py`` asserts, and it
    is the bug this function exists to prevent: the run shape (candidates, the
    texture pass) used to be read off the flag and so applied to only one path.

    An explicit CLI flag (``--rounds`` / ``--candidates`` / ``--max-usd`` /
    ``--max-minutes`` / ``--texture``) always wins over both.
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
        max_usd=prof.max_usd if max_usd is None else float(max_usd),
        max_minutes=prof.max_minutes if max_minutes is None else float(max_minutes),
    )


def round_policy_options(spec: Spec, settings: Any | None = None) -> dict[str, Any]:
    """``get_track(...)`` options the active profile implies.

    Only the judge sample count needs a ``RoundPolicy`` (rounds travel on
    ``spec.budget``, best-of-N on ``spec.options``), so a run at the default
    ``n=1`` gets **no** policy and keeps the track's own — including the
    rubric-derived stop target that ``BaseTrack.after_plan`` binds when no policy
    was injected.  When a profile does ask for more samples we bind that target
    here instead, from the same rubric the track will use."""
    settings = settings or get_settings()
    samples = int(getattr(settings.judge, "samples", 1) or 1)
    if samples <= 1:
        return {}
    from dataclasses import replace

    from codeverse.orchestrator import RoundPolicy

    policy = RoundPolicy(max_rounds=spec.budget.max_rounds, judge_samples=samples)
    threshold = rubric_threshold(spec)
    if threshold is not None:
        policy = replace(policy, target=float(threshold))
    return {"policy": policy}


def rubric_threshold(spec: Spec) -> float | None:
    """Pass threshold of the rubric this spec will be judged with (``None`` when
    the rubric cannot be loaded — the caller then keeps the policy default)."""
    try:
        from codeverse.contracts.common import Track
        from codeverse.judges.rubrics import load_rubric
        from codeverse.tracks import get_track
        from codeverse.tracks.lifecycle import REFERENCE_RUBRIC

        # same selection rule as BaseTrack.build_context
        name = (REFERENCE_RUBRIC if spec.references and spec.track is Track.STATIC_OBJECT
                else get_track(spec.track).rubric)
        return float(load_rubric(name).pass_threshold)
    except Exception:  # noqa: BLE001 - a missing rubric must not stop a run
        return None
