"""``3dcv bench run <battery.yaml> | report <dir>``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from codeverse.cli import _common as C
from codeverse.cli._fmt import console, ok

bench_app = typer.Typer(no_args_is_help=True)


@bench_app.command("run")
def run_cmd(
    battery: Annotated[Path, typer.Argument(help="bench/prompts/<name>.yaml")],
    out: Annotated[Path | None, typer.Option("--out", help="default bench/out/<battery name>")] = None,
    generator: Annotated[str | None, typer.Option("--generator")] = None,
    planner: Annotated[str | None, typer.Option("--planner")] = None,
    judge: Annotated[str | None, typer.Option("--judge", help="fixed judge model for the whole battery")] = None,
    parallel: Annotated[int, typer.Option("--parallel", min=1)] = 4,
    rounds: Annotated[int, typer.Option("--rounds", min=0)] = 4,
    max_usd: Annotated[float, typer.Option("--max-usd")] = 5.0,
    max_minutes: Annotated[float, typer.Option("--max-minutes", help="wall-clock budget per run; size it to the weather "
                                                                   "(RUNBOOK 7.x: 120 in a 503 storm, else runs burn the hour with no judged round)")] = 60.0,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    ids: Annotated[list[str] | None, typer.Option("--id", help="only these prompt ids")] = None,
    tiers: Annotated[list[str] | None, typer.Option("--tier")] = None,
    no_resume: Annotated[bool, typer.Option("--no-resume")] = False,
    redo_status: Annotated[str, typer.Option("--redo-status", help="comma list of recorded statuses to re-run, "
                                                                   "e.g. infra_failed once the provider recovers")] = "",
    report: Annotated[bool, typer.Option("--report/--no-report")] = True,
) -> None:
    """Run every prompt of a battery through its track (N parallel workers); resumable."""
    if not battery.is_file():
        raise C.CliError(f"battery not found: {battery}")
    b = C.import_bench()
    run_bench = C.lazy("bench.run_bench")
    opts = run_bench.BenchOptions(generator=generator, planner=planner, judge=judge, rounds=rounds, max_usd=max_usd, max_minutes=max_minutes,
                                 parallel=parallel, limit=limit, ids=ids or [], tiers=tiers or [], resume=not no_resume,
                                 redo_status=[x for x in redo_status.split(",") if x])
    out_dir = out or (C.REPO_ROOT / "bench" / "out" / battery.stem)
    console.print(f"battery={battery} out={out_dir} generator={generator or 'default'} judge={judge or 'default'}")

    def _on(res) -> None:
        console.print(f"  [{res.status}] {res.id}: baseline={res.score_baseline} final={res.score_final} "
                      f"rounds={res.rounds} ${res.cost_usd:.2f} {res.minutes:.1f}min" + (f" [red]{res.errors[:80]}[/red]" if res.errors else ""))

    results = run_bench.run_battery(battery, out_dir, opts, on_result=_on)
    ok(f"{len(results)} results → {out_dir / 'results.csv'}")
    if report:
        rep = C.lazy("bench.report").build_report(out_dir)
        console.print(rep.markdown)
        ok(f"report → {out_dir / 'report.md'} / report.html")
    del b


@bench_app.command("report")
def report_cmd(out_dir: Annotated[Path, typer.Argument()]) -> None:
    """Aggregate results.jsonl → report.md + report.html (gallery of contact sheets)."""
    C.import_bench()
    rep = C.lazy("bench.report").build_report(out_dir)
    console.print(rep.markdown)
    ok(f"report → {out_dir / 'report.md'} / report.html")
