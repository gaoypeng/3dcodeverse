"""Shared build plumbing for the two graphics languages (glsl_shader, opengl_python).

``finish_build`` turns a :class:`GlResult` into the canonical artifact set —
``artifacts/frames/*.png`` (written by the runner), ``artifacts/frames_sheet.png``,
``artifacts/preview.gif``, ``artifacts/metrics.json`` (frame stats + the
``gl_frames`` gate), ``artifacts/build.json`` — and a typed :class:`BuildResult`
(``glb_path`` is always None: the deliverable is code + frames; ``gates`` carries the
``gl_frames`` report, which the round takes from there).
``judge_times`` derives the judged times from the plan's duration.
``make_host`` is the one place a runtime turns settings into a :class:`GlHost`.

GLSL info-log parsing lives here too (:class:`GlslMessage`, :class:`LineMap`,
:func:`parse_glsl_log`) so ``opengl_python`` does not depend on its sibling
``glsl_shader`` package; ``glsl_shader/wrap.py`` composes sources and keeps
``compose`` / ``Composed`` / ``first_error``, importing the types from here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import BuildResult, GateReport
from codeverse3d.contracts.plan import GraphicsPlan
from codeverse3d.spatial.frame_stats import SequenceStats, frame_gate, sequence_stats
from codeverse3d.spatial.gl_render import (
    RESULT_NAME,
    GlHost,
    GlResult,
    write_contact_sheet,
    write_gif,
)
from codeverse3d.spatial.tool_common import plan_or_none
from codeverse3d.workspace import Workspace

JUDGE_TIMES: tuple[float, ...] = (0.0, 1.0, 2.5, 4.0, 6.0)
SHEET_NAME = "frames_sheet.png"
GIF_NAME = "preview.gif"
METRICS_NAME = "metrics.json"
BUILD_JSON = "build.json"
FRAMES_DIR = "frames"
DEFAULT_RESOLUTION: tuple[int, int] = (1280, 720)
MAX_PIXELS = 1920 * 1080


def invalidate_stale_outputs(ws: Workspace) -> None:
    """Wipe the previous build's GL artifacts at the TOP of ``build()``.

    Hoisted from ``GlHost._run`` (which still wipes its own out_dir) so the
    ``MISSING_ENTRY`` early returns — which never reach the host — also clear
    ``frames/`` and ``gl_result.json``; sheet/gif/metrics/build.json were never
    cleared anywhere, so a failed build left the previous round's sheet looking
    current to every bare-existence reader (``read_metrics``, the gallery)."""
    ws.stage_artifacts(BUILD_JSON, SHEET_NAME, GIF_NAME, METRICS_NAME).invalidate()
    (ws.artifacts / RESULT_NAME).unlink(missing_ok=True)
    frames = ws.artifacts / FRAMES_DIR
    if frames.is_dir():
        for p in frames.glob("*.png"):
            p.unlink(missing_ok=True)


def make_host(override: GlHost | None = None, timeout_s: float | None = None) -> GlHost:
    """The runtime's :class:`GlHost`: the injected one (tests) or one built from settings."""
    if override is not None:
        return override
    settings = get_settings()
    return GlHost(gpu=settings.render.gpu, timeout_s=float(timeout_s or settings.limits.render_timeout_s))


def load_plan(ws: Workspace) -> GraphicsPlan | None:
    """The run's GraphicsPlan; ``None`` (→ defaults) without one."""
    plan = plan_or_none(ws.plan_path)
    return plan if isinstance(plan, GraphicsPlan) else None


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


def finish_build(ws: Workspace, res: GlResult, *, language: str, error_file: str = "", error_line: int | None = None,
                 error_message: str | None = None, census: dict | None = None, motion_expected: bool = True) -> BuildResult:
    """Write derived artifacts + build.json and return the BuildResult."""
    art = ws.artifacts
    art.mkdir(parents=True, exist_ok=True)
    extras: dict[str, str] = {}
    gates: list[GateReport] = []
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
        gates.append(gate)
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
        duration_ms=res.duration_ms, census=cen, gates=gates,
    )
    if res.ok and not res.judge_frames:
        result.error_type, result.error_message = "NoFrames", "the renderer produced no frames"
    if not result.ok:
        # a failed build must not leave the previous round's derived artifacts
        # (sheet / gif / metrics are written only on ok above) looking current
        ws.stage_artifacts(SHEET_NAME, GIF_NAME, METRICS_NAME).invalidate()
    ws.write_json(art / BUILD_JSON, result)
    return result


def read_metrics(ws: Workspace, where: Path | None = None) -> tuple[SequenceStats, GateReport] | None:
    """``metrics.json`` in ``where`` (a round's ``renders/rNN/`` copy), default the canonical one."""
    p = (where or ws.artifacts) / METRICS_NAME
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


# --------------------------------------------------------------------------- GLSL info logs
# The composed-source line map (``wrap.compose`` builds one) and the driver-dialect
# log parser live here so both GL runtimes can map compiler messages to agent files.


@dataclass(frozen=True)
class Segment:
    file: str  # "src/shader.frag" | "src/common.glsl" | "harness"
    start: int  # 1-based first line in the composed source
    n_lines: int

    @property
    def end(self) -> int:
        return self.start + self.n_lines - 1


@dataclass
class LineMap:
    segments: list[Segment] = field(default_factory=list)

    def locate(self, line: int) -> tuple[str, int]:
        """Composed line → (file, line-in-file); harness lines map to ("harness", line)."""
        for s in self.segments:
            if s.start <= line <= s.end:
                return s.file, line - s.start + 1
        return "harness", line


# Mesa: "0:12(5): error: `foo' undeclared" · NVIDIA: "0(12) : error C1008: ..." · ANGLE/ES: "ERROR: 0:12: 'foo' : undeclared"
_MSG_PATTERNS = (
    re.compile(r"^\s*(?P<src>\d+):(?P<line>\d+)\((?P<col>\d+)\):\s*(?P<kind>error|warning):\s*(?P<msg>.*)$"),
    re.compile(r"^\s*(?P<src>\d+)\((?P<line>\d+)\)\s*:\s*(?P<kind>error|warning)\s*(?P<msg>.*)$"),
    re.compile(r"^\s*(?P<kind>ERROR|WARNING):\s*(?P<src>\d+):(?P<line>\d+):\s*(?P<msg>.*)$"),
)


@dataclass(frozen=True)
class GlslMessage:
    kind: str  # error | warning
    line: int  # composed line
    message: str
    file: str = ""
    file_line: int = 0

    def text(self) -> str:
        loc = f"{self.file}:{self.file_line}" if self.file else f"line {self.line}"
        return f"{loc}: {self.kind}: {self.message}"


def parse_glsl_log(log: str, line_map: LineMap | None = None) -> list[GlslMessage]:
    """Parse a GLSL info log (any driver dialect) into messages mapped to agent files."""
    out: list[GlslMessage] = []
    for raw in (log or "").splitlines():
        for pat in _MSG_PATTERNS:
            m = pat.match(raw)
            if not m:
                continue
            line = int(m.group("line"))
            kind = m.group("kind").lower()
            msg = m.group("msg").strip()
            f, fl = line_map.locate(line) if line_map else ("", 0)
            out.append(GlslMessage(kind=kind, line=line, message=msg, file=f, file_line=fl))
            break
    return out
