"""Standalone moderngl runner (never imports codeverse).

Usage: ``python run_gl.py --job job.json``

``job.json``::

    {"mode": "shader" | "program",
     "out_dir": "...",                      # frames/ + result.json are written here
     "width": 1280, "height": 720,
     "times": [0.0, 1.0, 2.5],              # judged frames
     "extra_times": [0.5, 1.5],             # gif-only frames (optional)
     "fps": 30,                             # simulation step for feedback shaders
     "max_steps": 240,
     # shader mode: fully wrapped sources (header already prepended by the harness)
     "frag_src": "...", "buffer_a_src": "..." | null, "feedback": false,
     # program mode
     "program_path": "/abs/src/program.py", "program_cwd": "/abs/src"}

Writes ``<out_dir>/frames/f<NN>_t<T>.png`` (8-bit RGB, top-down) and
``<out_dir>/gl_result.json``::

    {"ok": bool, "stage": "context|compile|compile_buffer_a|setup|render|import",
     "error_type": "", "error_message": "", "traceback": "", "renderer": "...",
     "frames": [{"index", "time", "path", "judge": bool, "nan": int, "inf": int,
                 "mean": float}], "duration_ms": int}

Exit code 0 whenever gl_result.json was written (even for agent errors); 3 when the
GL context could not be created (the harness retries with another driver env).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np

VERT = """#version 330 core
in vec2 in_pos;
void main() { gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

EXIT_CONTEXT = 3


RESULT_NAME = "gl_result.json"


def _write_result(out_dir: Path, data: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / RESULT_NAME).write_text(json.dumps(data, indent=1, default=str))


def _frame_path(out_dir: Path, index: int, t: float) -> Path:
    return out_dir / "frames" / f"f{index:02d}_t{t:06.2f}.png"


def _save_frame(raw: bytes, w: int, h: int, path: Path) -> dict:
    """float32 RGBA readback (bottom-up) → top-down 8-bit PNG + nan/inf counts."""
    from PIL import Image

    arr = np.frombuffer(raw, dtype=np.float32).reshape(h, w, 4)[::-1]
    nan = int(np.isnan(arr).sum())
    inf = int(np.isinf(arr).sum())
    rgb = np.nan_to_num(arr[..., :3], nan=0.0, posinf=1.0, neginf=0.0)
    rgb = np.clip(rgb, 0.0, 1.0)
    img = (rgb * 255.0 + 0.5).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img, "RGB").save(path)
    return {"nan": nan, "inf": inf, "mean": float(rgb.mean())}


def _noise_texture(ctx, size: int = 256):
    rng = np.random.default_rng(12345)
    data = rng.integers(0, 256, size=(size, size, 4), dtype=np.uint8)
    tex = ctx.texture((size, size), 4, data.tobytes())
    tex.repeat_x = tex.repeat_y = True
    tex.filter = (ctx.LINEAR, ctx.LINEAR)
    return tex


def _set_uniforms(prog, *, t: float, frame: int, w: int, h: int, units: dict) -> None:
    members = set(prog)
    if "u_time" in members:
        prog["u_time"].value = float(t)
    if "u_resolution" in members:
        prog["u_resolution"].value = (float(w), float(h))
    if "u_mouse" in members:
        prog["u_mouse"].value = (0.0, 0.0)
    if "u_frame" in members:
        prog["u_frame"].value = int(frame)
    for name, unit in units.items():
        if name in members:
            prog[name].value = unit


class Pass:
    """One fullscreen fragment pass with ping-pong float targets."""

    def __init__(self, ctx, frag_src: str, w: int, h: int, quad):
        self.prog = ctx.program(vertex_shader=VERT, fragment_shader=frag_src)
        self.vao = ctx.vertex_array(self.prog, [(quad, "2f", "in_pos")])
        self.tex = [ctx.texture((w, h), 4, dtype="f4") for _ in range(2)]
        for t in self.tex:
            t.filter = (ctx.LINEAR, ctx.LINEAR)
            t.repeat_x = t.repeat_y = False
        self.fbo = [ctx.framebuffer(color_attachments=[t]) for t in self.tex]
        self.cur = 0

    @property
    def prev_tex(self):
        return self.tex[1 - self.cur]

    def draw(self, ctx, *, w: int, h: int, t: float, frame: int, samplers: dict):
        units: dict = {}
        for unit, (name, tex) in enumerate(samplers.items()):
            tex.use(location=unit)
            units[name] = unit
        self.fbo[self.cur].use()
        ctx.viewport = (0, 0, w, h)
        self.fbo[self.cur].clear(0.0, 0.0, 0.0, 1.0)
        _set_uniforms(self.prog, t=t, frame=frame, w=w, h=h, units=units)
        self.vao.render(mode=ctx.TRIANGLE_STRIP)

    def swap(self) -> None:
        self.cur = 1 - self.cur


def _schedule(job: dict) -> list[tuple[float, bool]]:
    """(time, is_judge_frame) sorted by time."""
    times = {float(t): True for t in job.get("times", [])}
    for t in job.get("extra_times", []) or []:
        times.setdefault(float(t), False)
    return sorted(times.items())


def run_shader(ctx, job: dict, out_dir: Path) -> dict:
    import moderngl  # noqa: F401  (already imported by caller; explicit for clarity)

    w, h = int(job["width"]), int(job["height"])
    quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    noise = _noise_texture(ctx)
    try:
        buffer_a = Pass(ctx, job["buffer_a_src"], w, h, quad) if job.get("buffer_a_src") else None
    except Exception as e:  # noqa: BLE001 — GLSL compile error → typed result
        return {"ok": False, "stage": "compile_buffer_a", "error_type": type(e).__name__, "error_message": str(e)}
    try:
        image = Pass(ctx, job["frag_src"], w, h, quad)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "compile", "error_type": type(e).__name__, "error_message": str(e)}

    schedule = _schedule(job)
    feedback = bool(job.get("feedback")) or buffer_a is not None
    fps = float(job.get("fps", 30))
    frames: list[dict] = []

    def step(t: float, frame: int) -> None:
        if buffer_a is not None:
            buffer_a.draw(ctx, w=w, h=h, t=t, frame=frame, samplers={"u_prev": buffer_a.prev_tex, "u_noise": noise})
            buffer_a.swap()
        samplers = {"u_prev": image.prev_tex, "u_noise": noise}
        if buffer_a is not None:
            samplers["u_buffer_a"] = buffer_a.prev_tex  # just-rendered output (after swap)
        image.draw(ctx, w=w, h=h, t=t, frame=frame, samplers=samplers)
        image.swap()

    def capture(index: int, t: float, judge: bool) -> None:
        raw = image.fbo[1 - image.cur].read(components=4, dtype="f4")  # last drawn
        p = _frame_path(out_dir, index, t)
        info = _save_frame(raw, w, h, p)
        frames.append({"index": index, "time": t, "path": str(p), "judge": judge, **info})

    try:
        if not feedback:
            for i, (t, judge) in enumerate(schedule):
                step(t, int(round(t * fps)))
                capture(i, t, judge)
        else:
            t_end = schedule[-1][0] if schedule else 0.0
            n_steps = int(round(t_end * fps)) + 1
            max_steps = int(job.get("max_steps", 240))
            dt = 1.0 / fps
            if n_steps > max_steps:
                n_steps, dt = max_steps, t_end / max(1, max_steps - 1)
            pending = list(schedule)
            idx = 0
            for k in range(n_steps):
                t = k * dt
                step(t, k)
                while pending and (pending[0][0] <= t + 1e-9 or k == n_steps - 1):
                    target, judge = pending.pop(0)
                    capture(idx, target, judge)
                    idx += 1
        ctx.finish()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "render", "error_type": type(e).__name__, "error_message": str(e),
                "traceback": traceback.format_exc()[-3000:], "frames": frames}
    return {"ok": True, "stage": "render", "frames": frames, "feedback": feedback}


def run_program(ctx, job: dict, out_dir: Path) -> dict:
    w, h = int(job["width"]), int(job["height"])
    path = Path(job["program_path"])
    cwd = Path(job.get("program_cwd") or path.parent)
    os.chdir(cwd)
    sys.path.insert(0, str(cwd))
    try:
        spec = importlib.util.spec_from_file_location("agent_program", str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        setup = getattr(module, "setup", None)
        render = getattr(module, "render", None)
        if not callable(setup) or not callable(render):
            raise AttributeError("program.py must define setup(ctx, width, height) and render(ctx, state, t, frame, fbo)")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "import", "error_type": type(e).__name__, "error_message": str(e),
                "traceback": traceback.format_exc()[-4000:]}
    color = ctx.texture((w, h), 4, dtype="f4")
    depth = ctx.depth_renderbuffer((w, h))
    fbo = ctx.framebuffer(color_attachments=[color], depth_attachment=depth)
    try:
        fbo.use()
        state = setup(ctx, w, h)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "setup", "error_type": type(e).__name__, "error_message": str(e),
                "traceback": traceback.format_exc()[-4000:]}
    frames: list[dict] = []
    try:
        for i, (t, judge) in enumerate(_schedule(job)):
            fbo.use()
            ctx.viewport = (0, 0, w, h)
            fbo.clear(0.0, 0.0, 0.0, 1.0, depth=1.0)
            render(ctx, state, float(t), i, fbo)
            ctx.finish()
            raw = fbo.read(components=4, dtype="f4")
            p = _frame_path(out_dir, i, t)
            info = _save_frame(raw, w, h, p)
            frames.append({"index": i, "time": t, "path": str(p), "judge": judge, **info})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "render", "error_type": type(e).__name__, "error_message": str(e),
                "traceback": traceback.format_exc()[-4000:], "frames": frames}
    return {"ok": True, "stage": "render", "frames": frames}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ns = ap.parse_args(argv)
    job = json.loads(Path(ns.job).read_text())
    out_dir = Path(job["out_dir"])
    t0 = time.time()
    try:
        import moderngl

        ctx = moderngl.create_standalone_context(backend=job.get("backend", "egl"))
    except Exception as e:  # noqa: BLE001 — no GL at all: let the harness retry another driver env
        _write_result(out_dir, {"ok": False, "stage": "context", "error_type": type(e).__name__, "error_message": str(e),
                                "traceback": traceback.format_exc()[-2000:], "duration_ms": int((time.time() - t0) * 1000)})
        return EXIT_CONTEXT
    renderer = str(ctx.info.get("GL_RENDERER", ""))
    # agent prints must not corrupt anything: capture them
    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    try:
        res = run_program(ctx, job, out_dir) if job["mode"] == "program" else run_shader(ctx, job, out_dir)
    finally:
        sys.stdout = real_stdout
    res.update({"renderer": renderer, "duration_ms": int((time.time() - t0) * 1000), "stdout": captured.getvalue()[-4000:]})
    _write_result(out_dir, res)
    with contextlib.suppress(Exception):
        ctx.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
