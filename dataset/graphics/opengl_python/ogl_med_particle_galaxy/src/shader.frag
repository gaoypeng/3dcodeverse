// src/shader.frag — SpiralGalaxyCore
// CONTRACT (the harness prepends `#version 330 core` + these uniforms + `out vec4 fragColor`; DO NOT redeclare them):
//   uniform float u_time;  uniform vec2 u_resolution;  uniform vec2 u_mouse;  uniform int u_frame;
//   uniform sampler2D u_prev;  (previous frame of this pass)   uniform sampler2D u_noise;  (256x256 RGBA noise, repeat)
//   uniform sampler2D u_buffer_a;  (output of src/buffer_a.frag when that file exists)
//   iTime / iResolution / iFrame / iMouse / iChannel0(=u_prev) / iChannel1(=u_noise) are #defined for Shadertoy ports.
// Entry: void mainImage(out vec4 fragColor, in vec2 fragCoord) — fragCoord in pixels, origin BOTTOM-LEFT.
// Helpers available from src/common.glsl: hash11 hash12 hash22 noise fbm rot2 palette tonemap PI TAU.
// Duration 8s; judged frames at t = 0, 1, 2.5, 4, 6 s.  Everything must MOVE with u_time.
//
// PLAN — A luminous spiral galaxy composed of dense stellar streams orbiting in defined logarithmic arms, featuring an intensely glowing supermassive galactic core and orbiting dust nebulae. The 3D camera smoothly tumbles and revolves around the galaxy plane to reveal its volumetric disc depth.
// Style: Astrophotography deep space: intense core incandescent white-gold (vec3(1.0, 0.95, 0.8)), electric blue/cyan outer spiral stars (vec3(0.25, 0.65, 1.0)), magenta/violet ionized gas dust lanes (vec3(0.85, 0.25, 0.65)), deep void black with faint background stars; additive bloom and filmic glow
//   TODO pass 1: BackgroundStarfield (fullscreen): Distant static and twinkling field stars generated via hash grid layers (3 depth planes, 0.005 size sparkling points) against a deep void space gradient with subtle ambient nebular dust.
//   TODO pass 2: SpiralParticleGalaxy (fullscreen): Raymarched volumetric particle & density accumulation along 64-96 steps through a 3D logarithmic spiral field (2 main arms, 2 secondary spurs). Each step samples radial Keplerian orbital angular velocity (w = 1.0 / sqrt(r^3 + 0.1)), calculating dense instanced point sprites, stellar clusters, and ionized interstellar medium with exponential additive glow (1.0 / (d*d + 0.01)).
//   TODO pass 3: GalacticCoreGlow (fullscreen): Multi-tier radial core bloom with supermassive central glow (r < 0.15, saturated gold/white vec3(1.2, 1.1, 0.9)), intense falloff, dynamic relativistic accretion flare, and subtle dust silhouette absorption lanes.
//   TODO pass 4: PostProcessGrade (postprocess): ACES-style tonemapping to preserve intense additive core highlights without clipping, mild anamorphic lens flare streak across the nucleus, subtle chromatic aberration at screen perimeter, and soft photographic vignette.
//   TODO key visual 1: two dominant logarithmic spiral arms packed with dense bright stars
//   TODO key visual 2: bright glowing central galactic nucleus
//   TODO key visual 3: additive particle glow with blue-white stars and magenta dust lanes
//   TODO key visual 4: smooth 3D rotating camera perspective showcasing disk thickness
//   TODO key visual 5: differential orbital motion across radii
// Motion: Galaxy arms rotate clockwise with differential Keplerian velocity (inner stars rotate faster at ~0.6 rad/s, outer spiral arms drift at ~0.15 rad/s); the 3D camera slowly orbits around the galactic disc inclination (pitch oscillating between 25 deg and 45 deg at 0.1 Hz, yaw drifting continuously at 0.08 rad/s); individual stars shimmer and stream along spiral tracks.

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
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
}
