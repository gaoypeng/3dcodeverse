"""In-process MCP client ↔ our stdio server (spawned as a subprocess)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from codeverse.spatial.mcp_server import build_context, encode_image, main, observation_content
from codeverse.spatial.registry import Observation, ToolContext


def test_build_context_infers_from_spec(stool_ctx: ToolContext) -> None:
    ctx = build_context(stool_ctx.workspace.root)
    assert ctx.language == "blender" and ctx.track == "static_object"
    ctx = build_context(stool_ctx.workspace.root, language="threejs", round_index=2)
    assert ctx.language == "threejs" and ctx.round_index == 2


def test_observation_content_blocks(tmp_path: Path) -> None:
    from PIL import Image

    big = tmp_path / "big.png"
    Image.new("RGB", (2048, 1024), (10, 20, 30)).save(big)
    obs = Observation(ok=True, text="hello", numbers={"a": 1}, images=[str(big), str(tmp_path / "missing.png")])
    blocks = observation_content(obs)
    assert blocks[0].type == "text" and blocks[0].text.startswith("hello\n{\"a\": 1}")
    assert len(blocks) == 2 and blocks[1].type == "image" and blocks[1].mime_type == "image/png"
    import base64
    import io

    im = Image.open(io.BytesIO(base64.b64decode(blocks[1].data)))
    assert max(im.size) == 1024
    assert encode_image(str(tmp_path / "missing.png")) is None


def test_cli_list(stool_ctx: ToolContext, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--workspace", str(stool_ctx.workspace.root), "--list"]) == 0
    data = json.loads(capsys.readouterr().out)
    names = {d["name"] for d in data}
    assert {"build", "measure", "render_views"} <= names and "shader_probe" not in names
    assert main(["--workspace", str(stool_ctx.workspace.root / "nope")]) == 2


def test_stdio_roundtrip(stool_ctx: ToolContext, tmp_path: Path) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    # `errlog` is handed straight to loop.subprocess_exec, which needs a real
    # fileno(); pytest's captured sys.stderr (stdio_client's default) has none
    # from anyio 4.14 on.  A file also keeps the server's stderr readable here.
    errlog = (tmp_path / "server.err").open("w")

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "codeverse.spatial.mcp_server", "--workspace", str(stool_ctx.workspace.root)])
        async with stdio_client(params, errlog=errlog) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            by_name = {t.name: t for t in tools.tools}
            assert "measure" in by_name and "check_connectivity" in by_name
            assert by_name["render_views"].input_schema["properties"]["views"]["type"] == "array"
            conn = await s.call_tool("check_connectivity", {})
            assert conn.content and conn.content[0].type == "text"
            assert "connectivity" in conn.content[0].text          # the gate's own header
            assert "Leg_3" in conn.content[0].text                 # the fixture's floating leg
            meas = await s.call_tool("measure", {})
            assert not meas.is_error and "Leg ×4" in meas.content[0].text
            sec = await s.call_tool("cross_section", {"axis": "y", "at": 0.5})
            assert [c.type for c in sec.content] == ["text", "image"]
            bad = await s.call_tool("measure", {"parts": 3})
            assert bad.is_error and "invalid arguments" in bad.content[0].text
            unknown = await s.call_tool("nope", {})
            assert unknown.is_error

    try:
        asyncio.run(asyncio.wait_for(run(), timeout=120))
    finally:
        errlog.close()
