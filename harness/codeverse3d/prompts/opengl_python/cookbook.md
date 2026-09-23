# opengl_python cookbook — copyable moderngl snippets (GL 3.3 core, headless; harness calls setup()/render())

## Fullscreen quad (every post-process / procedural pass starts here)
```python
QUAD_VERT = """#version 330 core
in vec2 in_pos; out vec2 v_uv;
void main() { v_uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""
quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
prog = ctx.program(vertex_shader=QUAD_VERT, fragment_shader=FRAG)     # FRAG: `in vec2 v_uv; out vec4 fragColor; ...`
vao = ctx.vertex_array(prog, [(quad, "2f", "in_pos")])
vao.render(mode=moderngl.TRIANGLE_STRIP)                               # 4 vertices
```
A fragment-only effect (Shadertoy style) in this language = one quad pass with `uniform float u_time; uniform vec2 u_resolution;`
set from render(): `prog["u_time"].value = t`.

## VBO / VAO with interleaved attributes, index buffer
```python
verts = np.array([...], dtype="f4")            # x y z nx ny nz u v per vertex
vbo = ctx.buffer(verts.tobytes())
ibo = ctx.buffer(np.array([...], dtype="i4").tobytes())
vao = ctx.vertex_array(prog, [(vbo, "3f 3f 2f", "in_pos", "in_normal", "in_uv")], index_buffer=ibo)
vao.render(mode=moderngl.TRIANGLES)
# attribute names MUST match the `in` names in the vertex shader; unused `in`s are optimised away → skip them
# with "3f 3f 2x4" (x = padding) or check `prog.get("in_uv", None)`.
```

## Instancing (hundreds–thousands of objects in one draw)
```python
inst = np.concatenate([offsets, colors, scales[:, None]], axis=1).astype("f4")    # per-instance rows
ibo = ctx.buffer(inst.tobytes())
vao = ctx.vertex_array(prog, [(vbo, "3f 3f", "in_pos", "in_normal"),
                              (ibo, "3f 3f 1f/i", "in_offset", "in_color", "in_scale")])   # "/i" = per instance
vao.render(mode=moderngl.TRIANGLES, instances=len(inst))
# vertex shader: `in vec3 in_offset; ... vec3 world = in_offset + in_scale * (R * in_pos);`
# animate per-instance in the SHADER from u_time + a per-instance phase attribute → no per-frame buffer uploads
```
Updating instance data per frame (CPU particles): `ibo.write(new_rows.astype("f4").tobytes())` (keep row count constant; use `orphan()` for speed).

## Uniforms and matrices (numpy → GLSL is column-major: transpose!)
```python
prog["u_color"].value = (1.0, 0.5, 0.2)                 # vec3
prog["u_time"].value = float(t)                          # float
prog["u_mvp"].write(mvp.T.astype("f4").tobytes())       # mat4: transpose a row-major numpy matrix
prog["u_tex"].value = 0                                   # sampler → texture unit; then tex.use(location=0)
if "u_optional" in prog: prog["u_optional"].value = 1.0  # uniforms unused by the shader are optimised away → KeyError
```
```python
def perspective(fov_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fov_deg) / 2); m = np.zeros((4, 4), dtype="f4")
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3], m[3, 2] = (far + near) / (near - far), 2 * far * near / (near - far), -1.0
    return m
def look_at(eye, target, up=(0, 1, 0)):
    eye, target, up = (np.array(v, dtype="f4") for v in (eye, target, up))
    f = target - eye; f /= np.linalg.norm(f); s = np.cross(f, up); s /= np.linalg.norm(s); u = np.cross(s, f)
    m = np.identity(4, dtype="f4"); m[0, :3], m[1, :3], m[2, :3] = s, u, -f; m[:3, 3] = -m[:3, :3] @ eye
    return m
def rot_y(a): c, s = math.cos(a), math.sin(a); return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], dtype="f4")
mvp = proj @ view @ model          # then prog["u_mvp"].write(mvp.T.astype("f4").tobytes())
# With nonuniform scale, normals use inverse transpose, never mat3(model).
normal_matrix = np.linalg.inv(model[:3, :3]).T
prog["u_normal_matrix"].write(normal_matrix.T.astype("f4").tobytes())
```

## Textures (procedural data, noise, LUTs)
```python
rng = np.random.default_rng(0)
noise = rng.random((256, 256, 4), dtype=np.float32)
tex = ctx.texture((256, 256), 4, noise.astype("f4").tobytes(), dtype="f4")    # or uint8 data with dtype default
tex.filter = (moderngl.LINEAR, moderngl.LINEAR); tex.repeat_x = tex.repeat_y = True
tex.use(location=0); prog["u_noise"].value = 0
# 3D textures: ctx.texture3d((32,32,32), 1, data.tobytes(), dtype="f4")
```

## FBOs: offscreen scene, ping-pong feedback, MRT
```python
color = ctx.texture((w, h), 4, dtype="f2")                     # f2 = half float (HDR); "f4" = float32
depth = ctx.depth_renderbuffer((w, h))
scene_fbo = ctx.framebuffer(color_attachments=[color], depth_attachment=depth)
scene_fbo.use(); ctx.viewport = (0, 0, w, h); scene_fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
...draw...
# ping-pong (feedback / simulation): two FBOs, read A write B, then swap
pp_tex = [ctx.texture((w, h), 4, dtype="f4") for _ in range(2)]; pp_fbo = [ctx.framebuffer(color_attachments=[t]) for t in pp_tex]
read, write = state["pp"]; pp_fbo[write].use(); pp_tex[read].use(location=0); sim_vao.render(moderngl.TRIANGLE_STRIP); state["pp"] = (write, read)
# MRT: ctx.framebuffer(color_attachments=[albedo, normal, position], depth_attachment=depth) with
#      `layout(location = 0) out vec4 out_albedo; layout(location = 1) out vec4 out_normal; ...` in the fragment shader
```
ALWAYS end render() with `fbo.use(); ctx.viewport = (0, 0, width, height)` and the final draw into the harness `fbo`.

## Separable Gaussian blur (bloom)
```glsl
#version 330 core
uniform sampler2D u_tex; uniform vec2 u_dir; in vec2 v_uv; out vec4 fragColor;
void main() {
    float w[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
    vec3 acc = texture(u_tex, v_uv).rgb * w[0];
    for (int i = 1; i < 5; i++) { acc += texture(u_tex, v_uv + u_dir * float(i)).rgb * w[i]; acc += texture(u_tex, v_uv - u_dir * float(i)).rgb * w[i]; }
    fragColor = vec4(acc, 1.0);
}
```
Run horizontally (`u_dir = (1/w, 0)`) into FBO A, vertically (`(0, 1/h)`) into FBO B, at half resolution; composite `scene + k * blur`, tonemap `c/(1+c)`, gamma `pow(c, 1/2.2)`.

## Lighting in GLSL (Blinn-Phong + rim + fog)
This short stylized lighting model is for sketches. For a realistic material, use the GGX
`pbrDirect` / `fresnelSchlick` functions in the GLSL cookbook's Physical surface lighting section;
keep all light and albedo inputs linear and tonemap once at the final output. Use the Volume
lighting helpers there for extinction and normalized phase functions in smoke/cloud raymarches.
```glsl
vec3 n = normalize(v_normal); vec3 l = normalize(vec3(0.5, 0.9, 0.4)); vec3 v = normalize(u_cam - v_world);
float diff = max(dot(n, l), 0.0); float spec = pow(max(dot(n, normalize(l + v)), 0.0), 48.0);
float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0);
vec3 col = albedo * (0.15 + 0.85 * diff) + 0.3 * spec + 0.3 * rim * albedo;
col = mix(col, fogColor, 1.0 - exp(-0.03 * dist));
```

## Geometry generators (numpy)
```python
def grid_mesh(nx, nz, size):            # flat plane for water / terrain (vertex shader displaces y)
    xs = np.linspace(-size, size, nx, dtype="f4"); zs = np.linspace(-size, size, nz, dtype="f4")
    X, Z = np.meshgrid(xs, zs); verts = np.stack([X, np.zeros_like(X), Z], axis=-1).reshape(-1, 3)
    idx = []
    for j in range(nz - 1):
        for i in range(nx - 1):
            a = j * nx + i; idx += [a, a + nx, a + 1, a + 1, a + nx, a + nx + 1]
    return verts.astype("f4"), np.array(idx, dtype="i4")
def sphere_mesh(rings, sectors, r):     # pos3 + normal3 interleaved
    data, idx = [], []
    for j in range(rings + 1):
        v = j / rings; phi = v * math.pi
        for i in range(sectors + 1):
            u = i / sectors; th = u * 2 * math.pi
            n = (math.sin(phi) * math.cos(th), math.cos(phi), math.sin(phi) * math.sin(th))
            data += [r * n[0], r * n[1], r * n[2], *n]
    for j in range(rings):
        for i in range(sectors):
            a = j * (sectors + 1) + i; idx += [a, a + 1, a + sectors + 1, a + 1, a + sectors + 2, a + sectors + 1]
    return np.array(data, dtype="f4"), np.array(idx, dtype="i4")
```

## GL state you may need
```python
ctx.enable(moderngl.DEPTH_TEST); ctx.disable(moderngl.DEPTH_TEST)
ctx.enable(moderngl.BLEND); ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA   # or ONE, ONE for additive glow
ctx.enable(moderngl.CULL_FACE); ctx.front_face = "ccw"
ctx.point_size = 4.0   # with mode=moderngl.POINTS (gl_PointSize in the vertex shader needs ctx.enable(moderngl.PROGRAM_POINT_SIZE))
ctx.line_width = 1.0   # > 1 is unsupported on core profiles: draw quads for thick lines
```

## PITFALLS (each one cost a real run)
* `KeyError: 'u_x'` — the uniform/attribute is unused in the GLSL and got optimised away; remove the assignment or guard with `in prog`.
* Matrices: numpy is row-major, GLSL column-major → `write(m.T.astype("f4").tobytes())`; never `value = m.tolist()`.
* Read-back rows are bottom-up (the harness flips them): draw with y up, do NOT flip in your shader.
* Forgetting `fbo.use()` + viewport reset before the final pass → the harness frame stays black (cap 0.5).
* `ctx.clear()` clears whatever FBO is bound; clear your own FBOs explicitly (`my_fbo.clear(...)`).
* Depth: attach a depth renderbuffer to your offscreen FBOs or z-fighting / wrong occlusion; disable DEPTH_TEST for fullscreen passes.
* `#version 330 core` at the very top of every shader string (no blank line before it); `out vec4 fragColor;` not gl_FragColor.
* Attribute format strings: count+type per attribute (`"3f 3f 2f"`), `/i` for per-instance buffers; mismatched float counts silently shuffle data.
* Big per-frame uploads (100k particles in numpy each frame) are slow; move animation into the vertex shader via `u_time`.
* Deterministic: `np.random.default_rng(SEED)` in setup, never `random.random()` inside render without a seed.
* Textures sampled with `texture()` need `.use(location=k)` every frame *and* `prog["u_tex"].value = k`.
* Lines wider than 1 px and GL_QUADS do not exist in core profile; `ctx.wireframe = True` works for debugging only.
* Half-float (`f2`) colour targets clip at ~65504 — tonemap before writing bright accumulations.
* When replacing resources, release each owned VAO, buffer, texture, framebuffer and program;
  a framebuffer does not own its attachments. The harness tears down the context at job end
  and has no `cleanup()` callback. Do not release its supplied context or final framebuffer.
