# `src/program.py` — one full-screen pass driven by `t` (the interface only)

```python
import moderngl
import numpy as np

VERT = "#version 330 core\nin vec2 in_pos; out vec2 v_uv;\nvoid main() { v_uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }\n"
FRAG = "#version 330 core\nuniform float u_time; in vec2 v_uv; out vec4 fragColor;\nvoid main() { fragColor = vec4(vec3(0.5 + 0.5 * sin(6.2832 * (v_uv.x - 0.05 * u_time))), 1.0); }\n"


def setup(ctx: moderngl.Context, width: int, height: int):
    prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
    quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    return {"prog": prog, "vao": ctx.vertex_array(prog, [(quad, "2f", "in_pos")]), "size": (width, height)}
```
```python
def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    fbo.use()
    ctx.viewport = (0, 0, *state["size"])
    ctx.disable(moderngl.DEPTH_TEST)
    state["prog"]["u_time"].value = t
    state["vao"].render(mode=moderngl.TRIANGLE_STRIP)
```
What the build calls, how a uniform takes `t`, where the final draw lands.  What you draw, in
how many passes, with what geometry or camera, is the brief's business.
