---
name: 3d-lang-opengl-python
description: "Use when writing, refining or repairing a graphics-track moderngl program (language opengl_python): the two entry points setup/render and their exact signatures, the sampled-time trap (render is called at t = 0, 1, 2.5, 4, 6 s, in any order), the final pass into the supplied fbo, the AST lint's import allow-list and forbidden calls, the moderngl facts that decide whether the build runs, the gl-frames gate caps, and the 3dcode loop."
license: Apache-2.0
compatibility: raw OpenGL 3.3 core through moderngl in a headless EGL process owned by the 3dcode build; frames read back as float and flipped by the build.
metadata:
  verified: "2026-11-07"
---

# moderngl program — graphics

You write ONE module, `src/program.py`, with a lifetime: `setup(ctx, width, height)` runs
ONCE and returns your state; `render(ctx, state, t, frame, fbo)` may be called for any `t`,
any number of times, in any order, and draws the frame for that `t` into the framebuffer it is
handed.  No window, no context creation, no clock of your own.

## Files
```
src/program.py      REQUIRED  defines setup() and render()
src/*.glsl          optional  shader sources; read with Path(__file__).with_name("x.glsl").read_text()
```
Only these two globs are delivered; another `src/*.py` is a lint WARN (`stray_file`).
`references/interface.md` is the interface, executable.

## The two entry points (exact signatures)
```python
def setup(ctx: moderngl.Context, width: int, height: int):      # called ONCE; returns your state
def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    ...                                                          # multi-pass into your own FBOs allowed
    fbo.use()   # the build's framebuffer (RGBA f4 + depth) — draw the FINAL image into it LAST
```
* The build makes ONE sorted schedule of the judged times (t = 0, 1, 2.5, 4, 6 s, clipped to the
  plan's duration, never fewer than 3) and about 12 preview times; for each it binds `fbo`,
  clears it to (0, 0, 0, 1) with depth 1.0, sets `ctx.viewport` to the full size and calls
  `render`.  `frame` is the index into that merged schedule — not a 60 fps counter.
* **The sampled-time trap**: a simulation that advances one step per call has taken five steps
  by the last judged frame.  Pose from `t`; if a state must integrate, integrate to `t` inside
  the call.  Same `t` → same image, whatever was rendered before.
* After `render` the build reads `fbo` back as float and flips the rows: draw with y up, do not
  flip in the shader.  If you draw into your own FBOs, call `fbo.use()` and reset
  `ctx.viewport = (0, 0, width, height)` before the final pass.  Never call `ctx.screen`.
* Width / height come from the plan's resolution (1280 × 720 when unusable; at most
  1920 × 1080).  The whole schedule renders inside 300 s or the build fails.

## The lint (an ERROR caps the score at 0.6)
* Imports allowed: `moderngl`, `numpy`, `math`, `random`, `struct`, `array`, `pathlib`,
  `typing`, `dataclasses`, `itertools`, `functools`, `collections`, `colorsys`, `enum`,
  `__future__`; anything else is an ERROR.
* ERROR: window / input libraries (glfw, pygame, pyglet, PyQt, PySide, tkinter,
  moderngl_window, sdl2, wx, PyOpenGL); `os`, `sys`, `io`, `time`, `datetime`, `signal`,
  `tempfile`, subprocess and network modules; `open` / `exec` / `eval` / `compile` /
  `__import__` / `input` / `exit` / `quit` / `breakpoint`; file-system writes (`.write_text` /
  `.write_bytes` / `.unlink` / `.mkdir` / `.rmdir` / `.rename`); `moderngl.create_context()` /
  `create_standalone_context()`; `time.time()` / `perf_counter()` / `monotonic()`;
  `gl_FragColor`, `attribute` / `varying` in a shader string.
* WARN: a `#version` other than `330 core` (`glsl_version`); `random.*` without
  `random.seed(...)` or a `default_rng(...)` (`unseeded_random`) — seed in `setup`; a file over
  120 000 characters; an extra `src/*.py`.
* No `cleanup()` callback: when you replace resources, release each VAO, buffer, texture,
  framebuffer and program you own; never release the supplied `ctx` or `fbo`.

## moderngl facts that decide whether the build runs
* Every shader string starts with `#version 330 core` on its first line (nothing before it),
  uses `in` / `out` and declares `out vec4 fragColor;`.
* `ctx.vertex_array(prog, [(buffer, "3f 3f 2f", "in_pos", "in_normal", "in_uv")], index_buffer=ibo)`:
  names match the vertex shader's `in`s; `x` pads bytes (`"3f 3f 2x4"`); `/i` is per-instance
  (`"3f 1f/i"`, `vao.render(instances=n)`).  A wrong float count silently shuffles the data.
  `buffer.write(...)` keeps the byte size.
* An `in` or uniform the compiler optimised away is absent from `prog`: setting it raises
  `KeyError: 'u_name'` — remove it, pad it, or guard with `if "u_name" in prog:`.
* `prog["u"].value = 1.0` / `(r, g, b)`; matrices: numpy is row-major and GLSL column-major, so
  `prog["u_m"].write(M.T.astype("f4").tobytes())`, never `.value = m.tolist()`.
* A sampler is a texture unit: `prog["u_tex"].value = k` once, `tex.use(location=k)` on every
  `render` before the draw that samples it.
* `ctx.clear(...)` clears whatever framebuffer is bound (`my_fbo.clear(...)` for your own).  An
  offscreen framebuffer tests depth only with a `depth_attachment=` (`ctx.depth_renderbuffer`);
  reading and writing the same texture in one pass is undefined.
* With `CULL_FACE` a front face is counter-clockwise from outside (`ctx.front_face` defaults to
  `"ccw"`; wound the other way the mesh is inside out).  GL 3.3 core: no
  `line_width` above 1, no GL_QUADS; `ctx.wireframe = True` is a debugging aid.
* Errors come back as `src/program.py:LINE: <Exception>`; GLSL compile errors show the driver
  log with line numbers inside the shader STRING.

## What is measured (`3dcode gl-frames`; the cap is the highest score the round can get)
| measured | threshold | finding | cap |
|---|---|---|---|
| any NaN / Inf pixel | ≥ 1 | `nan` ERROR | 0 |
| every frame black | > 97 % of pixels under luminance 0.03 in each frame | `black` ERROR | 0.5 |
| every frame blown | > 97 % over 0.98 in each frame | `blown` ERROR | 0.5 |
| largest mean \|Δ\| between consecutive frames | < 0.002 (motion planned) | `static` WARN | 0.5 |
| largest mean \|Δ\| between consecutive frames | > 0.35 | `flicker` WARN | 0.7 |
| mean edge density | < 0.002 | `low_detail` WARN ("a near-flat gradient") | none |
| a lint ERROR | any | | 0.6 |
| the build fails (import, setup, render, timeout) | | | 0 |
An unmet `must` acceptance item caps at 0.7.  A periodic wrap that jumps between two sampled
times is the flicker finding.  Get the frame into a readable band before spending on hue and
palette, which are invisible on dark, low-chroma pixels.  The judge's named defects are the
`3d-lang-glsl-shader` skill's (`flat_fill`, `comb_artefact`, `opaque_shapes_for_light`,
`no_dark_range`, `white_wash`, `candy_saturation`, `stretched_aspect`, `placeholder_look`).

## The loop
write → `3dcode gl-probe` (imports? one frame at t = 1 s) → fix everything it reports → repeat →
`3dcode gl-frames` → OPEN the sheet and compare with the plan's key visuals → `3dcode check`
clean.  Do not finish while `gl-probe` reports anything.  Clear NaN, black, blown and static
before adding the next pass; deleting a pass is a legitimate fix.
