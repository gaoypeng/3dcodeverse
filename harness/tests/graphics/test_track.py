"""Offline: the graphics track with fakes (no GL, no network) — judge replay."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from codeverse3d.config import Settings
from codeverse3d.contracts.artifacts import BuildResult
from codeverse3d.contracts.common import Backends, Budget, Language, Track
from codeverse3d.contracts.spec import Spec
from codeverse3d.languages._gl_common import finish_build, judge_times
from codeverse3d.languages.glsl_shader import GlslShaderRuntime
from codeverse3d.spatial.gl_render import GlFrame, GlResult, gif_times
from codeverse3d.tracks import planner
from codeverse3d.tracks.graphics import GraphicsTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.fakes import FakeAgent, FakeChatModel, FakeJudge, FakeServices


class FakeGlRuntime(GlslShaderRuntime):
    """glsl_shader runtime without GL: synthetic frames through the real ``finish_build``."""

    entry_globs = ("src/shader.frag", "src/common.glsl")

    def build(self, ws: Workspace, *, timeout_s: int | None = None, **kw: Any) -> BuildResult:
        frames = []
        for i, t in enumerate(judge_times(8.0) + gif_times(8.0, 4)):
            p = ws.artifacts / "frames" / f"f{i:02d}_t{t:06.2f}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            x = np.linspace(0, 1, 64, dtype=np.float32)[None, :].repeat(36, 0)
            rgb = np.stack([0.5 + 0.5 * np.sin(6 * (x + 0.3 * t)), x * 0.5 + 0.2, 0.6 - 0.4 * x], -1)
            Image.fromarray((rgb * 255).astype(np.uint8), "RGB").save(p)
            frames.append(GlFrame(index=i, time=t, path=str(p), judge=i < 5))
        res = GlResult(ok=True, mode="shader", stage="render", renderer="fake-gl", frames=frames)
        return finish_build(ws, res, language="glsl_shader", census={"convention": "mainImage"})


def _writer(job, ws):
    return {"src/shader.frag": "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n"
                               f"  // {job.label} r{job.round}\n  fragColor = vec4(fragCoord / u_resolution.xy, 0.5, 1.0);\n}}\n"}


def test_a_replayed_round_reads_the_payload_the_in_run_judge_read(tmp_path):
    """``3dcodeverse judge`` / calibration rebuild the payload the in-run judge read (``judges.base.round_input``)."""
    from codeverse3d.cli._judge import build_judge_input
    from codeverse3d.record.record import load_record

    spec = Spec(id="g1", track=Track.GRAPHICS, language=Language.GLSL_SHADER, prompt="animated neon rain on a window",
                budget=Budget(max_rounds=1, max_minutes=10, max_repair_attempts=2),
                backends=Backends(planner="fake:planner", generator="fake:fake-model", judge="fake:judge"))
    ws = Workspace(tmp_path / "runs" / "replay")
    judge = FakeJudge(scores=(0.55, 0.72), targets=("SurfaceRipples",))
    track = GraphicsTrack(services=FakeServices(runtime_factory=lambda lang: FakeGlRuntime()), judge=judge, agent=FakeAgent(_writer),
                          planner_model=FakeChatModel(lambda req: planner.plan_example(Track.GRAPHICS)),
                          settings=Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache"), runtime=FakeGlRuntime())
    rec = track.run(spec, ws)
    replay = build_judge_input(ws, load_record(ws), rec.rounds[-1])
    for field in ("plan_summary", "extra_context", "acceptance", "round_index"):
        assert getattr(replay, field) == getattr(judge.calls[-1], field), field
    assert "Passes: PoolFloor (fullscreen)" in replay.plan_summary
