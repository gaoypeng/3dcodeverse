"""``3dcodeverse cost`` — what the runs cost and where the money went, from each run's
``telemetry/cost.jsonl`` ledger.

    3dcodeverse cost <slug>                      # one run, from its own ledger
    3dcodeverse cost --runs-dir eval/bench/out/x # a whole battery, aggregated
    3dcodeverse cost runs/ eval/bench/out/*      # any mix of run dirs / trees
    3dcodeverse cost show runs/ --md report.md   # the same, explicit + full markdown
    3dcodeverse cost runs/ --recheck             # re-price with today's table (drift vs what was billed)
    3dcodeverse cost prices [--stale] [--unverified]
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import click
import typer
from typer.core import TyperGroup

from codeverse3d.cli._common import console, ok, runs_root, warn

#: a price row older than this needs re-checking against the provider's page
STALE_AFTER_DAYS = 90

class _CostGroup(TyperGroup):
    """A first argument that is not a subcommand is forwarded to ``show``."""

    def resolve_command(self, ctx: click.Context, args: list[str]):  # type: ignore[override]
        # typer vendors its own click, so its UsageError is not click.UsageError: test membership
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            return "show", self.get_command(ctx, "show"), args
        return super().resolve_command(ctx, args)


cost_app = typer.Typer(no_args_is_help=True, invoke_without_command=True, cls=_CostGroup)


@cost_app.callback()
def cost(
    ctx: typer.Context,
    runs_dir: Annotated[Path | None, typer.Option("--runs-dir", help="audit every run under this root")] = None,
    recheck: Annotated[bool, typer.Option("--recheck", help="re-price every call with today's table")] = False,
    md: Annotated[Path | None, typer.Option("--md", help="also write the full markdown report here")] = None,
) -> None:
    """Cost breakdown: per stage, per role, per model, waste, $ per run."""
    if ctx.invoked_subcommand is not None:
        return
    if runs_dir is None:
        console.print(ctx.get_help())
        raise typer.Exit()
    _report([Path(runs_dir)], md=md, recheck=recheck, limit=20, per_run=True)


@cost_app.command("show")
def show(
    paths: Annotated[list[Path], typer.Argument(help="run dirs / trees of runs (runs/, eval/bench/out/<battery>)")],
    md: Annotated[Path | None, typer.Option("--md", help="also write the full markdown report here")] = None,
    recheck: Annotated[bool, typer.Option("--recheck", help="re-price every call with today's table")] = False,
    limit: Annotated[int, typer.Option("--limit", help="rows in the per-run table")] = 20,
    per_run: Annotated[bool, typer.Option("--per-run/--no-per-run")] = True,
    runs_dir: Annotated[Path | None, typer.Option("--runs-dir", help="runs root a bare slug is resolved against")] = None,
) -> None:
    """Cost breakdown for runs named by slug or path."""
    _report([_resolve(str(p), runs_dir) for p in paths], md=md, recheck=recheck, limit=limit, per_run=per_run)


def _resolve(arg: str, runs_dir: Path | None) -> Path:
    p = Path(arg)
    if p.exists():
        return p
    return runs_root(runs_dir) / arg


def _report(paths: list[Path], *, md: Path | None, recheck: bool, limit: int, per_run: bool) -> None:
    from codeverse3d.addons.costreport.audit import audit_runs
    from codeverse3d.addons.costreport.report import console as text_report
    from codeverse3d.addons.costreport.report import markdown, runs_table

    audit = audit_runs(paths, recheck=recheck)
    if not audit.runs:
        raise typer.BadParameter(f"no run with a cost ledger under {', '.join(str(p) for p in paths)}")
    console.print(text_report(audit), soft_wrap=True)
    if per_run:
        console.print("")
        console.print(runs_table(audit, limit=limit), soft_wrap=True)
    if recheck:
        drift = audit.total_usd - audit.written_usd
        warn(f"re-priced total ${audit.total_usd:.4f} vs ${audit.written_usd:.4f} as written ({drift:+.4f})")
        for run in audit.runs:
            if abs(run.ledger_usd - run.written_usd) > 0.005:
                console.print(f"  {run.run}: ${run.ledger_usd - run.written_usd:+.4f}")
    if md:
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(markdown(audit, title=f"Cost audit — {', '.join(str(p) for p in paths)}"))
        ok(f"wrote {md}")


# --------------------------------------------------------------------------- prices
@cost_app.command("prices")
def prices(
    provider: Annotated[str | None, typer.Option("--provider", help="gemini | anthropic | openai")] = None,
    unverified: Annotated[bool, typer.Option("--unverified", help="only rows that are not verified")] = False,
    stale: Annotated[bool, typer.Option("--stale", help=f"only rows older than {STALE_AFTER_DAYS} days or approximate")] = False,
    days: Annotated[int, typer.Option("--days", help="staleness threshold in days")] = STALE_AFTER_DAYS,
) -> None:
    """The USD price table with its provenance; flags stale, approximate or unverified rows."""
    from codeverse3d.models.pricing import PRICES, price_provenance

    rows, flagged = [], 0
    for (prov, model) in sorted(PRICES):
        if provider and prov != provider:
            continue
        row = price_provenance(prov, model)
        if unverified and row.status == "verified":
            continue
        age = _age_days(row.checked)
        problems = _price_flags(row, age, days)
        if stale and not problems:
            continue
        flagged += bool(problems)
        p = row.price
        assert p is not None
        rows.append((f"{prov}:{model}", f"{p.input:g}", f"{p.cached:g}", f"{p.output:g}",
                     row.status, row.checked,
                     "-" if age is None else str(age), ",".join(problems) or "-",
                     p.note[:52]))
    header = ("model", "in/M", "cached/M", "out/M", "status", "checked", "age_d", "flags", "note")
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))] if rows else \
        [len(h) for h in header]
    console.print("  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True)), soft_wrap=True)
    for r in rows:
        console.print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)), soft_wrap=True)
    if flagged:
        warn(f"{flagged} of {len(rows)} row(s) flagged: re-check them against the provider's pricing page")
    elif rows or stale:  # --stale filters to the flagged rows: none left is the good answer
        ok(f"{len(rows)} price row(s) shown, none older than {days} days or approximate")


def _price_flags(row: object, age: int | None, days: int) -> list[str]:
    flags = []
    if age is not None and age > days:
        flags.append(f"stale>{days}d")
    price = getattr(row, "price", None)
    if price is not None and getattr(price, "approximate", False):
        flags.append("approximate")
    status = getattr(row, "status", "")
    if status not in ("verified", ""):
        flags.append(status)
    return flags


def _age_days(checked: str) -> int | None:
    try:
        return (date.today() - datetime.strptime(checked, "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        return None
