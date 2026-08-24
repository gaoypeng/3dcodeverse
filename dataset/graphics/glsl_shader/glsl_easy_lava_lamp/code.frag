// src/shader.frag — WarmLavaLamp
// Ambient lava lamp shader featuring smoothly undulating, rising and merging molten blobs,
// soft volumetric aura glow, and subtle glass reflections over a deep midnight-plum backdrop.

// Warm luminous color palette
const vec3 BG_TOP       = vec3(0.045, 0.015, 0.075); // Deep obsidian plum
const vec3 BG_BOT       = vec3(0.110, 0.035, 0.155); // Rich midnight violet
const vec3 HEATER_GLOW  = vec3(1.000, 0.280, 0.040); // Radiant amber heat source
const vec3 CORE_HOT     = vec3(1.000, 0.880, 0.550); // Glowing golden-peach center
const vec3 CORE_AMBER   = vec3(1.000, 0.350, 0.060); // Vibrant molten orange
const vec3 EDGE_MAGENTA = vec3(0.920, 0.100, 0.460); // Saturated hot magenta-pink
const vec3 EDGE_CRIMSON = vec3(0.480, 0.020, 0.180); // Deep crimson-plum rim

// Glass contour horizontal half-width at height y
float lampProfile(float y) {
    // Elegant tapered silhouette with slight mid-waist narrowing and rounded ends
    float yNorm = clamp((y + 0.45) / 0.90, 0.0, 1.0);
    float taper = 0.28 + 0.06 * cos(yNorm * PI - 0.2) + 0.03 * sin(yNorm * TAU);
    return taper;
}

// Computes 2D distance field of the dynamic metaballs
float blobField(vec2 p, float t) {
    // Base heating pool that breathes and bulges upward to spawn blobs
    float baseBulge = 0.035 * sin(t * 0.8) + 0.025 * cos(t * 1.3);
    vec2 pBase = p - vec2(0.0, -0.44 + baseBulge * 0.5);
    float d = length(vec2(pBase.x * 0.85, pBase.y * 2.2)) - 0.24;

    // Top cooling reservoir where rising blobs merge and settle
    float topBulge = 0.025 * sin(t * 0.6 + 1.5);
    vec2 pTop = p - vec2(0.0, 0.44 + topBulge * 0.5);
    float dTop = length(vec2(pTop.x * 0.90, pTop.y * 2.0)) - 0.21;
    d = smin(d, dTop, 0.20);

    // 8 distinct animated circulating blobs
    // Each blob follows smooth cyclic convective paths with dynamic stretch along velocity
    for (int i = 0; i < 8; i++) {
        float fi = float(i);
        float speed = 0.42 + 0.11 * sin(fi * 1.87);
        float phaseOffset = fi * 1.2566; // evenly distributed initial phases (2*PI / 5)
        float ph = t * speed + phaseOffset;

        // Continuous vertical convection loop: rises slowly in center, sinks at outer sides
        float yCycle = -cos(ph); // -1 (bottom) to +1 (top)
        float vy = sin(ph) * speed; // positive = rising, negative = sinking

        // Center bias when rising, side drift when sinking
        float xSway = (0.13 + 0.04 * sin(fi * 2.4)) * sin(ph * 1.1 + fi * 1.7);
        float yPos = yCycle * 0.32 - 0.02;
        float xPos = xSway * (0.65 + 0.35 * abs(yCycle));

        // Dynamic fluid elongation: stretch vertically while rising/falling fast, squash at turnaround
        float stretchY = 1.0 + clamp(vy * 1.2, -0.25, 0.45);
        float stretchX = 1.0 / sqrt(max(stretchY, 0.5));

        // Organic micro-perturbation
        float wobble = 0.015 * sin(t * 2.5 + fi * 2.0 + p.y * 6.0);
        float radius = 0.075 + 0.035 * sin(fi * 3.1 + 0.8) + wobble;

        vec2 pBlob = p - vec2(xPos, yPos);
        pBlob.x /= stretchX;
        pBlob.y /= stretchY;

        float dSphere = length(pBlob) - radius;
        // Blend dynamically with neighboring metaballs
        d = smin(d, dSphere, 0.18 + 0.04 * sin(fi + t * 0.5));
    }

    // Organic surface tension domain ripple
    vec2 warp = vec2(
        noise(p * 3.2 + vec2(0.0, t * 0.25)),
        noise(p * 3.2 + vec2(4.1, t * 0.30 + 1.2))
    ) - 0.5;
    d += 0.012 * warp.x;

    return d;
}

// Compute surface normal for 3D volumetric shading
vec2 calcFieldNormal(vec2 p, float t) {
    vec2 e = vec2(0.003, 0.0);
    float d = blobField(p, t);
    return normalize(vec2(
        blobField(p + e.xy, t) - blobField(p - e.xy, t),
        blobField(p + e.yx, t) - blobField(p - e.yx, t) + 1e-5
    ));
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    // Aspect-correct centered coordinates (height = 1.0, |y| <= 0.5)
    vec2 p = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;
    vec2 uv = fragCoord / u_resolution.xy;
    float t = u_time;

    // -------------------------------------------------------------
    // PASS 1: LampBackdrop & Liquid Chamber
    // -------------------------------------------------------------
    // Deep vertical plum gradient
    float chamberV = smoothstep(-0.5, 0.5, p.y);
    vec3 bgLiquid = mix(BG_BOT, BG_TOP, chamberV);

    // Warm radial glow from base heater element
    vec2 heaterOrigin = vec2(0.0, -0.52);
    float heaterDist = length(p - heaterOrigin);
    float heaterRadiance = exp(-3.8 * heaterDist) * 1.35;
    bgLiquid += HEATER_GLOW * heaterRadiance;

    // Gentle ambient liquid luminescence in center
    float centerAmbient = exp(-6.0 * (p.x * p.x)) * (0.20 + 0.10 * cos(p.y * 3.0));
    bgLiquid += mix(EDGE_MAGENTA, HEATER_GLOW, 0.5) * centerAmbient * 0.45;

    // Subtle fluid convection caustics/motes
    float caustics = fbm(p * 5.0 + vec2(0.0, -t * 0.12));
    bgLiquid += vec3(0.06, 0.01, 0.04) * caustics;

    // -------------------------------------------------------------
    // PASS 2: Metaball Field & Subsurface Volumetric Blob Shading
    // -------------------------------------------------------------
    float dField = blobField(p, t);
    vec3 finalCol = bgLiquid;

    // Multi-tier exponential bloom aura around outer blob contours
    float auraFar  = exp(-12.0 * max(dField, 0.0));
    float auraMid  = exp(-28.0 * max(dField, 0.0));
    float auraNear = exp(-70.0 * max(dField, 0.0));

    vec3 auraCol = EDGE_CRIMSON * auraFar * 0.9 +
                   EDGE_MAGENTA * auraMid * 1.3 +
                   CORE_AMBER   * auraNear * 1.6;
    finalCol += auraCol;

    // Inside the molten metaball bodies
    if (dField < 0.05) {
        float interiorDepth = clamp(-dField / 0.18, 0.0, 1.0);
        vec2 n2D = calcFieldNormal(p, t);

        // 3D pseudo-normal on curved liquid surface
        float nz = sqrt(max(1.0 - dot(n2D, n2D) * 0.85, 0.02));
        vec3 normal3D = normalize(vec3(n2D, nz));

        // Key lighting from lower heater and interior core
        vec3 lightDir = normalize(vec3(0.0, -0.7, 0.7));
        vec3 viewDir  = vec3(0.0, 0.0, 1.0);

        float diff = max(dot(normal3D, lightDir), 0.0);
        vec3 halfV = normalize(lightDir + viewDir);
        float spec = pow(max(dot(normal3D, halfV), 0.0), 24.0);

        // Fresnel edge glow (saturated hot magenta / crimson rim)
        float fresnel = pow(1.0 - max(dot(normal3D, viewDir), 0.0), 2.2);

        // Interior temperature gradient: golden hot center -> molten amber -> magenta skin
        vec3 blobInner = mix(EDGE_MAGENTA, CORE_AMBER, smoothstep(0.05, 0.55, interiorDepth));
        blobInner = mix(blobInner, CORE_HOT, smoothstep(0.55, 1.0, interiorDepth) * 0.95);

        // Add incandescent diffuse and golden specular highlight
        vec3 blobLit = blobInner * (0.85 + 0.45 * diff) + CORE_HOT * (spec * 0.65);
        // Blend edge fresnel
        blobLit = mix(blobLit, EDGE_MAGENTA * 1.4, fresnel * 0.65);

        // Anti-aliased boundary composite
        float edgeAlpha = smoothstep(0.02, -0.015, dField);
        finalCol = mix(finalCol, blobLit, edgeAlpha);
    }

    // -------------------------------------------------------------
    // PASS 3: Glass Container, Highlights & Liquid Silhouette
    // -------------------------------------------------------------
    float glassEdge = lampProfile(p.y);
    float distToWall = abs(p.x) - glassEdge;
    float inVessel = smoothstep(0.015, -0.015, distToWall) *
                     smoothstep(-0.50, -0.46, p.y) *
                     smoothstep(0.50, 0.46, p.y);

    // Outside the lamp chamber: dark ambient room vignette
    vec3 roomBg = vec3(0.015, 0.008, 0.025);
    // Soft outer spill from the lamp onto the dark room
    float roomGlow = exp(-3.2 * max(distToWall, 0.0)) * (0.45 + 0.35 * exp(-3.0 * heaterDist));
    roomBg += mix(EDGE_MAGENTA, HEATER_GLOW, 0.5) * roomGlow * 0.60;

    // Glass cylinder reflection streaks
    float glassSpecularLeft  = exp(-180.0 * pow(p.x + glassEdge * 0.72, 2.0));
    float glassSpecularRight = exp(-320.0 * pow(p.x - glassEdge * 0.82, 2.0));
    vec3 glassHighlights = vec3(1.0, 0.92, 0.96) * (glassSpecularLeft * 0.35 + glassSpecularRight * 0.22);
    
    // Glass rim reflection
    float glassRim = smoothstep(0.03, 0.0, abs(distToWall)) * 0.28;
    glassHighlights += vec3(0.9, 0.6, 0.8) * glassRim;

    // Composite lamp interior with room background and glass
    vec3 sceneCol = mix(roomBg, finalCol, inVessel) + glassHighlights * inVessel;

    // -------------------------------------------------------------
    // PASS 4: Post-Processing, Vignette & Dithering
    // -------------------------------------------------------------
    // Subtle chromatic aberration toward edges
    vec2 caOffset = p * 0.0035;
    sceneCol.r += 0.015 * fbm((p + caOffset) * 6.0);
    sceneCol.b -= 0.010 * fbm((p - caOffset) * 6.0);

    // Soft lens vignette
    float vig = 1.0 - 0.42 * dot(p * vec2(1.2, 1.0), p * vec2(1.2, 1.0));
    sceneCol *= clamp(vig, 0.0, 1.0);

    // Dither against 8-bit banding on dark gradients
    float dither = (hash12(fragCoord + vec2(t * 15.0, 0.0)) - 0.5) / 255.0;
    sceneCol += dither;

    // ACES tonemapping and gamma correction
    vec3 mapped = tonemapACES(sceneCol);
    fragColor = vec4(gamma(mapped), 1.0);
}
