"""src/program.py — InstancedSpiralGalaxy
25,000 instanced particle points orbiting in 4 logarithmic spiral arms with additive glow,
separable HDR Gaussian bloom, and dynamic camera rotation over time.
"""
import math
import moderngl
import numpy as np

N_PARTICLES = 26000

PARTICLE_VERT = """#version 330 core
uniform mat4 u_view_proj;
uniform vec3 u_cam_right;
uniform vec3 u_cam_up;
uniform float u_time;

// Per-vertex quad offset
in vec2 in_quad;

// Per-instance attributes
in vec4 in_orbit;   // x: radius, y: base_angle, z: y_offset, w: size_scale
in vec4 in_color;   // rgb: base color, a: brightness multiplier
in vec2 in_params;  // x: angular_speed_factor, y: pulse_phase

out vec2 v_uv;
out vec4 v_color;

void main() {
    float r = in_orbit.x;
    float base_angle = in_orbit.y;
    float y_pos = in_orbit.z;
    float size = in_orbit.w;
    float speed_factor = in_params.x;
    float phase = in_params.y;

    // Differential Keplerian / flat rotation: omega(r) ~ 0.65 / (r^0.75 + 0.35)
    float omega = (0.75 / (pow(r + 0.25, 0.72) + 0.30)) * speed_factor;
    float theta = base_angle + omega * u_time;

    // Orbit position in galactic disc plane
    vec3 center = vec3(r * cos(theta), y_pos + 0.02 * sin(u_time * 1.5 + phase * 6.2831), r * sin(theta));

    // Dynamic shimmer pulse
    float pulse = 0.82 + 0.28 * sin(u_time * (1.2 + phase * 1.8) + phase * 12.566);

    // Billboard expansion
    vec3 world_pos = center + (u_cam_right * in_quad.x + u_cam_up * in_quad.y) * size;

    v_uv = in_quad;
    v_color = vec4(in_color.rgb, in_color.a * pulse);
    gl_Position = u_view_proj * vec4(world_pos, 1.0);
}
"""

PARTICLE_FRAG = """#version 330 core
in vec2 v_uv;
in vec4 v_color;
out vec4 fragColor;

void main() {
    float distSq = dot(v_uv, v_uv);
    if (distSq > 1.0) {
        discard;
    }
    // Dual exponential core + soft halo falloff
    float core = exp(-9.0 * distSq);
    float halo = exp(-3.0 * distSq) * (1.0 - sqrt(distSq));
    float alpha = clamp(core * 0.7 + halo * 0.45, 0.0, 1.0);

    fragColor = vec4(v_color.rgb * (v_color.a * alpha), 1.0);
}
"""

QUAD_VERT = """#version 330 core
in vec2 in_pos;
out vec2 v_uv;
void main() {
    v_uv = in_pos * 0.5 + 0.5;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

BLUR_FRAG = """#version 330 core
uniform sampler2D u_tex;
uniform vec2 u_dir;
in vec2 v_uv;
out vec4 fragColor;

void main() {
    // 9-tap Gaussian filter weights
    float weights[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
    vec3 acc = texture(u_tex, v_uv).rgb * weights[0];
    for (int i = 1; i < 5; i++) {
        vec2 offset = u_dir * float(i) * 1.4;
        acc += texture(u_tex, v_uv + offset).rgb * weights[i];
        acc += texture(u_tex, v_uv - offset).rgb * weights[i];
    }
    fragColor = vec4(acc, 1.0);
}
"""

COMPOSITE_FRAG = """#version 330 core
uniform sampler2D u_scene;
uniform sampler2D u_blur;
uniform float u_time;
uniform vec2 u_resolution;

in vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    p = fract(p * vec2(234.34, 435.345));
    p += dot(p, p + 34.23);
    return fract(p.x * p.y);
}

void main() {
    vec2 p = (v_uv - 0.5) * vec2(u_resolution.x / u_resolution.y, 1.0);
    vec3 scene = texture(u_scene, v_uv).rgb;
    vec3 bloom = texture(u_blur, v_uv).rgb;

    // Background cosmic void + faint stellar field
    vec3 spaceVoid = vec3(0.015, 0.010, 0.025);
    float bgNebula = 0.02 * sin(v_uv.x * 3.0 + u_time * 0.05) + 0.02 * cos(v_uv.y * 4.0 - u_time * 0.04);
    spaceVoid += vec3(0.018, 0.012, 0.035) * max(0.0, bgNebula + 0.5);

    // Subtle background twinkling stars
    vec2 starGrid = floor(v_uv * 400.0);
    float starRnd = hash21(starGrid);
    float star = 0.0;
    if (starRnd > 0.985) {
        float twinkle = 0.5 + 0.5 * sin(u_time * 2.5 + starRnd * 62.8);
        star = smoothstep(0.985, 1.0, starRnd) * twinkle * 0.35;
    }
    vec3 bgStars = vec3(0.8, 0.9, 1.0) * star;

    // Combine crisp particle layer + multi-tier bloom
    vec3 hdr = spaceVoid + bgStars + scene * 1.1 + bloom * 0.85;

    // Anamorphic horizontal core glare
    vec3 streak = texture(u_blur, vec2(v_uv.x, 0.5 + (v_uv.y - 0.5) * 0.2)).rgb;
    hdr += streak * vec3(0.12, 0.20, 0.40) * exp(-abs(v_uv.y - 0.5) * 16.0);

    // Exposure tonemapping: 1 - exp(-hdr * exposure)
    vec3 mapped = vec3(1.0) - exp(-hdr * 1.35);

    // Vignette
    float vig = smoothstep(1.2, 0.2, length(p));
    mapped *= 0.75 + 0.25 * vig;

    // Gamma correction
    vec3 finalCol = pow(clamp(mapped, 0.0, 1.0), vec3(1.0 / 2.2));
    fragColor = vec4(finalCol, 1.0);
}
"""


def _generate_galaxy_particles(n_count: int) -> np.ndarray:
    """Generate 4 logarithmic spiral arms + galactic nucleus particles."""
    rng = np.random.default_rng(42)

    n_core = int(n_count * 0.22)
    n_spiral = n_count - n_core

    # --- 1. Galactic Nucleus / Core Bulge ---
    r_core = rng.power(2.2, n_core) * 0.9 + 0.02
    theta_core = rng.uniform(0.0, 2.0 * math.pi, n_core)
    y_core = rng.normal(0.0, 0.12, n_core) * (1.0 - (r_core / 1.0) * 0.5)
    size_core = rng.uniform(0.025, 0.065, n_core) * (1.2 - r_core * 0.4)

    # Core colors: intense warm white to golden nucleus
    core_warm = np.array([1.25, 1.10, 0.88], dtype="f4")
    core_gold = np.array([1.10, 0.75, 0.42], dtype="f4")
    t_core = np.clip(r_core / 0.9, 0.0, 1.0)[:, None]
    rgb_core = core_warm * (1.0 - t_core) + core_gold * t_core
    alpha_core = rng.uniform(1.3, 2.2, n_core)[:, None]
    color_core = np.concatenate([rgb_core, alpha_core], axis=1)

    speed_core = rng.uniform(0.9, 1.1, n_core)[:, None]
    phase_core = rng.uniform(0.0, 1.0, n_core)[:, None]
    params_core = np.concatenate([speed_core, phase_core], axis=1)
    orbit_core = np.stack([r_core, theta_core, y_core, size_core], axis=1)

    # --- 2. Spiral Arms (4 Logarithmic Spiral Arms) ---
    n_arms = 4
    arm_indices = rng.integers(0, n_arms, n_spiral)
    arm_offsets = arm_indices * (2.0 * math.pi / n_arms)

    # Radial distribution spanning 0.5 to 5.2 units
    r_spiral = 0.5 + rng.power(1.3, n_spiral) * 4.7
    # Logarithmic spiral winding angle theta = (1/b) * ln(r / r0)
    b_spiral = 0.52
    theta_log = (np.log(np.maximum(r_spiral / 0.5, 0.01)) / b_spiral) + arm_offsets
    # Angular & radial scatter
    scatter_angle = rng.normal(0.0, 0.16 + 0.03 * r_spiral, n_spiral)
    theta_spiral = theta_log + scatter_angle
    # Disc thickness (thinner outer disc)
    y_spiral = rng.normal(0.0, 0.045, n_spiral) * np.exp(-r_spiral * 0.18)
    size_spiral = rng.uniform(0.020, 0.055, n_spiral)

    # Arm Color Gradient: Core Golden -> Mid-Arm Magenta/Violet -> Outer Cyan/Electric Blue
    c_mid = np.array([0.92, 0.32, 0.72], dtype="f4")      # Magenta star clusters
    c_outer = np.array([0.32, 0.65, 0.98], dtype="f4")    # Electric cyan/blue arms
    c_dust = np.array([0.55, 0.20, 0.65], dtype="f4")     # Ionized violet dust

    t_arm = np.clip((r_spiral - 0.5) / 4.2, 0.0, 1.0)[:, None]
    rgb_spiral = np.where(
        t_arm < 0.45,
        core_gold * (1.0 - t_arm / 0.45) + c_mid * (t_arm / 0.45),
        c_mid * (1.0 - (t_arm - 0.45) / 0.55) + c_outer * ((t_arm - 0.45) / 0.55),
    )
    # Jitter arm colors slightly with dust nebula tones
    dust_mask = (rng.uniform(0.0, 1.0, n_spiral)[:, None] > 0.78)
    rgb_spiral = np.where(dust_mask, c_dust * 0.9, rgb_spiral)

    alpha_spiral = rng.uniform(0.85, 1.5, n_spiral)[:, None]
    color_spiral = np.concatenate([rgb_spiral, alpha_spiral], axis=1)

    speed_spiral = rng.uniform(0.88, 1.12, n_spiral)[:, None]
    phase_spiral = rng.uniform(0.0, 1.0, n_spiral)[:, None]
    params_spiral = np.concatenate([speed_spiral, phase_spiral], axis=1)
    orbit_spiral = np.stack([r_spiral, theta_spiral, y_spiral, size_spiral], axis=1)

    # Combine all particles
    orbits = np.concatenate([orbit_core, orbit_spiral], axis=0).astype("f4")
    colors = np.concatenate([color_core, color_spiral], axis=0).astype("f4")
    params = np.concatenate([params_core, params_spiral], axis=0).astype("f4")

    # Interleave instance buffer: 4f (orbit) + 4f (color) + 2f (params) = 10 floats per instance
    inst_data = np.concatenate([orbits, colors, params], axis=1).astype("f4")
    return inst_data


def _perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    m = np.zeros((4, 4), dtype="f4")
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3] = (far + near) / (near - far), (2.0 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def _look_at(eye: np.ndarray, target: np.ndarray, up=(0.0, 1.0, 0.0)):
    eye = np.array(eye, dtype="f4")
    target = np.array(target, dtype="f4")
    up = np.array(up, dtype="f4")
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4, dtype="f4")
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m, s, u


def setup(ctx: moderngl.Context, width: int, height: int):
    # 1. Billboard Quad VBO (unit quad centered at origin)
    quad_verts = np.array(
        [-1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0],
        dtype="f4",
    )
    quad_vbo = ctx.buffer(quad_verts.tobytes())

    # 2. Particle Instanced Data & Programs
    particle_data = _generate_galaxy_particles(N_PARTICLES)
    particle_ibo = ctx.buffer(particle_data.tobytes())

    particle_prog = ctx.program(vertex_shader=PARTICLE_VERT, fragment_shader=PARTICLE_FRAG)
    particle_vao = ctx.vertex_array(
        particle_prog,
        [
            (quad_vbo, "2f", "in_quad"),
            (particle_ibo, "4f 4f 2f/i", "in_orbit", "in_color", "in_params"),
        ],
    )

    # 3. Offscreen Full-Res HDR Scene Target (for high dynamic range particle accumulation)
    scene_tex = ctx.texture((width, height), 4, dtype="f2")
    scene_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
    scene_tex.repeat_x = scene_tex.repeat_y = False
    scene_fbo = ctx.framebuffer(color_attachments=[scene_tex])

    # 4. Half-Res HDR Bloom Ping-Pong Targets
    bw, bh = max(1, width // 2), max(1, height // 2)
    blur_tex = [ctx.texture((bw, bh), 4, dtype="f2") for _ in range(2)]
    for tex in blur_tex:
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
    blur_fbo = [ctx.framebuffer(color_attachments=[t]) for t in blur_tex]

    # 5. Post-Process Programs & VAOs
    blur_prog = ctx.program(vertex_shader=QUAD_VERT, fragment_shader=BLUR_FRAG)
    comp_prog = ctx.program(vertex_shader=QUAD_VERT, fragment_shader=COMPOSITE_FRAG)

    blur_vao = ctx.vertex_array(blur_prog, [(quad_vbo, "2f", "in_pos")])
    comp_vao = ctx.vertex_array(comp_prog, [(quad_vbo, "2f", "in_pos")])

    proj = _perspective(48.0, width / height, 0.1, 80.0)

    return {
        "particle_prog": particle_prog,
        "particle_vao": particle_vao,
        "scene_fbo": scene_fbo,
        "scene_tex": scene_tex,
        "blur_fbo": blur_fbo,
        "blur_tex": blur_tex,
        "blur_prog": blur_prog,
        "blur_vao": blur_vao,
        "comp_prog": comp_prog,
        "comp_vao": comp_vao,
        "proj": proj,
        "size": (width, height),
        "bsize": (bw, bh),
    }


def render(ctx: moderngl.Context, state, t: float, frame: int, fbo: moderngl.Framebuffer) -> None:
    w, h = state["size"]
    bw, bh = state["bsize"]

    # Dynamic camera orbit & inclination pitch to reveal galactic disc 3D depth
    cam_yaw = t * 0.11
    cam_pitch = math.radians(28.0 + 8.0 * math.sin(t * 0.28))
    cam_dist = 6.4 + 0.3 * math.cos(t * 0.20)

    eye = np.array(
        [
            cam_dist * math.cos(cam_yaw) * math.cos(cam_pitch),
            cam_dist * math.sin(cam_pitch),
            cam_dist * math.sin(cam_yaw) * math.cos(cam_pitch),
        ],
        dtype="f4",
    )
    target = np.array([0.0, 0.0, 0.0], dtype="f4")
    view_mat, cam_right, cam_up = _look_at(eye, target)
    view_proj = state["proj"] @ view_mat

    # =========================================================================
    # PASS 1: GalaxyParticles (Additive HDR geometry render)
    # =========================================================================
    state["scene_fbo"].use()
    ctx.viewport = (0, 0, w, h)
    state["scene_fbo"].clear(0.0, 0.0, 0.0, 0.0)

    ctx.disable(moderngl.DEPTH_TEST)
    ctx.enable(moderngl.BLEND)
    # Additive blending for luminous cosmic plasma & star glow
    ctx.blend_func = (moderngl.ONE, moderngl.ONE)

    prog = state["particle_prog"]
    prog["u_view_proj"].write(view_proj.T.astype("f4").tobytes())
    prog["u_cam_right"].value = tuple(cam_right.tolist())
    prog["u_cam_up"].value = tuple(cam_up.tolist())
    prog["u_time"].value = float(t)

    state["particle_vao"].render(mode=moderngl.TRIANGLE_STRIP, instances=N_PARTICLES)

    ctx.disable(moderngl.BLEND)

    # =========================================================================
    # PASS 2 & 3: Separable Gaussian Bloom (Half-res HDR blur)
    # =========================================================================
    ctx.viewport = (0, 0, bw, bh)

    # Horizontal Blur Pass
    state["blur_fbo"][0].use()
    state["scene_tex"].use(location=0)
    state["blur_prog"]["u_tex"].value = 0
    state["blur_prog"]["u_dir"].value = (1.0 / bw, 0.0)
    state["blur_vao"].render(mode=moderngl.TRIANGLE_STRIP)

    # Vertical Blur Pass
    state["blur_fbo"][1].use()
    state["blur_tex"][0].use(location=0)
    state["blur_prog"]["u_dir"].value = (0.0, 1.0 / bh)
    state["blur_vao"].render(mode=moderngl.TRIANGLE_STRIP)

    # =========================================================================
    # PASS 4: Composite & Tonemap to Output Framebuffer
    # =========================================================================
    fbo.use()
    ctx.viewport = (0, 0, w, h)

    state["scene_tex"].use(location=0)
    state["blur_tex"][1].use(location=1)

    c_prog = state["comp_prog"]
    c_prog["u_scene"].value = 0
    c_prog["u_blur"].value = 1
    c_prog["u_time"].value = float(t)
    c_prog["u_resolution"].value = (float(w), float(h))

    state["comp_vao"].render(mode=moderngl.TRIANGLE_STRIP)
