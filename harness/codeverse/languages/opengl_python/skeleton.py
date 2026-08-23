"""Skeleton for the opengl_python language: a RUNNABLE ``src/program.py`` (background
gradient pass + one rotating lit cube) that shows the exact API the harness calls —
``setup(ctx, width, height) -> state`` and ``render(ctx, state, t, frame, fbo)`` —
with the plan's passes / key visuals / motion as TODO comments."""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.plan import GraphicsPlan, Plan
from codeverse.workspace import Workspace

_TEMPLATE = '''"""src/program.py — {title}

CONTRACT (the harness imports this module in a headless moderngl process):
  def setup(ctx, width, height) -> state     create programs / VAOs / textures / FBOs ONCE; return any object
  def render(ctx, state, t, frame, fbo)      draw the frame at time t (seconds) into `fbo` (already bound + cleared)
* `ctx` is a moderngl.Context (GL 3.3 core).  Never create a context or window; never read the clock.
* Multi-pass: render into your own FBOs, then call `fbo.use()` and draw the final image into it.
* Deterministic: same t → same frame.  Seed randomness.  GLSL strings use `#version 330 core`.
* Allowed imports: moderngl, numpy, math, random, struct, array, pathlib (+typing/dataclasses).
Duration {duration:g}s; judged frames at t = 0, 1, 2.5, 4, 6 s — everything must MOVE with t.

PLAN — {summary}
Style: {style}
{passes}
{visuals}
Motion: {motion}
"""
import math

import moderngl
import numpy as np

BACKGROUND_VERT = """#version 330 core
in vec2 in_pos;
out vec2 v_uv;
void main() {{ v_uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }}
"""
BACKGROUND_FRAG = """#version 330 core
uniform float u_time;
in vec2 v_uv;
out vec4 fragColor;
void main() {{
    vec3 top = vec3(0.08, 0.10, 0.25), bottom = vec3(0.85, 0.45, 0.30);
    vec3 col = mix(bottom, top, smoothstep(0.0, 1.0, v_uv.y + 0.1 * sin(u_time * 0.5 + v_uv.x * 6.0)));
    fragColor = vec4(col, 1.0);
}}
"""
CUBE_VERT = """#version 330 core
uniform mat4 u_mvp;
uniform mat4 u_model;
in vec3 in_pos;
in vec3 in_normal;
out vec3 v_normal;
void main() {{ v_normal = mat3(u_model) * in_normal; gl_Position = u_mvp * vec4(in_pos, 1.0); }}
"""
CUBE_FRAG = """#version 330 core
uniform vec3 u_color;
in vec3 v_normal;
out vec4 fragColor;
void main() {{
    vec3 n = normalize(v_normal);
    float diff = max(dot(n, normalize(vec3(0.4, 0.8, 0.5))), 0.0);
    fragColor = vec4(u_color * (0.25 + 0.75 * diff), 1.0);
}}
"""


def _cube() -> np.ndarray:
    """36 vertices: position (3) + normal (3), interleaved float32."""
    faces = [((0, 0, 1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)),
             ((0, 0, -1), (1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)),
             ((1, 0, 0), (1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)),
             ((-1, 0, 0), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)),
             ((0, 1, 0), (-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)),
             ((0, -1, 0), (-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1))]
    out = []
    for n, a, b, c, d in faces:
        for v in (a, b, c, a, c, d):
            out.extend(v)
            out.extend(n)
    return np.array(out, dtype="f4") * np.tile([0.5, 0.5, 0.5, 1, 1, 1], 36).astype("f4")


def _perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    m = np.zeros((4, 4), dtype="f4")
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3] = (far + near) / (near - far), (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def _look_at(eye, target, up=(0, 1, 0)) -> np.ndarray:
    eye, target, up = np.array(eye, dtype="f4"), np.array(target, dtype="f4"), np.array(up, dtype="f4")
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4, dtype="f4")
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def _rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], dtype="f4")


def setup(ctx: moderngl.Context, width: int, height: int):
    bg_prog = ctx.program(vertex_shader=BACKGROUND_VERT, fragment_shader=BACKGROUND_FRAG)
    quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    bg_vao = ctx.vertex_array(bg_prog, [(quad, "2f", "in_pos")])
    cube_prog = ctx.program(vertex_shader=CUBE_VERT, fragment_shader=CUBE_FRAG)
    vbo = ctx.buffer(_cube().tobytes())
    cube_vao = ctx.vertex_array(cube_prog, [(vbo, "3f 3f", "in_pos", "in_normal")])
    proj = _perspective(45.0, width / height, 0.1, 100.0)
    view = _look_at((0.0, 1.2, 3.0), (0.0, 0.0, 0.0))
    # TODO build the plan's passes here (instancing, extra FBOs for post-processing, textures)
    return {{"bg_prog": bg_prog, "bg_vao": bg_vao, "cube_prog": cube_prog, "cube_vao": cube_vao, "proj": proj, "view": view}}


def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    fbo.use()
    ctx.disable(moderngl.DEPTH_TEST)
    state["bg_prog"]["u_time"].value = t
    state["bg_vao"].render(mode=moderngl.TRIANGLE_STRIP)
    ctx.enable(moderngl.DEPTH_TEST)
    model = _rot_y(t * 0.8)
    mvp = state["proj"] @ state["view"] @ model
    state["cube_prog"]["u_mvp"].write(mvp.T.astype("f4").tobytes())   # column-major for GLSL
    state["cube_prog"]["u_model"].write(model.T.astype("f4").tobytes())
    state["cube_prog"]["u_color"].value = (0.9, 0.6, 0.2)
    state["cube_vao"].render(mode=moderngl.TRIANGLES)
    # TODO replace the placeholder cube with the plan's content; keep drawing into `fbo` last
'''


def _comment_lines(prefix: str, items: list[str]) -> str:
    if not items:
        return f"{prefix}: (none)"
    return "\n".join(f"  TODO {prefix} {i + 1}: {x}" for i, x in enumerate(items))


def program_source(plan: GraphicsPlan | None) -> str:
    title = plan.title if plan else "untitled program"
    passes = [f"{p.name} ({p.kind}): {p.description}" for p in plan.passes] if plan else []
    visuals = list(plan.key_visuals) if plan else []
    return _TEMPLATE.format(
        title=title, summary=plan.summary if plan else "", style=plan.style if plan else "",
        motion=plan.motion if plan else "animate with t", duration=plan.duration_s if plan else 8.0,
        passes=_comment_lines("pass", passes), visuals=_comment_lines("key visual", visuals),
    )


def write_skeleton(ws: Workspace, plan: Plan | None) -> list[Path]:
    gplan = plan if isinstance(plan, GraphicsPlan) else None
    ws.src.mkdir(parents=True, exist_ok=True)
    p = ws.src / "program.py"
    p.write_text(program_source(gplan))
    return [p]
