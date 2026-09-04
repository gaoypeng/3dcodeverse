"""stdio MCP server exposing the spatial tool registry to coding agents.

    python -m codeverse.spatial.mcp_server --workspace <ws> [--track X] [--language Y] [--round N]

One MCP tool per registry tool (name, description, JSON schema from
``ToolDef.schema()``).  Results are content blocks: one ``TextContent`` with the
observation text (+ compact numbers when small) and one ``ImageContent`` per
observation image (base64 PNG, long side ≤ 1024 px, at most ``MAX_IMAGES``).
``is_error`` is ``Observation.failed`` (the tool could not run) — never a
negative verdict, which is an ordinary result whose text leads with FAIL.
Every result is bounded here — ``MAX_TEXT_CHARS`` of text, at most
:func:`max_images_for` images, and ``MAX_IMAGE_BYTES`` of encoded image data across
them — because this is the last place the harness owns before the payload becomes
the vendor's prompt.
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

from codeverse.spatial.observe import fmt_numbers, truncate
from codeverse.spatial.registry import Observation, ToolContext, list_tools
from codeverse.spatial.tool_common import spec_dict
from codeverse.workspace import Workspace

MAX_IMAGES = 4
MAX_IMAGE_SIDE = 1024
MAX_NUMBERS_CHARS = 1200
#: hard ceiling on the text block of ONE result (text + numbers), twice the largest
#: per-tool limit in ``observe`` — an Observation built by hand (``joint_sweep``,
#: ``compare_reference``) never goes through those, and nothing may hand the model an
#: unbounded payload from here.
MAX_TEXT_CHARS = 6000
#: ceiling on the encoded image data of ONE result.  The count and the 1024 px long side
#: bound pixels, not bytes: the largest recorded articulation sheet (1,966,609 B,
#: 1176x3350) still encodes to 394,988 base64 characters after the downscale, so without
#: this a legal result could hand the model ~280 k tokens of image.  That leaves only ~1 %
#: of headroom, so a slightly taller sheet is DOWNSCALED to fit rather than dropped
#: (``_fit_first``): shipping no picture at all is the worse failure.
MAX_IMAGE_BYTES = 400_000
#: long sides tried, in order, when the first image does not fit the byte bound
IMAGE_FALLBACK_SIDES = (768, 512, 384)
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


def max_images_for(obs: Observation) -> int:
    """Image budget for ONE result, by outcome: none for a call that failed, one for a
    FAIL verdict, ``MAX_IMAGES`` for a pass.

    A failed call ships none because the vendor CLI stringifies an ``is_error`` result
    whole, base64 included — 275 kB of contact sheet arrives as ~261 k prompt tokens of
    text (docs/COST.md §30, which also has the one-image rule's reasoning).
    """
    if obs.failed:
        return 0
    return MAX_IMAGES if obs.ok else 1


def _fit_first(path: str, budget: int) -> tuple[str | None, int]:
    """The first image, re-encoded smaller until it fits ``budget``.

    Returns ``(base64, long side used)`` or ``(None, 0)``.  ``images[0]`` is the contact
    sheet — the one picture the round is about — and the byte bound has ~1 % of headroom
    over the largest recorded sheet, so "over budget" means "a bit taller than last time",
    not "unreasonable"."""
    b64 = encode_image(path)
    if b64 is not None and len(b64) <= budget:
        return b64, MAX_IMAGE_SIDE
    for side in IMAGE_FALLBACK_SIDES:
        smaller = encode_image(path, max_side=side)
        if smaller is not None and len(smaller) <= budget:
            return smaller, side
    return None, 0


def observation_content(obs: Observation) -> list[Any]:
    """Observation → MCP content blocks (text first, then images), both bounded."""
    from mcp import types

    text = obs.text
    if obs.numbers:
        nums = json.dumps(obs.numbers, default=str)
        text += "\n" + (nums if len(nums) <= MAX_NUMBERS_CHARS else fmt_numbers(obs.numbers))
    # the slice makes the ceiling hard: truncate's "N chars omitted" marker overshoots its
    # own budget by a few characters, and this is the last bound before the vendor's prompt
    blocks: list[Any] = [types.TextContent(type="text", text=truncate(text, MAX_TEXT_CHARS)[:MAX_TEXT_CHARS])]
    # images[0] is the contact sheet (image_budget keeps it first) and every later image is
    # a per-view repeat of it, so the budget STOPS at the first one that does not fit rather
    # than skipping it: dropping the sheet and shipping three views of the same thing would
    # be the worst of both.  Whatever is dropped is said in the text — a FAIL verdict that
    # silently shipped no picture is the case this guards.
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
        # inside the ceiling, not appended past it: this is the last bound before the
        # vendor's prompt, so the note takes room from the body rather than adding to it
        tail = "\n" + "\n".join(notes)
        body = blocks[0].text[:max(0, MAX_TEXT_CHARS - len(tail))]
        blocks[0] = types.TextContent(type="text", text=body + tail)
    for b64 in kept:
        blocks.append(types.ImageContent(type="image", data=b64, mime_type="image/png"))
    return blocks


async def call_tool(ctx: ToolContext, defs: dict[str, Any], params: Any) -> Any:
    """One MCP tool call → a ``CallToolResult``.

    Module level, not a closure inside :func:`make_server`, so the coupling below can be
    driven by a test instead of grepped for: ``is_error`` is ``Observation.failed``, NOT
    ``not ok``.  A tool that RAN and answered FAIL is a result the model must read; an MCP
    error is a call the vendor retries instead (a mean 119k prompt tokens, $0.030 blended)
    — and, on gemini-cli, one whose images it re-sends as base64 TEXT.  The FAIL verdict
    leads the text.
    """
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
        tools = [types.Tool(name=t.name, description=f"({t.cost_hint}) {t.describe()}", input_schema=t.schema())
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
        print(json.dumps([{"name": t.name, "description": t.describe(), "schema": t.schema()} for t in list_tools(track=ctx.track, language=ctx.language)], indent=1))
        return 0
    asyncio.run(serve_stdio(ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
