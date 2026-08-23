"""``3dcv cost`` — what the runs cost and where the money went.

    3dcv cost runs/                       # audit a tree of runs
    3dcv cost runs/e2e_chair_blender      # one run, stage by stage
    3dcv cost bench/out/static_v1_flash --md report.md
    3dcv cost runs/ --recheck             # re-price with today's table (drift vs what was billed)
    3dcv cost prices                      # the price table + provenance
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from codeverse.cli._fmt import console, ok, warn

cost_app = typer.Typer(no_args_is_help=True)


@cost_app.command("show")
def show(
    paths: Annotated[list[Path], typer.Argument(help="run dirs / trees of runs (runs/, bench/out/<battery>)")],
    md: Annotated[Path | None, typer.Option("--md", help="also write the full markdown report here")] = None,
    recheck: Annotated[bool, typer.Option("--recheck", help="re-price every call with today's table")] = False,
    limit: Annotated[int, typer.Option("--limit", help="rows in the per-run table")] = 20,
    per_run: Annotated[bool, typer.Option("--per-run/--no-per-run")] = True,
) -> None:
    """Cost breakdown: per stage, per role, per model, waste, $ per passing artifact."""
    from codeverse.cost import audit_runs
    from codeverse.cost.report import console as text_report
    from codeverse.cost.report import markdown, runs_table

    audit = audit_runs(paths, recheck=recheck)
    if not audit.runs:
        raise typer.BadParameter(f"no runs with a record.json under {', '.join(str(p) for p in paths)}")
    console.print(text_report(audit), soft_wrap=True)
    if per_run:
        console.print("")
        console.print(runs_table(audit, limit=limit), soft_wrap=True)
    if recheck:
        drift = audit.total_usd - audit.recorded_usd
        warn(f"re-priced total ${audit.total_usd:.4f} vs recorded ${audit.recorded_usd:.4f} ({drift:+.4f})")
        for run in audit.runs:
            for note in run.notes:
                console.print(f"  {run.run}: {note}")
    if md:
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(markdown(audit, title=f"Cost audit — {', '.join(str(p) for p in paths)}"))
        ok(f"wrote {md}")


@cost_app.command("prices")
def prices(
    provider: Annotated[str | None, typer.Option("--provider", help="gemini | anthropic | openai")] = None,
    unverified: Annotated[bool, typer.Option("--unverified", help="only rows that are not verified")] = False,
) -> None:
    """The USD price table with its provenance (source + checked date + status)."""
    from codeverse.models.pricing import PRICES, price_provenance

    rows = []
    for (prov, model) in sorted(PRICES):
        if provider and prov != provider:
            continue
        row = price_provenance(prov, model)
        if unverified and row.status == "verified":
            continue
        p = row.price
        assert p is not None
        rows.append((f"{prov}:{model}", f"{p.input:g}", f"{p.cached:g}", f"{p.output:g}",
                     f"{p.image_usd:g}" if p.image_usd else "-", row.status, row.checked,
                     (row.provenance.note if row.provenance else "")[:60]))
    header = ("model", "in/M", "cached/M", "out/M", "$/img", "status", "checked", "note")
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    console.print("  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True)), soft_wrap=True)
    for r in rows:
        console.print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)), soft_wrap=True)


@cost_app.command("estimate")
def estimate(
    model: Annotated[str, typer.Argument(help="model id, e.g. gemini:gemini-3.1-pro-preview")],
    input_tokens: Annotated[int, typer.Option("--in", help="prompt tokens")] = 0,
    output_tokens: Annotated[int, typer.Option("--out", help="expected output tokens")] = 0,
    cached: Annotated[int, typer.Option("--cached")] = 0,
    images: Annotated[int, typer.Option("--images", help="input images at 1024px")] = 0,
) -> None:
    """What one call would cost before you send it."""
    from codeverse.cost import estimate_call

    est = estimate_call(model, input_tokens=input_tokens, output_tokens=output_tokens,
                        cached_tokens=cached, n_images=images)
    console.print(est.line())
