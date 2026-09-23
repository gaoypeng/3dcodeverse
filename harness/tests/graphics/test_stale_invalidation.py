"""GL builds must not leave the previous build's artifacts looking current (offline).

frames_sheet.png / preview.gif / metrics.json are written only on ok and were never
cleared; the MissingEntryFile early returns never reached GlHost._run's frames wipe.
Both wipes now happen at the top of build() (``_gl_common.invalidate_stale_outputs``)
plus a failure-path invalidation in ``finish_build``.
"""

from __future__ import annotations

import json

import pytest

import codeverse3d.spatial.tools  # noqa: F401 — registers the gl_* tools
from codeverse3d.languages.glsl_shader import GlslShaderRuntime
from codeverse3d.spatial.gl_render import GlResult
from codeverse3d.spatial.registry import ToolContext, get_tool
from codeverse3d.workspace import Workspace


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


@pytest.mark.parametrize("frag, error_type", [
    ("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(nope); }\n", "GlslCompileError"),
    # the MissingEntryFile early return never reaches the host: the hoisted wipe must still clear
    (None, "MissingEntryFile"),
])
def test_a_failed_build_invalidates_every_output_of_the_last_one(tmp_ws: Workspace, frag: str | None, error_type: str) -> None:
    if frag is not None:
        (tmp_ws.src / "shader.frag").write_text(frag)
    _seed_stale(tmp_ws)
    br = GlslShaderRuntime(host=_FailingHost()).build(tmp_ws, preview=False)
    assert not br.ok and br.error_type == error_type
    for name in ("frames_sheet.png", "preview.gif", "metrics.json", "gl_result.json"):
        assert not (tmp_ws.artifacts / name).exists(), name
    assert not list((tmp_ws.artifacts / "frames").glob("*.png"))
    assert json.loads((tmp_ws.artifacts / "build.json").read_text())["ok"] is False


def test_gl_tool_lint_fail_records_the_failed_build_status(tmp_ws: Workspace) -> None:
    """A lint refusal in the gl tools is the LATEST build status — the previous
    round's build.json (ok: true) must not stay readable as current."""
    (tmp_ws.src / "shader.frag").write_text(
        "#version 330 core\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }\n")
    tmp_ws.write_json(tmp_ws.artifacts / "build.json", {"ok": True})
    ctx = ToolContext(workspace=tmp_ws, language="glsl_shader", track="graphics")
    obs = get_tool("gl_probe").call(ctx, {})
    assert not obs.ok and "LINT FAILED" in obs.text
    last = json.loads((tmp_ws.artifacts / "build.json").read_text())
    assert last["ok"] is False and last["error_type"] == "LintError"
