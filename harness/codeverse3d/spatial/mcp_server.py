"""stdio MCP server exposing the spatial tool registry to coding agents.

    python -m codeverse3d.spatial.mcp_server --workspace <ws> [--track X] [--language Y] [--round N]

One MCP tool per registry tool; a result is one bounded text block plus bounded base64 PNG images. ``is_error`` means the tool could not run, never a FAIL verdict. Track / language default to ``<ws>/spec.json``.
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

from codeverse3d.spatial.observe import fmt_numbers, truncate
from codeverse3d.spatial.registry import Observation, ToolContext, list_tools
from codeverse3d.spatial.tool_common import spec_dict
from codeverse3d.workspace import Workspace

MAX_IMAGES = 4
MAX_IMAGE_SIDE = 1024
MAX_NUMBERS_CHARS = 1200
#: ceiling on the text block of one result (text + numbers)
MAX_TEXT_CHARS = 6000
#: ceiling on the encoded image bytes of one result; an over-budget sheet is downscaled, not dropped
MAX_IMAGE_BYTES = 400_000
#: long sides tried when the first image does not fit
IMAGE_FALLBACK_SIDES = (768, 512, 384)
SERVER_NAME = "3dcode"


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


def max_images_for(obs: Observation) -> int:
    """Image budget: none for a failed call (vendor CLIs stringify ``is_error`` results, base64 included), one for FAIL, ``MAX_IMAGES`` for a pass."""
    if obs.failed:
        return 0
    return MAX_IMAGES if obs.ok else 1


def _fit_first(path: str, budget: int) -> tuple[str | None, int]:
    """The first image (the contact sheet), re-encoded smaller until it fits ``budget``: ``(base64, long side)`` or ``(None, 0)``."""
    b64 = encode_image(path)
    if b64 is not None and len(b64) <= budget:
        return b64, MAX_IMAGE_SIDE
    for side in IMAGE_FALLBACK_SIDES:
        smaller = encode_image(path, max_side=side)
        if smaller is not None and len(smaller) <= budget:
            return smaller, side
    return None, 0


def observation_content(obs: Observation) -> list[Any]:
    """Observation → bounded MCP content blocks (text first, then images)."""
    from mcp import types

    text = obs.text
    if obs.numbers:
        nums = json.dumps(obs.numbers, default=str)
        text += "\n" + (nums if len(nums) <= MAX_NUMBERS_CHARS else fmt_numbers(obs.numbers))
    # the slice makes the ceiling hard: truncate's omitted-marker overshoots its budget
    blocks: list[Any] = [types.TextContent(type="text", text=truncate(text, MAX_TEXT_CHARS)[:MAX_TEXT_CHARS])]
    # images[0] is the contact sheet; later images repeat it per view, so the budget stops at
    # the first that does not fit.  Dropped images are always said in the text.
    wanted = obs.images[:max_images_for(obs)]
    kept, spent, dropped, shrunk = [], 0, 0, 0
    for i, p in enumerate(wanted):
        b64 = encode_image(p)
        if i == 0 and b64 is not None and len(b64) > MAX_IMAGE_BYTES:
            b64, side = _fit_first(p, MAX_IMAGE_BYTES)
            shrunk = side
        if b64 is None:
            dropped += 1
            continue
        if spent + len(b64) > MAX_IMAGE_BYTES:
            dropped += len(wanted) - len(kept) - dropped
            break
        spent += len(b64)
        kept.append(b64)
    notes = []
    if shrunk:
        notes.append(f"[the sheet was re-encoded at {shrunk} px to fit the "
                     f"{MAX_IMAGE_BYTES}-byte payload bound]")
    if dropped:
        notes.append(f"[{dropped} image(s) not attached: over the {MAX_IMAGE_BYTES}-byte "
                     "payload bound or unreadable; the files are in the workspace]")
    if notes:
        # the note takes room from the body so the text ceiling holds
        tail = "\n" + "\n".join(notes)
        body = blocks[0].text[:max(0, MAX_TEXT_CHARS - len(tail))]
        blocks[0] = types.TextContent(type="text", text=body + tail)
    for b64 in kept:
        blocks.append(types.ImageContent(type="image", data=b64, mime_type="image/png"))
    return blocks


async def call_tool(ctx: ToolContext, defs: dict[str, Any], params: Any) -> Any:
    """One MCP tool call → ``CallToolResult``; ``is_error`` is ``Observation.failed``, not ``not ok`` (vendors retry MCP errors)."""
    from mcp import types

    tdef = defs.get(params.name)
    if tdef is None:
        return types.CallToolResult(
            content=[types.TextContent(type="text",
                                       text=f"unknown tool {params.name!r}; known: {sorted(defs)}")],
            is_error=True)
    obs = await asyncio.to_thread(tdef.call, ctx, params.arguments or {})
    return types.CallToolResult(content=observation_content(obs), is_error=obs.failed)


def make_server(ctx: ToolContext):
    """Low-level MCP ``Server`` wired to the registry for ``ctx``."""
    from mcp import types
    from mcp.server.lowlevel import Server

    defs = {t.name: t for t in list_tools(track=ctx.track, language=ctx.language)}

    async def on_list_tools(_req_ctx: Any, _params: Any) -> types.ListToolsResult:
        tools = [types.Tool(name=t.name, description=f"({t.cost_hint}) {t.description}", input_schema=t.schema())
                 for t in defs.values()]
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(_req_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await call_tool(ctx, defs, params)

    return Server(SERVER_NAME, version="0.1.0",
                  instructions="3dcodeverse spatial tools: build, measure, render and check the 3D object in this workspace.",
                  on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def serve_stdio(ctx: ToolContext) -> None:
    from mcp.server.stdio import stdio_server

    server = make_server(ctx)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="codeverse3d.spatial.mcp_server", description=__doc__)
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
        print(json.dumps([{"name": t.name, "description": t.description, "schema": t.schema()} for t in list_tools(track=ctx.track, language=ctx.language)], indent=1))
        return 0
    asyncio.run(serve_stdio(ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
