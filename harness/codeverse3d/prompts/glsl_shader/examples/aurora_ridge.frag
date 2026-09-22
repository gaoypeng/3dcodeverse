// Aurora over a ridge with a frozen lake — organic curtains, dense stars, real darks.
float hash12(vec2 p)  { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
vec2  hash22(vec2 p)  { vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973)); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.xx + p3.yz) * p3.zy); }
float noise(vec2 p) { vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1, 0)), u.x), mix(hash12(i + vec2(0, 1)), hash12(i + vec2(1, 1)), u.x), u.y); }
float fbm(vec2 p) { float v = 0.0, a = 0.5; mat2 m = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 6; i++) { v += a * noise(p); p = m * p * 2.03 + 0.31; a *= 0.5; } return v; }

// ---- ORGANIC CURTAIN: a ribbon whose lower edge wanders and folds, sharp below, fading above, with fine vertical rays
// returns intensity 0..1 and writes k = 0 at the lower edge .. 1 at the top (for the green->violet gradient)
float curtain(vec2 p, float t, float seed, out float k) {
    float xw = p.x + 0.55 * (fbm(vec2(p.x * 0.7 + seed, t * 0.05 + seed * 2.0)) - 0.5);   // horizontal folding (domain warp)
    float base = -0.08 + 0.11 * sin(xw * 2.3 + seed * 1.7 + t * 0.07) + 0.16 * fbm(vec2(xw * 2.0 + seed * 3.1, 0.7 + t * 0.06));   // a long arc + sinuous lower edge
    float h = 0.22 + 0.25 * fbm(vec2(xw * 0.6 + seed * 5.0, 2.0 + t * 0.03));               // how tall the curtain is here
    float d = p.y - base;                                                                     // height above the lower edge
    k = clamp(d / h, 0.0, 1.0);
    float rays = 0.45 + 0.55 * noise(vec2(xw * 55.0 + t * 0.9, seed));                       // fine vertical striations
    rays *= 0.55 + 0.45 * noise(vec2(xw * 12.0 - t * 0.25, seed + 3.0));                     // grouped into bundles
    float lower = smoothstep(-0.02 - 0.05 * rays, 0.012, d);                                  // bright lower edge, ragged by the rays
    float upper = exp(-max(d, 0.0) / h * 1.7);                                                // fades out upward
    float gaps = smoothstep(0.36, 0.66, fbm(vec2(xw * 1.6 + seed * 7.0, t * 0.04)));         // the ribbon thins and breaks
    return lower * upper * rays * gaps;
}
vec3 auroraCol(float k) { return mix(vec3(0.10, 0.95, 0.42), vec3(0.70, 0.20, 0.85), smoothstep(0.18, 0.9, k)); }
vec3 aurora(vec2 p, float t) {
    vec3 acc = vec3(0.0);
    float k = 0.0;
    float a0 = curtain(p, t, 0.0, k);            acc += auroraCol(k) * a0 * 1.00;   // nearest, brightest
    float a1 = curtain(p - vec2(0.3, 0.12), t * 0.8, 11.0, k); acc += auroraCol(k) * a1 * 0.55;
    float a2 = curtain(p - vec2(-0.5, 0.22), t * 0.6, 23.0, k); acc += auroraCol(k) * a2 * 0.30;
    return acc;
}
// ---- DENSE STARS: two layers, thousands of points, two brightness classes, no twinkle blobs
float stars(vec2 p, float density, float keep) {
    vec2 g = floor(p * density), f = fract(p * density);
    float h = hash12(g); vec2 o = hash22(g) * 0.7 + 0.15;
    float d = length(f - o);                                 // distance in cell units (0..0.7)
    float bright = step(1.0 - keep, h);                      // keep-fraction of cells hold a star
    float size = 0.07 + 0.10 * step(0.97, h);                // a few are bigger/brighter
    return bright * exp(-d * d / (size * size)) * (0.35 + 0.65 * hash12(g + 9.0));
}
float ridge(float x) { return -0.02 + 0.16 * fbm(vec2(x * 1.7 + 3.0, 1.0)) + 0.06 * fbm(vec2(x * 6.0, 4.0)); }

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 p = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;   // |y| <= 0.5, y up
    float t = u_time;
    float horizon = -0.18;                                            // lake surface
    vec3 col = mix(vec3(0.02, 0.035, 0.08), vec3(0.0, 0.0, 0.012), smoothstep(-0.15, 0.45, p.y));   // near-black sky, faint blue at the horizon
    // stars (only above the ridge; dense, small)
    col += vec3(0.9, 0.95, 1.0) * (stars(p, 160.0, 0.22) * 0.55 + stars(p + 3.7, 70.0, 0.25) * 0.9);
    // aurora — the brightest thing in the frame, drifting sideways
    vec3 au = aurora(vec2(p.x + t * 0.015, p.y - horizon - 0.16), t);
    col += au * 1.7;
    col += au * au * 0.5;                                             // a little bloom on the bright lower edges
    // mountain ridge silhouette (flat near-black, thin snow line on the sunlit crest)
    float r = horizon + 0.06 + ridge(p.x);
    float ridgeMask = smoothstep(r + 0.002, r - 0.002, p.y);
    vec3 ridgeCol = vec3(0.012, 0.014, 0.02) + au * 0.03;             // a hint of aurora light on the rock
    ridgeCol += vec3(0.5, 0.6, 0.7) * smoothstep(0.006, 0.0, abs(p.y - r)) * 0.25;   // snow line
    col = mix(col, ridgeCol, ridgeMask);
    // frozen lake: blurred vertical mirror of the sky + ridge, dimmer, with faint shimmer lines
    if (p.y < horizon) {
        float dy = horizon - p.y;
        float shimmer = 0.006 * sin(p.y * 220.0 + t * 1.5) * noise(vec2(p.x * 8.0, t * 0.3));
        vec2 rp = vec2(p.x + shimmer, horizon + dy * 1.05);           // mirrored point
        vec3 refl = vec3(0.0);
        for (int i = 0; i < 4; i++) {                                  // cheap vertical blur
            vec2 q = rp + vec2(0.0, float(i) * 0.006);
            vec3 c = mix(vec3(0.02, 0.035, 0.08), vec3(0.0), smoothstep(-0.15, 0.45, q.y));
            vec3 a = aurora(vec2(q.x + t * 0.015, q.y - horizon - 0.16), t);
            float rr = horizon + 0.06 + ridge(q.x);
            c += a * 1.15; c = mix(c, vec3(0.012, 0.014, 0.02), smoothstep(rr + 0.002, rr - 0.002, q.y));
            refl += c;
        }
        refl *= 0.25;
        col = refl * 0.55 * (1.0 - smoothstep(0.0, 0.32, dy) * 0.5) + vec3(0.005, 0.008, 0.015);
        col += vec3(0.02, 0.05, 0.05) * smoothstep(0.02, 0.0, dy);     // faint bright shore line
    }
    // grade: keep the darks, tonemap the aurora, vignette
    col = col / (1.0 + col * 0.6);
    col *= 1.0 - 0.35 * dot(p * vec2(0.9, 1.4), p * vec2(0.9, 1.4));
    fragColor = vec4(pow(max(col, 0.0), vec3(0.95)), 1.0);
}
