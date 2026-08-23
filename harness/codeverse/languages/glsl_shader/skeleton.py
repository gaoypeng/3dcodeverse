"""Skeleton for the glsl_shader language: a RUNNABLE starter pair
(``src/common.glsl`` helpers + ``src/shader.frag`` entry) carrying the plan's
passes / key visuals / motion as TODO comments and the uniform contract as a
header comment.  The baseline generator rewrites it; the harness can already
build and render it (gradient sky + fbm haze + moving sun)."""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.plan import GraphicsPlan, Plan
from codeverse.workspace import Workspace

COMMON_GLSL = """// src/common.glsl — helpers pasted above shader.frag by the harness (no #include needed).
// Keep ONLY functions / constants here; no main(), no uniforms, no #version.
#define PI 3.14159265359
#define TAU 6.28318530718

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
vec2  hash22(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973)); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.xx + p3.yz) * p3.zy); }

// value noise 2D (smooth, 0..1)
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1, 0)), u.x),
               mix(hash12(i + vec2(0, 1)), hash12(i + vec2(1, 1)), u.x), u.y);
}

// fractal brownian motion, 5 octaves (constant loop bound)
float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    mat2 rot = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 5; i++) { v += a * noise(p); p = rot * p * 2.02; a *= 0.5; }
    return v;
}

mat2 rot2(float a) { float c = cos(a), s = sin(a); return mat2(c, -s, s, c); }

// cosine palette (IQ): t in 0..1
vec3 palette(float t, vec3 a, vec3 b, vec3 c, vec3 d) { return a + b * cos(TAU * (c * t + d)); }

// filmic-ish tonemap + gamma
vec3 tonemap(vec3 c) { c = c / (1.0 + c); return pow(clamp(c, 0.0, 1.0), vec3(1.0 / 2.2)); }
"""

_SHADER_TEMPLATE = """// src/shader.frag — {title}
// CONTRACT (the harness prepends `#version 330 core` + these uniforms + `out vec4 fragColor`; DO NOT redeclare them):
//   uniform float u_time;  uniform vec2 u_resolution;  uniform vec2 u_mouse;  uniform int u_frame;
//   uniform sampler2D u_prev;  (previous frame of this pass)   uniform sampler2D u_noise;  (256x256 RGBA noise, repeat)
//   uniform sampler2D u_buffer_a;  (output of src/buffer_a.frag when that file exists)
//   iTime / iResolution / iFrame / iMouse / iChannel0(=u_prev) / iChannel1(=u_noise) are #defined for Shadertoy ports.
// Entry: void mainImage(out vec4 fragColor, in vec2 fragCoord) — fragCoord in pixels, origin BOTTOM-LEFT.
// Helpers available from src/common.glsl: hash11 hash12 hash22 noise fbm rot2 palette tonemap PI TAU.
// Duration {duration:g}s; judged frames at t = 0, 1, 2.5, 4, 6 s.  Everything must MOVE with u_time.
//
// PLAN — {summary}
// Style: {style}
{passes}
{visuals}
// Motion: {motion}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {{
    vec2 uv = fragCoord / u_resolution.xy;                 // 0..1
    vec2 p  = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;  // aspect-correct, centred
    float t = u_time;

    // TODO replace this placeholder look with the plan's passes above
    vec3 sky = mix(vec3(0.95, 0.55, 0.25), vec3(0.10, 0.20, 0.45), smoothstep(-0.2, 0.6, p.y));
    vec2 sunPos = vec2(0.35 * cos(t * 0.3), 0.15 + 0.1 * sin(t * 0.3));
    float sun = exp(-40.0 * length(p - sunPos));
    float haze = fbm(p * 3.0 + vec2(t * 0.15, 0.0));
    vec3 col = sky + vec3(1.0, 0.8, 0.5) * sun + 0.25 * haze * vec3(0.9, 0.7, 0.6);
    col *= 1.0 - 0.35 * length(p);                        // vignette
    fragColor = vec4(tonemap(col), 1.0);
}}
"""


def _comment_lines(prefix: str, items: list[str]) -> str:
    if not items:
        return f"// {prefix}: (none)"
    return "\n".join(f"//   TODO {prefix} {i + 1}: {x}" for i, x in enumerate(items))


def shader_source(plan: GraphicsPlan | None) -> str:
    title = plan.title if plan else "untitled shader"
    summary = plan.summary if plan else ""
    style = plan.style if plan else ""
    motion = plan.motion if plan else "animate with u_time"
    duration = plan.duration_s if plan else 8.0
    passes = [f"{p.name} ({p.kind}): {p.description}" for p in plan.passes] if plan else []
    visuals = list(plan.key_visuals) if plan else []
    return _SHADER_TEMPLATE.format(
        title=title, summary=summary, style=style, motion=motion, duration=duration,
        passes=_comment_lines("pass", passes), visuals=_comment_lines("key visual", visuals),
    )


def write_skeleton(ws: Workspace, plan: Plan | None) -> list[Path]:
    gplan = plan if isinstance(plan, GraphicsPlan) else None
    ws.src.mkdir(parents=True, exist_ok=True)
    common = ws.src / "common.glsl"
    shader = ws.src / "shader.frag"
    common.write_text(COMMON_GLSL)
    shader.write_text(shader_source(gplan))
    return [common, shader]
