"""In-process MCP client ↔ our stdio server (spawned as a subprocess)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from codeverse3d.spatial.mcp_server import encode_image, main, observation_content
from codeverse3d.spatial.registry import Observation, ToolContext


def test_observation_content_blocks(tmp_path: Path) -> None:

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


def test_the_byte_bound_keeps_the_sheet_drops_the_rest_and_says_so(tmp_path: Path) -> None:
    """The byte budget stops at the first image that does not fit (images[0] is the sheet) and says what did not come."""
    from codeverse3d.spatial.mcp_server import MAX_IMAGE_BYTES

    def noise(name: str, side: int) -> str:
        p = tmp_path / name
        Image.frombytes("RGB", (side, side), __import__("random").Random(len(name)).randbytes(side * side * 3)).save(p)
        return str(p)

    small, heavy = noise("small.png", 64), noise("heavy.png", 1024)
    blocks = observation_content(Observation(ok=True, text="t", images=[small, small]))
    assert sum(1 for b in blocks if b.type == "image") == 2 and "not attached" not in blocks[0].text
    blocks = observation_content(Observation(ok=True, text="t", images=[small, heavy, small, small]))
    images = [b for b in blocks if b.type == "image"]
    assert len(images) == 1 and sum(len(b.data) for b in images) <= MAX_IMAGE_BYTES
    assert "3 image(s) not attached" in blocks[0].text


def test_cli_list(stool_ctx: ToolContext, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--workspace", str(stool_ctx.workspace.root), "--list"]) == 0
    data = json.loads(capsys.readouterr().out)
    names = {d["name"] for d in data}
    assert {"build", "measure", "render_views"} <= names and "shader_probe" not in names
    assert main(["--workspace", str(stool_ctx.workspace.root / "nope")]) == 2


def test_stdio_roundtrip(stool_ctx: ToolContext, tmp_path: Path) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    # stdio_client's default errlog (pytest's captured stderr) has no fileno(): hand it a real file
    errlog = (tmp_path / "server.err").open("w")

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "codeverse3d.spatial.mcp_server", "--workspace", str(stool_ctx.workspace.root)])
        async with stdio_client(params, errlog=errlog) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            by_name = {t.name: t for t in tools.tools}
            assert "measure" in by_name and "check_connectivity" in by_name
            assert by_name["render_views"].input_schema["properties"]["views"]["type"] == "array"
            conn = await s.call_tool("check_connectivity", {})
            assert not conn.is_error     # the gate answered FAIL: a RESULT, not a protocol error
            assert conn.content[0].text.startswith("connectivity: FAIL")   # the verdict leads
            assert "Leg_3" in conn.content[0].text                 # the fixture's floating leg
            meas = await s.call_tool("measure", {})
            assert not meas.is_error and "Leg ×4" in meas.content[0].text
            sec = await s.call_tool("cross_section", {"axis": "y", "at": 0.5})
            assert [c.type for c in sec.content] == ["text", "image"]
            # ... and a tool that could NOT run is still an error, whichever way it broke
            bad = await s.call_tool("measure", {"parts": 3})
            assert bad.is_error and "invalid arguments" in bad.content[0].text
            unusable = await s.call_tool("isolate", {"part": "Nope"})
            assert unusable.is_error and "unknown part" in unusable.content[0].text

    try:
        asyncio.run(asyncio.wait_for(run(), timeout=120))
    finally:
        errlog.close()


def test_result_payload_is_bounded_by_outcome(tmp_path: Path) -> None:
    """Text is capped and the image budget shrinks with the outcome (gemini-cli re-sends an error as text, docs/COST.md §30)."""
    from PIL import Image

    from codeverse3d.spatial.mcp_server import MAX_TEXT_CHARS, max_images_for

    pngs = []
    for i in range(5):
        p = tmp_path / f"v{i}.png"
        Image.new("RGB", (32, 32), (10 * i, 0, 0)).save(p)
        pngs.append(str(p))
    huge = "x" * 200_000
    cases = ((Observation(ok=True, text=huge, images=pngs), 4),
             (Observation(ok=False, text=huge, images=pngs), 1),               # a FAIL verdict
             (Observation(ok=False, failed=True, text=huge, images=pngs), 0))  # the tool could not run
    for obs, n_images in cases:
        assert max_images_for(obs) == n_images
        blocks = observation_content(obs)
        assert len(blocks[0].text) <= MAX_TEXT_CHARS
        assert sum(1 for b in blocks if b.type == "image") == n_images


def test_an_oversized_sheet_is_downscaled_and_every_bound_holds_including_the_note(tmp_path: Path) -> None:
    """The sheet is re-encoded smaller until it fits (never dropped), and the note stays under the text cap."""
    from codeverse3d.spatial.mcp_server import MAX_IMAGE_BYTES, MAX_TEXT_CHARS

    heavy = tmp_path / "heavy.png"
    Image.effect_noise((1600, 4200), 90).convert("RGB").save(heavy)
    blocks = observation_content(Observation(ok=True, text="x" * 20_000,
                                             images=[str(heavy), str(heavy)]))
    assert len(encode_image(str(heavy))) > MAX_IMAGE_BYTES
    images = [b for b in blocks if b.type == "image"]
    assert len(images) == 1 and len(images[0].data) <= MAX_IMAGE_BYTES
    assert len(blocks[0].text) <= MAX_TEXT_CHARS and "re-encoded at" in blocks[0].text
