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


def test_result_payload_is_bounded_by_outcome(tmp_path: Path) -> None:
    """Since the harness decides what is an error, no single result may hand the model an
    unbounded blob.  Text is capped here (an Observation built by hand never passes through
    ``observe``'s truncation), and the image budget shrinks with the outcome: gemini-cli's
    error path re-sends the WHOLE result as text, base64 images included — a 275 kB contact
    sheet arrives as ~261k prompt tokens instead of ~516 (docs/COST.md §30) — and its own
    40 000-char truncation does not fire for a multi-part MCP result."""
    from PIL import Image

    from codeverse.spatial.mcp_server import MAX_TEXT_CHARS, max_images_for

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


def test_image_bytes_are_bounded_not_only_the_count(tmp_path: Path) -> None:
    """The count and the 1024 px downscale bound PIXELS, not bytes: the largest recorded
    articulation sheet still encodes to ~395 k base64 characters, so four of them would be
    a bigger payload than the text cap the same result respects."""
    from PIL import Image

    from codeverse.spatial.mcp_server import MAX_IMAGE_BYTES, encode_image

    noisy = tmp_path / "noise.png"
    rnd = __import__("random").Random(0)
    img = Image.new("RGB", (1024, 1024))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(1024 * 1024)])
    img.save(noisy)
    one = encode_image(str(noisy))
    assert one is not None and len(one) > MAX_IMAGE_BYTES / 3  # a real sheet is this heavy
    blocks = observation_content(Observation(ok=True, text="t", images=[str(noisy)] * 4))
    images = [b for b in blocks if b.type == "image"]
    assert sum(len(b.data) for b in images) <= MAX_IMAGE_BYTES
    assert len(images) < 4  # the byte budget, not the count, is what stopped it


def test_is_error_is_failed_not_the_verdict(tmp_path: Path) -> None:
    """The one coupling every other bound assumes: a FAIL verdict is an ordinary result,
    so the vendor sends its images as images.  Re-couple this to ``not obs.ok`` and the
    base64-as-text blow-up of docs/COST.md §30 comes back."""
    import inspect

    from codeverse.spatial import mcp_server

    src = inspect.getsource(mcp_server.make_server)
    assert "is_error=obs.failed" in src and "is_error=not obs.ok" not in src


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
            # the gate ran and answered FAIL: a RESULT, not a protocol error the model retries
            assert not conn.is_error
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
            unknown = await s.call_tool("nope", {})
            assert unknown.is_error

    try:
        asyncio.run(asyncio.wait_for(run(), timeout=120))
    finally:
        errlog.close()
