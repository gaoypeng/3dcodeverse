"""GL builds must not leave the previous build's artifacts looking current (offline).

frames_sheet.png / preview.gif / metrics.json are written only on ok and were never
cleared; the MissingEntry early returns never reached GlHost._run's frames wipe.
Both wipes now happen at the top of build() (``_gl_common.invalidate_stale_outputs``)
plus a failure-path invalidation in ``finish_build``.
"""

from __future__ import annotations

import json

import codeverse.spatial.tools  # noqa: F401 — registers the gl_* tools
from codeverse.languages.glsl_shader import GlslShaderRuntime
from codeverse.spatial.gl_render import GlResult
from codeverse.spatial.registry import ToolContext, get_tool
from codeverse.workspace import Workspace


class _FailingHost:
    """Stands in for GlHost: every render is a compile failure."""

    def render_fragment_shader(self, frag_src: str, out_dir, **kw) -> GlResult:
        return GlResult(ok=False, mode="shader", stage="compile", error_type="Error",
                        error_message="0:3(5): error: `nope' undeclared")


def _seed_stale(ws: Workspace) -> None:
    for name in ("frames_sheet.png", "preview.gif"):
        (ws.artifacts / name).write_bytes(b"stale")
    (ws.artifacts / "metrics.json").write_text('{"stale": true}')
    ws.write_json(ws.artifacts / "build.json", {"ok": True})
    frames = ws.artifacts / "frames"
    frames.mkdir(exist_ok=True)
    (frames / "f00_t000.00.png").write_bytes(b"stale frame")
    (ws.artifacts / "gl_result.json").write_text("{}")


def test_failed_build_invalidates_sheet_gif_metrics(tmp_ws: Workspace) -> None:
    (tmp_ws.src / "shader.frag").write_text(
        "void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(nope); }\n")
    _seed_stale(tmp_ws)
    br = GlslShaderRuntime(host=_FailingHost()).build(tmp_ws, preview=False)
    assert not br.ok and br.error_type == "GlslCompileError"
    for name in ("frames_sheet.png", "preview.gif", "metrics.json", "gl_result.json"):
        assert not (tmp_ws.artifacts / name).exists(), name
    assert not list((tmp_ws.artifacts / "frames").glob("*.png"))
    assert json.loads((tmp_ws.artifacts / "build.json").read_text())["ok"] is False


def test_missing_entry_clears_frames_and_result(tmp_ws: Workspace) -> None:
    """The MissingEntry early return never reaches the host — the hoisted wipe must
    still clear frames/ + gl_result.json (and the sheet/gif/metrics trio)."""
    _seed_stale(tmp_ws)
    br = GlslShaderRuntime(host=_FailingHost()).build(tmp_ws, preview=False)
    assert not br.ok and br.error_type == "MissingEntry"
    assert not list((tmp_ws.artifacts / "frames").glob("*.png"))
    for name in ("gl_result.json", "frames_sheet.png", "preview.gif", "metrics.json"):
        assert not (tmp_ws.artifacts / name).exists(), name
    assert json.loads((tmp_ws.artifacts / "build.json").read_text())["ok"] is False


def test_gl_tool_lint_fail_records_failed_build_last(tmp_ws: Workspace) -> None:
    """A lint refusal in the gl tools is the LATEST build status — the previous
    round's build_last.json (ok: true) must not stay readable as current."""
    (tmp_ws.src / "shader.frag").write_text(
        "#version 330 core\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }\n")
    tmp_ws.write_json(tmp_ws.artifacts / "build_last.json", {"ok": True})
    ctx = ToolContext(workspace=tmp_ws, language="glsl_shader", track="graphics")
    obs = get_tool("gl_probe").call(ctx, {})
    assert not obs.ok and "LINT FAILED" in obs.text
    last = json.loads((tmp_ws.artifacts / "build_last.json").read_text())
    assert last["ok"] is False and last["error_type"] == "LintError"
