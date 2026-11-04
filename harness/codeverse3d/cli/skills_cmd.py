"""``3dcode skills`` — list and validate the skill library, and report how often agents read it."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Annotated

import typer

from codeverse3d.cli._common import console, err_console
from codeverse3d.proc import read_jsonl_lenient

skills_app = typer.Typer(invoke_without_command=True, help="The skill library: list / show / validate / read-rate report.")


@skills_app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@skills_app.command("list")
def list_skills(
    routes: Annotated[bool, typer.Option("--routes", help="show the rules that can attach each skill")] = False,
) -> None:
    """Every bundle in the library, with its evidence label, body size and its one claim."""
    from rich.table import Table

    from codeverse3d.addons.skill_targets import target_for
    from codeverse3d.skills import iter_skills
    from codeverse3d.skills.registry import ROUTED_SKILLS, ROUTES

    # the base library, then every shipped variant's overlay (rows labelled by variant)
    roots = _roots()
    found = [(root, s) for root in roots for s in iter_skills(root.path)]
    overlay_rows = {root.variant: _overlay_routes(root.variant) for root in roots if root.variant}
    t = Table(title="3dcode skills")
    for col in ("name", "variant", "evidence", "verified", "lines", "~tokens", "refs", "target", "dir",
                *(["routes"] if routes else [])):
        t.add_column(col)
    for root, s in found:
        tg = target_for(s.name)
        row = [s.name, root.variant, s.evidence, s.verified, str(s.body_lines), str(s.body_tokens),
               str(len(s.references)), tg.metric if tg else "[red]none[/]",
               ("" if not tg else tg.direction if tg.measurable else f"{tg.direction} ?")]
        if routes:
            table = (*ROUTES, *overlay_rows.get(root.variant, ()))
            row.append(",".join(r.rule for r in table if r.skill == s.name))
        t.add_row(*row)
    console.print(t)
    missing = [n for n in ROUTED_SKILLS if n not in {s.name for root, s in found if not root.variant}]
    if missing:
        console.print(f"[yellow]{len(missing)} routed skill(s) have no bundle yet:[/] {', '.join(missing)}")


@skills_app.command("show")
def show_skill(name: Annotated[str, typer.Argument(help="skill name")]) -> None:
    """Print one bundle's frontmatter and body."""
    from codeverse3d.skills import load_skill
    from codeverse3d.skills.model import SkillError

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
    from codeverse3d.addons.skill_targets import check_claims, check_prompt_claims
    from codeverse3d.skills import bundle_dirs, validate_bundle
    from codeverse3d.skills.model import SkillError, parse_skill
    from codeverse3d.skills.registry import ROUTED_SKILLS

    # every root a run can route from, so a malformed overlay bundle fails here as `<variant>/<bundle>`
    roots = _roots()
    total = bad = tables_bad = 0
    base_names: set[str] = set()
    unshipped: list[str] = []
    for root in roots:
        dirs = bundle_dirs(root.path)
        total += len(dirs)
        for d in dirs:
            issues = validate_bundle(d)
            with contextlib.suppress(SkillError):
                issues += check_claims(parse_skill(d))
            if issues:
                bad += 1
                console.print(f"[red]FAIL[/] {root.label(d.name)}")
                for i in issues:
                    console.print(f"      - {i}")
            else:
                console.print(f"[green]ok[/]   {root.label(d.name)}")
        if not root.variant:
            base_names = {d.name for d in dirs}
            continue
        table_bad, orphans = _check_table(root, base_names | {d.name for d in dirs})
        tables_bad += table_bad
        if orphans:
            style = "red" if strict else "yellow"
            console.print(f"[{style}]{root.variant}/routes.toml: {len(orphans)} row(s) route a bundle "
                          f"nobody ships:[/] {', '.join(orphans)}")
            unshipped += orphans
    prompt_issues = check_prompt_claims()
    for i in prompt_issues:
        console.print(f"[red]FAIL[/] prompts/_claims.toml - {i}")
    missing = [n for n in ROUTED_SKILLS if n not in base_names]
    if missing:
        style = "red" if strict else "yellow"
        console.print(f"[{style}]{len(missing)} routed skill(s) have no bundle:[/] {', '.join(missing)}")
    console.print(f"\n{total - bad}/{total} bundles valid"
                  + (f", {tables_bad} routes.toml invalid" if tables_bad else ""))
    if bad or tables_bad or prompt_issues or (strict and (missing or unshipped)):
        raise typer.Exit(code=1)


def _roots() -> list:
    """Every root the library ships; a ``variant.toml`` that does not parse is reported and exits 1."""
    from codeverse3d.skills import shipped_roots

    try:
        return shipped_roots()
    except ValueError as e:
        err_console.print(f"[red]FAIL[/] variants - {e}")
        raise typer.Exit(code=1) from e


def _overlay_routes(variant: str) -> tuple:
    """A variant's ``[[route]]`` rows for the ``--routes`` column; an invalid table lists as empty."""
    from codeverse3d.skills import variants_table

    try:
        return variants_table(variant).routes
    except ValueError as e:
        err_console.print(f"[yellow]{variant}/routes.toml rejected:[/] {e}")
        return ()


def _check_table(root, available: set[str]) -> tuple[int, list[str]]:
    """One overlay's ``routes.toml``: ``(1 if invalid else 0, rows routing a skill no root ships)``."""
    from codeverse3d.skills import variants_table

    try:
        table = variants_table(root.variant)
    except ValueError as e:
        console.print(f"[red]FAIL[/] {root.variant}/routes.toml - {e}")
        return 1, []
    return 0, [f"{r.rule} -> {r.skill}" for r in table.routes if r.skill not in available]


@skills_app.command("report")
def report(
    runs_dir: Annotated[Path, typer.Argument(help="a runs root, a bench out dir, or one workspace")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Deep-read rate per skill and per backend; atime sessions that also "read" the control are excluded."""
    from rich.table import Table

    from codeverse3d.contracts.run import SkillsUsage
    from codeverse3d.record.record import unique_files
    from codeverse3d.skills.telemetry import SKILLS_FILE, TELEMETRY_DIR

    rows: list[dict] = []
    for p in unique_files(runs_dir, SKILLS_FILE, subruns=True):
        if p.parent.name == TELEMETRY_DIR:
            rows.extend(read_jsonl_lenient(p))
    if not rows:
        err_console.print(f"[yellow]no telemetry/skills.jsonl under {runs_dir}[/] (was C3D_SKILLS=0?)")
        raise typer.Exit(code=1)

    per: dict[tuple[str, str], list[int]] = {}

    def _blind(r: dict) -> bool:
        u = SkillsUsage.model_validate(r)
        return bool(u.listed) and u.deep_read_rate is None

    blind = sum(1 for r in rows if _blind(r))
    exact = sum(1 for r in rows if r.get("evidence") == "transcript")
    indiscriminate = sum(1 for r in rows if r.get("evidence") == "transcript" and r.get("control_read"))
    no_control = sum(1 for r in rows if not r.get("control_present"))
    for r in rows:
        if _blind(r):
            continue
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
            "transcript_sessions": exact, "control_opened_by_agent_sessions": indiscriminate,
            "skills": [
                {"skill": k[0], "backend": k[1], "listed": v[0], "surfaced": v[1], "deep": v[2],
                 "deep_read_rate": round(v[2] / v[0], 3) if v[0] else None}
                for k, v in sorted(per.items())]}))
        return
    scored = len(rows) - blind
    t = Table(title=f"skill read rate ({scored} of {len(rows)} sessions, {exact} from the CLI's own "
                    f"tool calls; {blind} blind — the atime control was read too)")
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
    if indiscriminate:
        err_console.print(
            f"[yellow]{indiscriminate} session(s) opened the control bundle themselves[/] (tool trace): "
            f"their agent reads skills whether or not the description matches.")
    if no_control:
        err_console.print(
            f"[yellow]{no_control} session(s) predate the control bundle[/]: their rates are an upper bound.")
    if not per:
        err_console.print("[red]every session was blind[/] — the read rate is unmeasured, not 0 and not 100%.")


__all__ = ["skills_app"]
