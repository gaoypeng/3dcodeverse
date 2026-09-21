"""``3dcode doctor`` — the typer shim over :mod:`codeverse.doctor`."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from codeverse.cli._common import console, doctor_table
from codeverse.doctor import run_doctor

doctor_app = typer.Typer(invoke_without_command=True)


@doctor_app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
    live: Annotated[bool, typer.Option("--live", help="make one tiny Gemini call")] = False,
    gpu: Annotated[bool, typer.Option("--gpu/--no-gpu", help="probe headless Chrome WebGL")] = True,
    skills: Annotated[bool, typer.Option("--skills", help="also check the skill library and its discovery wiring")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check python deps, Blender, node/three/puppeteer, keys, CLIs and MCP."""
    if ctx.invoked_subcommand:
        return
    rows = run_doctor(live=live, gpu=gpu, skills=skills)
    if as_json:
        console.print_json(json.dumps([{"check": c, "status": s, "detail": d} for c, s, d in rows]))
    else:
        console.print(doctor_table(rows))
    if any(s == "FAIL" for _, s, _ in rows):
        raise typer.Exit(code=1)
