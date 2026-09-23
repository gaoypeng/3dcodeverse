# opengl_python authoring contract (raw OpenGL 3.3 core through moderngl, headless)

You write ONE python module, `src/program.py`, that draws frames with raw OpenGL calls (moderngl API)
and inline GLSL.  The harness imports it in a headless EGL process, creates the context and an output
framebuffer, calls your two functions, reads the pixels back (PNG per frame at t = 0, 1, 2.5, 4, 6 s +
preview GIF), measures and judges them.  The code is the deliverable.

## Files
```
src/program.py      REQUIRED  defines setup() and render()
src/*.glsl          optional  shader sources you ship; read with Path(__file__).with_name("x.glsl").read_text()
```

## The two entry points (exact signatures)
```python
def setup(ctx: moderngl.Context, width: int, height: int):      # called ONCE
    ...create programs, buffers, VAOs, textures, your own FBOs...
    return state                                                 # any object; passed back to render()

def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    ...draw the frame for time t (seconds) — multi-pass allowed into your own FBOs...
    fbo.use()   # the harness framebuffer (RGBA float, depth attached) — draw the FINAL image into it LAST
```
* `fbo` is already bound and cleared to (0,0,0,1) + depth 1.0 when render() is called; `ctx.viewport` is the
  full size.  If you switch to your own FBOs, call `fbo.use()` and reset `ctx.viewport = (0, 0, width, height)` before the
  final pass.  Never call `ctx.screen`.
* `t` is absolute time in seconds; `frame` is the sample index (0, 1, 2 …; NOT a 60 fps counter).  Frames must be a
  pure function of `t` (same t → same image).  Seed any randomness in setup (`np.random.default_rng(0)`).
* Width/height come from the plan's resolution (default 1280x720).

## Allowed / forbidden (AST lint enforces)
* Imports: `moderngl`, `numpy`, `math`, `random`, `struct`, `array`, `pathlib`, `typing`, `dataclasses`, `itertools`,
  `functools`, `collections`, `colorsys`.  Nothing else — no glfw / pygame / pyglet / PyOpenGL / moderngl_window / PIL.
* No `moderngl.create_context()` / `create_standalone_context()` (the harness owns the context), no windows, no
  `time.time()` / clocks, no `open()` / file writes / subprocess / network / `os` / `sys`.
* GLSL strings: `#version 330 core`, `in`/`out` qualifiers, `out vec4 fragColor;` (no gl_FragColor / varying).
* Keep frame cost sane.

## Errors come back as `src/program.py:LINE: <Exception>`.  GLSL compile errors show the driver log with line
numbers inside the shader STRING; moderngl raises `KeyError: 'u_name'` when you set a uniform the compiler
optimised away (unused) — remove the assignment or guard with `if "u_name" in prog:`.

## Gates on the frames: NaN/Inf pixels → score 0; all frames identical (no motion) → ≤ 0.5; black / blown-out → ≤ 0.5.

## COMPLETE VERIFIED EXAMPLE — `src/program.py` (200 instanced rotating cubes, orbiting camera, separable blur bloom)
```python
"""src/program.py — 200 instanced rotating cubes in a swirling cloud, lit, with a bloom-style post blur.
Harness calls setup(ctx, width, height) once, then render(ctx, state, t, frame, fbo) per frame."""
import math

import moderngl
import numpy as np

N_CUBES = 200
PALETTE = np.array([[0.95, 0.35, 0.25], [0.25, 0.65, 0.95], [0.95, 0.80, 0.30], [0.55, 0.90, 0.55]], dtype="f4")

CUBE_VERT = """#version 330 core
uniform mat4 u_view_proj;
uniform float u_time;
in vec3 in_pos;             // per vertex
in vec3 in_normal;          // per vertex
in vec3 in_offset;          // per instance: orbit centre
in vec3 in_color;           // per instance
in float in_phase;          // per instance: spin phase / speed
out vec3 v_normal;
out vec3 v_color;
out vec3 v_world;
mat3 rotY(float a) { float c = cos(a), s = sin(a); return mat3(c, 0, s, 0, 1, 0, -s, 0, c); }
mat3 rotX(float a) { float c = cos(a), s = sin(a); return mat3(1, 0, 0, 0, c, -s, 0, s, c); }
void main() {
    float spin = u_time * (0.6 + in_phase) + in_phase * 6.2831;
    mat3 R = rotY(spin) * rotX(spin * 0.7);
    // orbit the whole cloud slowly around Y and bob
    vec3 centre = rotY(u_time * 0.25) * in_offset + vec3(0.0, 0.15 * sin(u_time * 1.3 + in_phase * 6.2831), 0.0);
    vec3 world = centre + R * (in_pos * 0.18);
    v_normal = R * in_normal;
    v_color = in_color;
    v_world = world;
    gl_Position = u_view_proj * vec4(world, 1.0);
}
"""
CUBE_FRAG = """#version 330 core
in vec3 v_normal;
in vec3 v_color;
in vec3 v_world;
out vec4 fragColor;
void main() {
    vec3 n = normalize(v_normal);
    vec3 l1 = normalize(vec3(0.5, 0.9, 0.4)), l2 = normalize(vec3(-0.6, -0.2, -0.7));
    float diff = max(dot(n, l1), 0.0) + 0.35 * max(dot(n, l2), 0.0);
    float rim = pow(1.0 - max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0), 3.0);
    vec3 col = v_color * (0.15 + 0.85 * diff) + 0.4 * rim * v_color;
    float fog = exp(-0.12 * max(0.0, -v_world.z - 4.0));
    fragColor = vec4(col * fog, 1.0);
}
"""
QUAD_VERT = """#version 330 core
in vec2 in_pos;
out vec2 v_uv;
void main() { v_uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""
BLUR_FRAG = """#version 330 core
uniform sampler2D u_tex;
uniform vec2 u_dir;         // (1/w, 0) or (0, 1/h)
in vec2 v_uv;
out vec4 fragColor;
void main() {
    float w[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
    vec3 acc = texture(u_tex, v_uv).rgb * w[0];
    for (int i = 1; i < 5; i++) {
        acc += texture(u_tex, v_uv + u_dir * float(i) * 2.0).rgb * w[i];
        acc += texture(u_tex, v_uv - u_dir * float(i) * 2.0).rgb * w[i];
    }
    fragColor = vec4(acc, 1.0);
}
"""
COMPOSITE_FRAG = """#version 330 core
uniform sampler2D u_scene;
uniform sampler2D u_blur;
in vec2 v_uv;
out vec4 fragColor;
void main() {
    vec3 scene = texture(u_scene, v_uv).rgb;
    vec3 bloom = texture(u_blur, v_uv).rgb;
    vec2 p = v_uv - 0.5;
    vec3 bg = mix(vec3(0.05, 0.04, 0.10), vec3(0.12, 0.10, 0.22), 1.0 - length(p) * 1.2);  // dark vignette gradient
    vec3 col = bg + scene + 0.5 * bloom;
    col = col / (1.0 + col);                                   // tonemap
    fragColor = vec4(pow(col, vec3(1.0 / 2.2)), 1.0);
}
"""


def _cube_mesh() -> np.ndarray:
    """36 vertices × (pos3 + normal3), unit cube centred at origin."""
    faces = [((0, 0, 1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)),
             ((0, 0, -1), (1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)),
             ((1, 0, 0), (1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)),
             ((-1, 0, 0), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)),
             ((0, 1, 0), (-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)),
             ((0, -1, 0), (-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1))]
    data = []
    for n, a, b, c, d in faces:
        for v in (a, b, c, a, c, d):
            data += [v[0] * 0.5, v[1] * 0.5, v[2] * 0.5, *n]
    return np.array(data, dtype="f4")


def _perspective(fov_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    m = np.zeros((4, 4), dtype="f4")
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3], m[3, 2] = (far + near) / (near - far), 2 * far * near / (near - far), -1.0
    return m


def _look_at(eye, target, up=(0.0, 1.0, 0.0)):
    eye, target, up = (np.array(v, dtype="f4") for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4, dtype="f4")
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def setup(ctx: moderngl.Context, width: int, height: int):
    rng = np.random.default_rng(7)                      # deterministic layout
    # instance data: offsets on a thick torus-ish ring, colours from the palette, phases
    ang = rng.uniform(0, 2 * math.pi, N_CUBES)
    rad = rng.uniform(1.2, 2.6, N_CUBES)
    offsets = np.stack([rad * np.cos(ang), rng.uniform(-0.8, 0.8, N_CUBES), rad * np.sin(ang)], axis=1).astype("f4")
    colors = PALETTE[rng.integers(0, len(PALETTE), N_CUBES)]
    phases = rng.uniform(0, 1, N_CUBES).astype("f4")
    inst = np.concatenate([offsets, colors, phases[:, None]], axis=1).astype("f4")   # 7 floats per instance

    cube_prog = ctx.program(vertex_shader=CUBE_VERT, fragment_shader=CUBE_FRAG)
    vbo = ctx.buffer(_cube_mesh().tobytes())
    ibo = ctx.buffer(inst.tobytes())
    cube_vao = ctx.vertex_array(cube_prog, [(vbo, "3f 3f", "in_pos", "in_normal"),
                                            (ibo, "3f 3f 1f/i", "in_offset", "in_color", "in_phase")])
    # offscreen scene FBO (with depth) + two half-res blur FBOs (ping-pong)
    scene_tex = ctx.texture((width, height), 4, dtype="f2")
    scene_fbo = ctx.framebuffer(color_attachments=[scene_tex], depth_attachment=ctx.depth_renderbuffer((width, height)))
    bw, bh = width // 2, height // 2
    blur_tex = [ctx.texture((bw, bh), 4, dtype="f2") for _ in range(2)]
    for tex in blur_tex + [scene_tex]:
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
    blur_fbo = [ctx.framebuffer(color_attachments=[tex]) for tex in blur_tex]

    quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    blur_prog = ctx.program(vertex_shader=QUAD_VERT, fragment_shader=BLUR_FRAG)
    comp_prog = ctx.program(vertex_shader=QUAD_VERT, fragment_shader=COMPOSITE_FRAG)
    blur_vao = ctx.vertex_array(blur_prog, [(quad, "2f", "in_pos")])
    comp_vao = ctx.vertex_array(comp_prog, [(quad, "2f", "in_pos")])
    proj = _perspective(50.0, width / height, 0.1, 50.0)
    return dict(cube_prog=cube_prog, cube_vao=cube_vao, scene_fbo=scene_fbo, scene_tex=scene_tex, blur_fbo=blur_fbo,
                blur_tex=blur_tex, blur_prog=blur_prog, blur_vao=blur_vao, comp_prog=comp_prog, comp_vao=comp_vao,
                proj=proj, size=(width, height), bsize=(bw, bh))


def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    w, h = state["size"]
    bw, bh = state["bsize"]
    # pass 1: cubes into the scene FBO (depth on)
    state["scene_fbo"].use()
    ctx.viewport = (0, 0, w, h)
    state["scene_fbo"].clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
    ctx.enable(moderngl.DEPTH_TEST)
    eye = (5.5 * math.cos(t * 0.15), 1.8 + 0.5 * math.sin(t * 0.2), 5.5 * math.sin(t * 0.15))
    view_proj = state["proj"] @ _look_at(eye, (0.0, 0.0, 0.0))
    state["cube_prog"]["u_view_proj"].write(view_proj.T.astype("f4").tobytes())     # column-major for GLSL
    state["cube_prog"]["u_time"].value = t
    state["cube_vao"].render(mode=moderngl.TRIANGLES, instances=N_CUBES)
    ctx.disable(moderngl.DEPTH_TEST)
    # pass 2: separable blur, half resolution, horizontal then vertical
    ctx.viewport = (0, 0, bw, bh)
    state["blur_fbo"][0].use()
    state["scene_tex"].use(location=0)
    state["blur_prog"]["u_tex"].value = 0
    state["blur_prog"]["u_dir"].value = (1.0 / bw, 0.0)
    state["blur_vao"].render(mode=moderngl.TRIANGLE_STRIP)
    state["blur_fbo"][1].use()
    state["blur_tex"][0].use(location=0)
    state["blur_prog"]["u_dir"].value = (0.0, 1.0 / bh)
    state["blur_vao"].render(mode=moderngl.TRIANGLE_STRIP)
    # pass 3: composite into the harness framebuffer (ALWAYS the last thing you draw)
    fbo.use()
    ctx.viewport = (0, 0, w, h)
    state["scene_tex"].use(location=0)
    state["blur_tex"][1].use(location=1)
    state["comp_prog"]["u_scene"].value = 0
    state["comp_prog"]["u_blur"].value = 1
    state["comp_vao"].render(mode=moderngl.TRIANGLE_STRIP)
```
