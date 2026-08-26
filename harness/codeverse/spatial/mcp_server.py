"""stdio MCP server exposing the spatial tool registry to coding agents.

    python -m codeverse.spatial.mcp_server --workspace <ws> [--track X] [--language Y] [--round N]

One MCP tool per registry tool (name, description, JSON schema from
``ToolDef.schema()``).  Results are content blocks: one ``TextContent`` with the
observation text (+ compact numbers when small) and one ``ImageContent`` per
observation image (base64 PNG, long side ≤ 1024 px, at most ``MAX_IMAGES``).
Track / language default to ``<ws>/spec.json`` so agents need no flags.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from codeverse.spatial.observe import fmt_numbers
from codeverse.spatial.registry import Observation, ToolContext, ToolDef, list_tools
from codeverse.spatial.tool_common import spec_dict
from codeverse.workspace import Workspace

MAX_IMAGES = 4
MAX_IMAGE_SIDE = 1024
MAX_NUMBERS_CHARS = 1200
SERVER_NAME = "3dcv"


def build_context(workspace: Path, *, track: str = "", language: str = "", round_index: int = 0) -> ToolContext:
    """ToolContext for ``workspace``; track/language fall back to spec.json."""
    ctx = ToolContext(workspace=Workspace(workspace), round_index=round_index, language=language, track=track)
    if not track or not language:
        spec = spec_dict(ctx)
        ctx.track = track or str(spec.get("track", ""))
        ctx.language = language or str(spec.get("language", ""))
    return ctx


def encode_image(path: str, max_side: int = MAX_IMAGE_SIDE) -> str | None:
    """PNG → base64 (downscaled so the long side is ≤ ``max_side``); None if unreadable."""
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return None
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def observation_content(obs: Observation) -> list[Any]:
    """Observation → MCP content blocks (text first, then images)."""
    from mcp import types

    text = obs.text
    if obs.numbers:
        nums = json.dumps(obs.numbers, default=str)
        text += "\n" + (nums if len(nums) <= MAX_NUMBERS_CHARS else fmt_numbers(obs.numbers))
    blocks: list[Any] = [types.TextContent(type="text", text=text)]
    for p in obs.images[:MAX_IMAGES]:
        b64 = encode_image(p)
        if b64:
            blocks.append(types.ImageContent(type="image", data=b64, mime_type="image/png"))
    return blocks


def mcp_tools(ctx: ToolContext) -> list[ToolDef]:
    return list_tools(track=ctx.track, language=ctx.language)


def make_server(ctx: ToolContext):
    """Low-level MCP ``Server`` wired to the registry for ``ctx``."""
    from mcp import types
    from mcp.server.lowlevel import Server

    defs = {t.name: t for t in mcp_tools(ctx)}

    async def on_list_tools(_req_ctx: Any, _params: Any) -> types.ListToolsResult:
        tools = [types.Tool(name=t.name, description=f"({t.cost_hint}) {t.describe()}", input_schema=t.schema())
                 for t in defs.values()]
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(_req_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        tdef = defs.get(params.name)
        if tdef is None:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"unknown tool {params.name!r}; known: {sorted(defs)}")], is_error=True)
        obs = await asyncio.to_thread(tdef.call, ctx, params.arguments or {})
        return types.CallToolResult(content=observation_content(obs), is_error=not obs.ok)

    return Server(SERVER_NAME, version="0.1.0",
                  instructions="3dcodeverse spatial tools: build, measure, render and check the 3D object in this workspace.",
                  on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def serve_stdio(ctx: ToolContext) -> None:
    from mcp.server.stdio import stdio_server

    server = make_server(ctx)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="codeverse.spatial.mcp_server", description=__doc__)
    ap.add_argument("--workspace", required=True, help="run workspace directory")
    ap.add_argument("--track", default="")
    ap.add_argument("--language", default="")
    ap.add_argument("--round", type=int, default=0, dest="round_index")
    ap.add_argument("--list", action="store_true", help="print the tool list as JSON and exit")
    ns = ap.parse_args(argv)
    ws = Path(ns.workspace)
    if not ws.is_dir():
        print(f"workspace not found: {ws}", file=sys.stderr)
        return 2
    ctx = build_context(ws, track=ns.track, language=ns.language, round_index=ns.round_index)
    if ns.list:
        print(json.dumps([{"name": t.name, "description": t.describe(), "schema": t.schema()} for t in mcp_tools(ctx)], indent=1))
        return 0
    asyncio.run(serve_stdio(ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
