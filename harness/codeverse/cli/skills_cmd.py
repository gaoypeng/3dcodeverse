"""``3dcv skills`` — look at the library, validate it, and read the read-rate.

``report`` is the command that matters.  The whole system exists because a tool that
exists is not a tool that gets used (``read_cookbook``: 0 of 16 zone sessions), so the
first question about any battery is not "did the score move" but "did anyone open the
file".  Everything else here is plumbing for that answer.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Annotated

import typer

from codeverse.cli._common import console, err_console
from codeverse.proc import read_jsonl_lenient

skills_app = typer.Typer(invoke_without_command=True, help="The skill library: list / show / validate / read-rate report.")


@skills_app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@skills_app.command("list")
def list_skills(
    routes: Annotated[bool, typer.Option("--routes", help="show the rules that can attach each skill")] = False,
) -> None:
    """Every bundle in the library, with its evidence label, body size and its ONE claim.

    ``target`` is the deterministic quantity the bundle says it moves (docs/SKILLS_LEDGER.md);
    ``dir`` is the direction that counts as an improvement, and a ``?`` marks a bundle whose
    claim no deterministic instrument can see.  Read them out with
    ``python bench/skill_targets.py bench/out``.
    """
    from rich.table import Table

    from codeverse.skills import iter_skills
    from codeverse.skills.registry import ROUTED_SKILLS, ROUTES
    from codeverse.skills.targets import target_for

    found = list(iter_skills())
    t = Table(title="3dcv skills")
    for col in ("name", "evidence", "verified", "lines", "~tokens", "refs", "target", "dir",
                *(["routes"] if routes else [])):
        t.add_column(col)
    for s in found:
        tg = target_for(s.name)
        row = [s.name, s.evidence, s.verified, str(s.body_lines), str(s.body_tokens), str(len(s.references)),
               tg.metric if tg else "[red]none[/]",
               ("" if not tg else tg.direction if tg.measurable else f"{tg.direction} ?")]
        if routes:
            row.append(",".join(r.rule for r in ROUTES if r.skill == s.name))
        t.add_row(*row)
    console.print(t)
    missing = [n for n in ROUTED_SKILLS if n not in {s.name for s in found}]
    if missing:
        console.print(f"[yellow]{len(missing)} routed skill(s) have no bundle yet:[/] {', '.join(missing)}")


@skills_app.command("show")
def show_skill(name: Annotated[str, typer.Argument(help="skill name")]) -> None:
    """Print one bundle's frontmatter and body."""
    from codeverse.skills import load_skill
    from codeverse.skills.model import SkillError

    try:
        s = load_skill(name)
    except SkillError as e:
        err_console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e
    console.print(f"[bold]{s.name}[/]  ({s.body_lines} lines, ~{s.body_tokens} tokens, evidence={s.evidence})")
    console.print(f"[dim]{s.path}[/]\n")
    console.print(s.description + "\n")
    console.print(s.body)


@skills_app.command("validate")
def validate(
    strict: Annotated[bool, typer.Option("--strict", help="also fail on a routed skill with no bundle")] = False,
) -> None:
    """Check every bundle against the open spec and our budget/evidence rules."""
    from codeverse.skills import bundle_dirs, validate_bundle
    from codeverse.skills.model import SkillError, parse_skill
    from codeverse.skills.registry import ROUTED_SKILLS
    from codeverse.skills.targets import check_claims

    dirs = bundle_dirs()
    bad = 0
    for d in dirs:
        issues = validate_bundle(d)
        with contextlib.suppress(SkillError):   # a parse failure is already reported above
            issues += check_claims(parse_skill(d))
        if issues:
            bad += 1
            console.print(f"[red]FAIL[/] {d.name}")
            for i in issues:
                console.print(f"      - {i}")
        else:
            console.print(f"[green]ok[/]   {d.name}")
    missing = [n for n in ROUTED_SKILLS if n not in {d.name for d in dirs}]
    if missing:
        style = "red" if strict else "yellow"
        console.print(f"[{style}]{len(missing)} routed skill(s) have no bundle:[/] {', '.join(missing)}")
    console.print(f"\n{len(dirs) - bad}/{len(dirs)} bundles valid")
    if bad or (strict and missing):
        raise typer.Exit(code=1)


@skills_app.command("report")
def report(
    runs_dir: Annotated[Path, typer.Argument(help="a runs root, a bench out dir, or one workspace")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Deep-read rate per skill and per backend — the number that replaces "0 of 16".

    Target from the design: >= 60% deep-read rate for a CLI backend.  A skill
    under 20% over 20 sessions is merged or deleted.

    Sessions whose CONTROL bundle was also "read" are counted separately and excluded from
    the rate: git's own diff and every CLI's skill activation both bump atime, so in those
    sessions the probe saw nothing it can attribute to the agent (docs/SKILLS.md section 4).
    """
    from rich.table import Table

    rows: list[dict] = []
    for p in sorted(Path(runs_dir).rglob("telemetry/skills.jsonl")):
        rows.extend(read_jsonl_lenient(p))
    if not rows:
        err_console.print(f"[yellow]no telemetry/skills.jsonl under {runs_dir}[/] (was CV3D_SKILLS on?)")
        raise typer.Exit(code=1)

    per: dict[tuple[str, str], list[int]] = {}
    blind = sum(1 for r in rows if r.get("control_read"))
    no_control = sum(1 for r in rows if not r.get("control_present"))
    for r in rows:
        if r.get("control_read"):
            continue                      # the probe was blind here; counting it would lie
        backend = str(r.get("agent", "?")).split(":", 1)[0]
        reads = {x["name"]: x for x in r.get("reads", [])}
        for name in r.get("listed", []):
            cell = per.setdefault((name, backend), [0, 0, 0])
            cell[0] += 1
            got = reads.get(name) or {}
            cell[1] += 1 if got.get("surfaced") else 0
            cell[2] += 1 if got.get("deep") else 0
    if as_json:
        console.print_json(json.dumps({
            "sessions": len(rows), "control_read_sessions": blind, "no_control_sessions": no_control,
            "skills": [
                {"skill": k[0], "backend": k[1], "listed": v[0], "surfaced": v[1], "deep": v[2],
                 "deep_read_rate": round(v[2] / v[0], 3) if v[0] else None}
                for k, v in sorted(per.items())]}))
        return
    scored = len(rows) - blind
    t = Table(title=f"skill read rate ({scored} of {len(rows)} sessions; "
                    f"{blind} blind — the control was read too)")
    for col in ("skill", "backend", "listed", "surfaced", "deep", "deep rate", "verdict"):
        t.add_column(col)
    for (name, backend), (listed, surfaced, deep) in sorted(per.items()):
        rate = deep / listed if listed else 0.0
        target = 0.60
        verdict = "delete/merge" if (listed >= 20 and rate < 0.20) else ("ok" if rate >= target else "below target")
        t.add_row(name, backend, str(listed), str(surfaced), str(deep), f"{rate:.0%}", verdict)
    console.print(t)
    if blind:
        err_console.print(
            f"[yellow]{blind} of {len(rows)} sessions excluded[/]: the never-routed control bundle "
            f"was opened too, so nothing in them can be attributed to the agent.")
    if no_control:
        err_console.print(
            f"[yellow]{no_control} session(s) predate the control bundle[/]: their rates are an upper bound.")
    if not per:
        err_console.print("[red]every session was blind[/] — the read rate is unmeasured, not 0 and not 100%.")


__all__ = ["skills_app"]
