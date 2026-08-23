"""``c3v tools`` — list registry tools or run one (prints an Observation).

This is also the fallback path for agentic CLIs without MCP support: the
prompt tells them to shell out to ``c3v tools <name> --json '{...}' --workspace .``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from codeverse.cli import _common as C
from codeverse.cli._fmt import console, print_observation


def tools(
    name: Annotated[str, typer.Argument(help="'list' or a tool name")] = "list",
    args_json: Annotated[str, typer.Option("--json", help="arguments object as JSON")] = "{}",
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
    round_index: Annotated[int, typer.Option("--round")] = 0,
    as_json: Annotated[bool, typer.Option("--json-out", help="print the Observation as JSON")] = False,
    cards: Annotated[bool, typer.Option("--cards", help="(list) print prompt cards instead of a table")] = False,
) -> None:
    registry = C.lazy("codeverse.spatial.registry")
    if name == "list":
        defs = registry.list_tools()
        if cards:
            console.print(registry.tool_cards())
            return
        from rich.table import Table

        t = Table(title="spatial tools")
        for col in ("name", "cost", "tracks", "languages", "description"):
            t.add_column(col)
        for d in defs:
            t.add_row(d.name, d.cost_hint, ",".join(d.tracks) or "*", ",".join(d.languages) or "*", d.description)
        console.print(t)
        return
    try:
        tdef = registry.get_tool(name)
    except KeyError as e:
        raise C.CliError(str(e)) from e
    try:
        arguments = json.loads(args_json)
    except ValueError as e:
        raise C.CliError(f"--json is not valid JSON: {e}") from e
    ws_root = workspace or Path.cwd()
    ws = C.open_workspace(str(ws_root))
    spec = C.load_spec(ws)
    ctx = registry.ToolContext(workspace=ws, round_index=round_index, language=spec.language.value, track=spec.track.value)
    obs = tdef.call(ctx, arguments)
    print_observation(obs, as_json=as_json)
    if not obs.ok:
        raise typer.Exit(code=1)
