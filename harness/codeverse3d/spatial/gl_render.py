"""GlHost: headless moderngl rendering of fragment shaders / raw OpenGL programs.

moderngl contexts are not fork-safe and the harness is multi-threaded, so every
render happens in a fresh ``python3`` subprocess running the standalone
``languages/opengl_python/wrappers/run_gl.py``.  The host tries the GPU driver
environment first (Mesa d3d12 on WSL2) and falls back to llvmpipe when the
context cannot be created; the decision is cached per process.

Public API::

    host = GlHost()                                   # gpu="auto" | "on" | "off"
    res = host.render_fragment_shader(frag_src, out_dir, width=1280, height=720, times=(0, 1, 2.5),
                                      buffer_a_src=None, feedback=False, extra_times=())
    res = host.run_program(program_path, out_dir, width=..., height=..., times=...)
    res.ok · res.frames[GlFrame] · res.judge_frames · res.stage · res.error_message · res.renderer

Artifacts derived from a result: :func:`write_contact_sheet`, :func:`write_gif`.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.proc import run_subprocess

RUNNER = Path(__file__).resolve().parent.parent / "languages" / "opengl_python" / "wrappers" / "run_gl.py"

#: Mesa d3d12 driver env (GPU on WSL2).  Falls back to llvmpipe when unusable.
GPU_ENV: dict[str, str] = {
    "GALLIUM_DRIVER": "d3d12",
    "MESA_LOADER_DRIVER_OVERRIDE": "d3d12",
    "MESA_D3D12_DEFAULT_ADAPTER_NAME": "NVIDIA",
}
CPU_ENV: dict[str, str] = {"LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe"}
_ENV_PASS = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONHASHSEED", "LD_LIBRARY_PATH", "XDG_RUNTIME_DIR", "TMPDIR")

DEFAULT_TIMES: tuple[float, ...] = (0.0, 1.0, 2.5, 4.0, 6.0)
EXIT_CONTEXT = 3
RESULT_NAME = "gl_result.json"
JOB_NAME = "gl_job.json"

_gpu_state: dict[str, bool | None] = {"ok": None}


class GlFrame(BaseModel):
    index: int
    time: float
    path: str
    judge: bool = True
    nan: int = 0
    inf: int = 0
    mean: float = 0.0


class GlResult(BaseModel):
    ok: bool
    mode: str
    stage: str = ""
    error_type: str = ""
    error_message: str = ""
    traceback: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    renderer: str = ""
    gpu: bool = False
    feedback: bool = False
    frames: list[GlFrame] = Field(default_factory=list)
    duration_ms: int = 0
    timed_out: bool = False

    @property
    def judge_frames(self) -> list[GlFrame]:
        return [f for f in self.frames if f.judge]


class GlHostError(RuntimeError):
    """Harness-side failure (runner missing, no GL at all) — not an agent error."""


def _base_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_PASS if k in os.environ}
    env["PYTHONUNBUFFERED"] = "1"
    env.update(extra)
    return env


def gpu_preference(gpu: str) -> list[bool]:
    """Order of attempts: [True, False] (GPU then CPU) for auto, etc."""
    if gpu == "on":
        return [True]
    if gpu == "off":
        return [False]
    if _gpu_state["ok"] is False:
        return [False]
    return [True, False]


class GlHost:
    def __init__(self, *, gpu: str = "auto", python: str | None = None, timeout_s: float = 240.0, fps: int = 30,
                 max_steps: int = 240):
        if not RUNNER.is_file():
            raise GlHostError(f"GL runner missing: {RUNNER}")
        self.gpu = gpu
        self.python = python or sys.executable
        self.timeout_s = timeout_s
        self.fps = fps
        self.max_steps = max_steps

    # ------------------------------------------------------------------ public
    def render_fragment_shader(self, frag_src: str, out_dir: Path, *, width: int = 1280, height: int = 720,
                               times: Sequence[float] = DEFAULT_TIMES, buffer_a_src: str | None = None,
                               feedback: bool = False, extra_times: Sequence[float] = ()) -> GlResult:
        job = {"mode": "shader", "frag_src": frag_src, "buffer_a_src": buffer_a_src, "feedback": bool(feedback),
               "width": int(width), "height": int(height), "times": [float(t) for t in times],
               "extra_times": [float(t) for t in extra_times]}
        return self._run(job, out_dir)

    def run_program(self, program_path: Path, out_dir: Path, *, width: int = 1280, height: int = 720,
                    times: Sequence[float] = DEFAULT_TIMES, extra_times: Sequence[float] = (),
                    cwd: Path | None = None) -> GlResult:
        program_path = Path(program_path).resolve()
        job = {"mode": "program", "program_path": str(program_path), "program_cwd": str(cwd or program_path.parent),
               "width": int(width), "height": int(height), "times": [float(t) for t in times],
               "extra_times": [float(t) for t in extra_times]}
        return self._run(job, out_dir)

    # ------------------------------------------------------------------ internals
    def _run(self, job: dict[str, Any], out_dir: Path) -> GlResult:
        # The subprocess starts inside out_dir. A relative job path would be
        # resolved against that directory a second time and appear to crash
        # before it could write any result (observed with --out examples/...).
        out_dir = Path(out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / RESULT_NAME).unlink(missing_ok=True)
        for p in (out_dir / "frames").glob("*.png"):
            p.unlink()
        job.update({"out_dir": str(out_dir), "fps": self.fps, "max_steps": self.max_steps, "backend": "egl"})
        job_path = out_dir / JOB_NAME
        job_path.write_text(json.dumps(job))
        last: GlResult | None = None
        for use_gpu in gpu_preference(self.gpu):
            res = self._run_once(job_path, out_dir, use_gpu=use_gpu, mode=str(job["mode"]))
            if res.stage == "context" and not res.ok:
                if use_gpu:
                    _gpu_state["ok"] = False
                last = res
                continue
            if use_gpu and _gpu_state["ok"] is None:
                _gpu_state["ok"] = True
            return res
        assert last is not None
        raise GlHostError(f"no usable OpenGL context: {last.error_type}: {last.error_message[:300]}")

    def _run_once(self, job_path: Path, out_dir: Path, *, use_gpu: bool, mode: str) -> GlResult:
        env = _base_env(GPU_ENV if use_gpu else CPU_ENV)
        t0 = time.time()
        proc = run_subprocess([self.python, str(RUNNER), "--job", str(job_path)], cwd=out_dir, timeout_s=self.timeout_s, env=env)
        result_path = out_dir / RESULT_NAME
        base = dict(mode=mode, gpu=use_gpu, stdout_tail=proc.stdout[-3000:], stderr_tail=proc.stderr[-3000:],
                    duration_ms=proc.duration_ms or int((time.time() - t0) * 1000))
        if proc.timed_out:
            return GlResult(ok=False, stage="render", error_type="RenderTimeout", timed_out=True,
                            error_message=f"rendering exceeded {self.timeout_s:.0f}s — reduce loop counts / raymarch steps / resolution",
                            **base)
        if not result_path.is_file():
            if proc.returncode == EXIT_CONTEXT:
                return GlResult(ok=False, stage="context", error_type="ContextError", error_message=proc.stderr[-800:], **base)
            return GlResult(ok=False, stage="crash", error_type="RunnerCrash",
                            error_message=f"GL runner exited {proc.returncode} without a result (segfault / OOM?)", **base)
        data = json.loads(result_path.read_text())
        frames = [GlFrame.model_validate(f) for f in data.get("frames", [])]
        res = GlResult(ok=bool(data.get("ok")), stage=str(data.get("stage", "")), error_type=str(data.get("error_type", "")),
                       error_message=str(data.get("error_message", "")), traceback=str(data.get("traceback", "")),
                       renderer=str(data.get("renderer", "")), feedback=bool(data.get("feedback", False)), frames=frames, **base)
        if data.get("stdout"):
            res.stdout_tail = (str(data["stdout"]) + "\n" + res.stdout_tail)[-3000:]
        return res


# --------------------------------------------------------------------------- derived artifacts
def write_contact_sheet(frames: Sequence[GlFrame], out: Path, *, cols: int = 3, tile_w: int = 480) -> Path:
    """Labelled grid of frames keeping their aspect ratio (``t=<s>`` under each tile).

    Thin wrapper over :func:`codeverse3d.spatial.sheet.contact_sheet` (the one
    sheet builder) with a 16:9-style cell derived from the first frame.
    """
    from codeverse3d.spatial.sheet import contact_sheet, tile_size

    frames = list(frames)
    if not frames:
        raise ValueError("write_contact_sheet: no frames")
    return contact_sheet([(f"t={f.time:g}s", f.path) for f in frames], Path(out), cols=cols,
                         tile=tile_size(tile_w, sample=frames[0].path))


def write_gif(frames: Sequence[GlFrame], out: Path, *, width: int = 480, fps: int = 6) -> Path | None:
    """Animated GIF preview from ALL frames (judge + extra), resized to ``width``."""
    from codeverse3d.spatial.sheet import write_gif as _write_gif

    ordered = sorted(frames, key=lambda f: f.time)
    if len(ordered) < 2:
        return None
    return _write_gif([f.path for f in ordered], out, fps=fps, width=width)


def gif_times(duration_s: float, n: int = 12) -> list[float]:
    """Evenly spaced preview times across one loop (excluding the end)."""
    n = max(2, n)
    d = max(0.5, float(duration_s))
    return [round(i * d / n, 3) for i in range(n)]
