"""A failed GL build must not leave the previous build's outputs looking current (offline)."""

from __future__ import annotations

import json

import codeverse3d.spatial.tools  # noqa: F401 — registers the gl_* tools
from codeverse3d.languages.glsl_shader import GlslShaderRuntime
from codeverse3d.spatial.gl_render import GlResult
from codeverse3d.workspace import Workspace


class _FailingHost:
    """Stands in for GlHost: every render is a compile failure."""

    def render_fragment_shader(self, frag_src: str, out_dir, **kw) -> GlResult:
        return GlResult(ok=False, mode="shader", stage="compile", error_type="Error",
                        error_message="0:3(5): error: `nope' undeclared")


def _seed_stale(ws: Workspace) -> None:
    for name in ("frames_sheet.png", "preview.gif", "metrics.json", "gl_result.json"):
        (ws.artifacts / name).write_bytes(b"stale")
    ws.write_json(ws.artifacts / "build.json", {"ok": True})
    frames = ws.artifacts / "frames"
    frames.mkdir(exist_ok=True)
    (frames / "f00_t000.00.png").write_bytes(b"stale frame")


def test_a_failed_build_invalidates_every_output_of_the_last_one(tmp_ws: Workspace) -> None:
    (tmp_ws.src / "shader.frag").write_text("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(nope); }\n")
    _seed_stale(tmp_ws)
    br = GlslShaderRuntime(host=_FailingHost()).build(tmp_ws, preview=False)
    assert not br.ok and br.error_type == "GlslCompileError"
    for name in ("frames_sheet.png", "preview.gif", "metrics.json", "gl_result.json"):
        assert not (tmp_ws.artifacts / name).exists(), name
    assert not list((tmp_ws.artifacts / "frames").glob("*.png"))
    assert json.loads((tmp_ws.artifacts / "build.json").read_text())["ok"] is False
