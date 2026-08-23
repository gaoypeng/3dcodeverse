"""Shared build plumbing for the two graphics languages (glsl_shader, opengl_python).

``finish_build`` turns a :class:`GlResult` into the canonical artifact set —
``artifacts/frames/*.png`` (written by the runner), ``artifacts/frames_sheet.png``,
``artifacts/preview.gif``, ``artifacts/metrics.json`` (frame stats + the
``gl_frames`` gate), ``artifacts/build.json`` — and a typed :class:`BuildResult`
(``glb_path`` is always None: the deliverable is code + frames).
``judge_times`` / ``preview_times`` derive the sampled times from the plan's duration.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.plan import GraphicsPlan
from codeverse.spatial.frame_stats import SequenceStats, frame_gate, sequence_stats
from codeverse.spatial.gl_render import GlResult, gif_times, write_contact_sheet, write_gif
from codeverse.workspace import Workspace

JUDGE_TIMES: tuple[float, ...] = (0.0, 1.0, 2.5, 4.0, 6.0)
SHEET_NAME = "frames_sheet.png"
GIF_NAME = "preview.gif"
METRICS_NAME = "metrics.json"
BUILD_JSON = "build.json"
DEFAULT_RESOLUTION: tuple[int, int] = (1280, 720)
MAX_PIXELS = 1920 * 1080


def load_plan(ws: Workspace) -> GraphicsPlan | None:
    p = ws.plan_path
    if not p.is_file():
        return None
    try:
        return GraphicsPlan.model_validate(json.loads(p.read_text()))
    except Exception:  # noqa: BLE001 — not a graphics plan (or invalid) → defaults
        return None


def resolution_for(plan: GraphicsPlan | None, *, fallback: tuple[int, int] = DEFAULT_RESOLUTION) -> tuple[int, int]:
    if plan is None:
        return fallback
    w, h = int(plan.resolution[0]), int(plan.resolution[1])
    if w < 64 or h < 64 or w * h > MAX_PIXELS:
        return fallback
    return w, h


def judge_times(duration_s: float | None) -> list[float]:
    """t ∈ {0, 1, 2.5, 4, 6} clipped to the loop duration (always ≥ 3 samples)."""
    d = float(duration_s or 8.0)
    ts = [t for t in JUDGE_TIMES if t <= d + 1e-9]
    if len(ts) < 3:
        ts = [round(i * d / 4, 3) for i in range(5)]
    return ts


def preview_times(duration_s: float | None, n: int = 12) -> list[float]:
    return gif_times(float(duration_s or 8.0), n)


def finish_build(ws: Workspace, res: GlResult, *, language: str, error_file: str = "", error_line: int | None = None,
                 error_message: str | None = None, census: dict | None = None, motion_expected: bool = True) -> BuildResult:
    """Write derived artifacts + build.json and return the BuildResult."""
    art = ws.artifacts
    art.mkdir(parents=True, exist_ok=True)
    extras: dict[str, str] = {}
    cen: dict = {"renderer": res.renderer, "gpu": res.gpu, "feedback": res.feedback, "n_frames": len(res.frames),
                 "gl_duration_ms": res.duration_ms, **(census or {})}
    if res.ok and res.judge_frames:
        frames = res.judge_frames
        extras["frames"] = str(art / "frames")
        sheet = write_contact_sheet(frames, art / SHEET_NAME)
        extras["sheet"] = str(sheet)
        gif = write_gif(res.frames, art / GIF_NAME)
        if gif is not None:
            extras["gif"] = str(gif)
        seq = sequence_stats([(f.time, f.path) for f in frames], nan_counts=[(f.nan, f.inf) for f in frames])
        gate = frame_gate(seq, motion_expected=motion_expected)
        metrics = art / METRICS_NAME
        metrics.write_text(json.dumps({"stats": seq.model_dump(mode="json"), "gate": gate.model_dump(mode="json")}, indent=1))
        extras["metrics"] = str(metrics)
        cen["frame_stats"] = {"mean_lum": seq.mean_lum, "mean_diff": seq.mean_diff, "static": seq.static, "any_nan": seq.any_nan,
                              "colourfulness": seq.mean_colourfulness, "edge_density": seq.mean_edge_density}
    result = BuildResult(
        ok=bool(res.ok and res.judge_frames), language=language, glb_path=None, extra_paths=extras,
        stdout_tail=res.stdout_tail, stderr_tail=(res.traceback or res.stderr_tail)[-4000:],
        error_type="" if res.ok else (res.error_type or "RenderError"),
        error_message="" if res.ok else (error_message if error_message is not None else res.error_message)[:4000],
        error_file=error_file if not res.ok else "", error_line=error_line if not res.ok else None,
        duration_ms=res.duration_ms, census=cen,
    )
    if res.ok and not res.judge_frames:
        result.error_type, result.error_message = "NoFrames", "the renderer produced no frames"
    ws.write_json(art / BUILD_JSON, result)
    return result


def read_metrics(ws: Workspace) -> tuple[SequenceStats, GateReport] | None:
    p = ws.artifacts / METRICS_NAME
    if not p.is_file():
        return None
    data = json.loads(p.read_text())
    return SequenceStats.model_validate(data["stats"]), GateReport.model_validate(data["gate"])


_TB_LINE = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)')


def traceback_location(tb: str, program_path: Path) -> tuple[str, int | None]:
    """Last traceback frame inside the agent's program → (rel path, line)."""
    hits = [(m.group("file"), int(m.group("line"))) for m in _TB_LINE.finditer(tb or "")]
    name = program_path.name
    for f, line in reversed(hits):
        if Path(f).name == name or f.endswith(str(program_path)):
            return "src/" + name, line
    return "", None
