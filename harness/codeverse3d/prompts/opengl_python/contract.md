# opengl_python authoring contract (raw OpenGL 3.3 core through moderngl, headless)

You write ONE python module, `src/program.py`, that draws frames with raw OpenGL calls (the moderngl API)
and inline GLSL.  The harness imports it in a headless EGL process, creates the context and an output
framebuffer, calls your two functions and reads the pixels back (a PNG per frame at t = 0, 1, 2.5, 4, 6 s,
clipped to the plan's duration and never fewer than 3 samples, plus about 12 preview frames for the GIF),
measures the judged frames and shows them to a judge.  The code is the deliverable.

## Files
```
src/program.py      REQUIRED  defines setup() and render()
src/*.glsl          optional  shader sources you ship; read with Path(__file__).with_name("x.glsl").read_text()
```
Only these two globs are delivered.  Another `src/*.py` imports at build time but draws a lint WARN
(`stray_file`) and is not part of what the harness hands over — keep the code in `program.py`.

## The two entry points (exact signatures)
```python
def setup(ctx: moderngl.Context, width: int, height: int):      # called ONCE
    ...create programs, buffers, VAOs, textures, your own FBOs...
    return state                                                 # any object; passed back to render()

def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    ...draw the frame for time t (seconds) — multi-pass allowed into your own FBOs...
    fbo.use()   # the harness framebuffer (RGBA f4 colour + depth) — draw the FINAL image into it LAST
```
* The harness builds ONE sorted schedule from the judged times and the preview times; for each entry it
  binds `fbo`, clears it to (0, 0, 0, 1) with depth 1.0, sets `ctx.viewport` to the full size and calls
  `render(ctx, state, t, frame, fbo)`.  `frame` is the index into that merged schedule (0, 1, 2 …), not a
  60 fps counter and not "the n-th judged frame".
* After `render` returns, the harness reads `fbo` back as float and flips the rows itself (GL read-back is
  bottom-up): draw with y up and do not flip in your shader.  If you draw into your own FBOs, call
  `fbo.use()` and reset `ctx.viewport = (0, 0, width, height)` before the final pass.  Never call `ctx.screen`.
* `render` may be called for any `t`, any number of times, in any order (the build renders the sorted
  schedule; the `gl_probe` / `gl_frames` tools re-render single times).  The frame for a `t` must not
  depend on which `t`s were rendered before: same `t` → same image.
* `t` is absolute time in seconds.  Width / height come from the plan's resolution (1280x720 when the plan
  states none or an unusable one; at most 1920x1080 pixels).
* The whole schedule must render inside the host's timeout (`limits.render_timeout_s`, 300 s by default);
  a render that overruns is a failed build.

## Executable interface example — `src/program.py` (one full-screen pass driven by `t`)
```python
import moderngl
import numpy as np

VERT = "#version 330 core\nin vec2 in_pos; out vec2 v_uv;\nvoid main() { v_uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }\n"
FRAG = "#version 330 core\nuniform float u_time; in vec2 v_uv; out vec4 fragColor;\nvoid main() { fragColor = vec4(vec3(0.5 + 0.5 * sin(6.2832 * (v_uv.x - 0.05 * u_time))), 1.0); }\n"


def setup(ctx: moderngl.Context, width: int, height: int):
    prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
    quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    return {"prog": prog, "vao": ctx.vertex_array(prog, [(quad, "2f", "in_pos")]), "size": (width, height)}


def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    fbo.use()
    ctx.viewport = (0, 0, *state["size"])
    ctx.disable(moderngl.DEPTH_TEST)
    state["prog"]["u_time"].value = t
    state["vao"].render(mode=moderngl.TRIANGLE_STRIP)
```
It shows the interface only — what the harness calls, how a uniform takes `t`, where the final draw lands.
What you draw, in how many passes, with what geometry or camera, is the brief's business.

## Allowed / forbidden (the AST lint enforces; an ERROR caps the score at 0.6)
* Imports (the allow-list; anything else is an ERROR): `moderngl`, `numpy`, `math`, `random`, `struct`,
  `array`, `pathlib`, `typing`, `dataclasses`, `itertools`, `functools`, `collections`, `colorsys`, `enum`,
  `__future__`.
* ERROR: window / input libraries (glfw, pygame, pyglet, PyQt, PySide, tkinter, moderngl_window, sdl2, wx,
  PyOpenGL); `os`, `sys`, `io`, `time`, `datetime`, `signal`, `tempfile`, subprocess and network modules;
  the calls `open` / `exec` / `eval` / `compile` / `__import__` / `input` / `exit` / `quit` / `breakpoint`;
  file-system writes (`.write_text` / `.write_bytes` / `.unlink` / `.mkdir` / `.rmdir` / `.rename`);
  `moderngl.create_context()` / `create_standalone_context()` (the harness owns the context);
  `time.time()` / `perf_counter()` / `monotonic()`; `gl_FragColor`, `attribute` / `varying` in a shader string.
* WARN: a `#version` other than `330 core` in a shader string (`glsl_version`); `random.*` called without
  `random.seed(...)` or a `default_rng(...)` (`unseeded_random`) — seed in `setup`; a file over 120 000
  characters (`too_long`); an extra `src/*.py` (`stray_file`).
* The harness tears the context down at job end and has no `cleanup()` callback.  When you replace
  resources, release each VAO, buffer, texture, framebuffer and program you own (a framebuffer does not
  own its attachments); never release the supplied `ctx` or `fbo`.

## moderngl facts that decide whether the build runs (not style)
* Every shader string starts with `#version 330 core` on its first line (nothing before it, not even a
  blank line), uses `in` / `out` qualifiers and declares `out vec4 fragColor;`.
* `ctx.vertex_array(prog, [(buffer, "3f 3f 2f", "in_pos", "in_normal", "in_uv")], index_buffer=ibo)`:
  the names must match the vertex shader's `in`s; one count+type per attribute; `x` pads bytes you skip
  (`"3f 3f 2x4"`); `/i` makes a buffer per-instance (`"3f 1f/i"`, drawn with `vao.render(instances=n)`).
  A wrong float count does not raise — it silently shuffles the data.  A buffer rewritten with
  `buffer.write(...)` keeps its byte size.
* An `in` or uniform the compiler optimised away is absent from `prog`: setting it raises
  `KeyError: 'u_name'`.  Remove the assignment, pad the attribute out, or guard with `if "u_name" in prog:`.
* Scalars and vectors: `prog["u"].value = 1.0` / `(r, g, b)`.  Matrices: numpy is row-major and GLSL is
  column-major, so `prog["u_m"].write(M.T.astype("f4").tobytes())` — never `.value = m.tolist()`.
* A sampler is a texture unit: `prog["u_tex"].value = k` once, and `tex.use(location=k)` on every call of
  `render` before the draw that samples it.
* `ctx.clear(...)` clears whatever framebuffer is bound.  Clear your own with `my_fbo.clear(...)`.  An
  offscreen framebuffer tests depth only when you attach one (`ctx.depth_renderbuffer((w, h))` as
  `depth_attachment=`); reading and writing the same texture in one pass is undefined.
* With `ctx.enable(moderngl.CULL_FACE)` a front face is counter-clockwise as seen from outside
  (`ctx.front_face` defaults to `"ccw"`); wind your triangles that way or the mesh is inside out.
* The context is GL 3.3 core: `ctx.line_width` above 1 and GL_QUADS do not exist (draw geometry for thick
  lines); `ctx.wireframe = True` is a debugging aid, not a look.

## Errors come back as `src/program.py:LINE: <Exception>`.  GLSL compile errors show the driver log with line
numbers inside the shader STRING; `KeyError: 'u_name'` is the optimised-away uniform above.

## Gates on the judged frames (`gl_frames`; the cap is the highest score the round can get)
| measured | threshold | finding | cap |
|---|---|---|---|
| any NaN / Inf pixel in the float read-back | ≥ 1 | `nan` ERROR | 0 |
| every frame black | over 97 % of pixels under luminance 0.03 in each frame | `black` ERROR | 0.5 |
| every frame blown | over 97 % of pixels over luminance 0.98 in each frame | `blown` ERROR | 0.5 |
| largest mean \|Δ\| between consecutive sampled frames | < 0.002 (motion planned) | `static` WARN | 0.5 |
| largest mean \|Δ\| between consecutive sampled frames | > 0.35 | `flicker` WARN | 0.7 |
| mean edge density over the frames | < 0.002 | `low_detail` WARN ("a near-flat gradient") | none — the judge sees it |
| a lint ERROR | any | | 0.6 |
| the build fails (import, setup, render, timeout) | | | 0 |
The judge then checks every acceptance item of the plan against the frames; an unmet `must` item caps 0.7.
