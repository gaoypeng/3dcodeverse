// src/shader.frag — StylisedOceanSunset

// Palette constants per style definition: rich, vivid, deep twilight tones
const vec3 SKY_ZENITH    = vec3(0.07, 0.04, 0.22); // Deep indigo / violet
const vec3 SKY_MID       = vec3(0.82, 0.20, 0.44); // Fiery magenta
const vec3 SKY_HORIZON   = vec3(1.00, 0.58, 0.18); // Golden amber
const vec3 SUN_COLOR     = vec3(1.00, 0.94, 0.78); // Radiant warm gold
const vec3 OCEAN_DEEP    = vec3(0.015, 0.045, 0.11); // Deep navy / midnight teal
const vec3 OCEAN_SHALLOW = vec3(0.03, 0.12, 0.18);  // Rich teal
const vec3 CREST_COLOR   = vec3(1.00, 0.82, 0.48); // Gilded crest highlights
const vec3 FOAM_COLOR    = vec3(0.95, 0.88, 0.78); // Sunlit foam

// Multi-stop vertical sky gradient: deep indigo zenith -> magenta -> amber
vec3 getSkyColor(vec2 p, vec2 sunPos) {
    float y = p.y;
    vec3 sky;
    if (y < 0.08) {
        float k = smoothstep(0.0, 0.08, max(y, 0.0));
        sky = mix(SKY_HORIZON, vec3(0.95, 0.35, 0.25), k);
    } else if (y < 0.22) {
        float k = smoothstep(0.08, 0.22, y);
        sky = mix(vec3(0.95, 0.35, 0.25), SKY_MID, k);
    } else {
        float k = smoothstep(0.22, 0.42, y);
        sky = mix(SKY_MID, SKY_ZENITH, k);
    }

    // Atmospheric sun glow contained near the sun to preserve deep indigo aloft
    float distToSun = length(p - sunPos);
    vec3 glow = SUN_COLOR * (0.35 * exp(-6.0 * distToSun) + 0.15 * exp(-18.0 * distToSun));
    
    // Subtle sunset horizon band
    float horizonGlow = exp(-35.0 * max(abs(p.y - sunPos.y * 0.5), 0.0)) * 0.18;
    sky += SKY_HORIZON * horizonGlow;

    return sky + glow;
}

// 2-layer backlit cloud synthesis
vec4 renderClouds(vec2 p, float t, vec2 sunPos) {
    vec3 col = vec3(0.0);
    float totalAlpha = 0.0;

    // Layer 1: High, slower, wispy
    {
        vec2 cp = p * vec2(1.2, 2.5) + vec2(t * 0.015, 0.2);
        float d = fbm(cp + 0.3 * fbm(cp * 2.0 + t * 0.01));
        float cloudMask = smoothstep(0.53, 0.74, d);
        if (cloudMask > 0.001) {
            vec2 toSun = normalize(sunPos - p + vec2(1e-4));
            float lightSample = fbm(cp - toSun * 0.08);
            float rim = clamp((d - lightSample) * 2.5 + 0.35, 0.0, 1.0);

            vec3 cloudBase = mix(vec3(0.18, 0.08, 0.22), SKY_MID * 0.6, 0.5);
            vec3 cloudLit = mix(SKY_HORIZON, SUN_COLOR, rim);
            vec3 layerCol = mix(cloudBase, cloudLit, rim);

            col = mix(col, layerCol, cloudMask * 0.75);
            totalAlpha += cloudMask * 0.75;
        }
    }

    // Layer 2: Mid-altitude, faster, billowy
    {
        vec2 cp = p * vec2(1.8, 3.2) + vec2(t * 0.024 + 5.3, 1.1);
        float q = fbm(cp * 1.5 - vec2(t * 0.01, 0.0));
        float d = fbm(cp + 0.4 * q);
        float cloudMask = smoothstep(0.56, 0.78, d);
        if (cloudMask > 0.001) {
            vec2 toSun = normalize(sunPos - p + vec2(1e-4));
            float lightSample = fbm(cp - toSun * 0.06);
            float rim = clamp((d - lightSample) * 3.0 + 0.25, 0.0, 1.0);

            vec3 cloudBase = mix(vec3(0.12, 0.06, 0.18), vec3(0.35, 0.12, 0.26), 0.6);
            vec3 cloudLit = mix(SKY_HORIZON * 1.05, SUN_COLOR * 1.1, rim);
            vec3 layerCol = mix(cloudBase, cloudLit, rim);

            float a = cloudMask * (1.0 - totalAlpha);
            col += layerCol * a;
            totalAlpha += a;
        }
    }

    return vec4(col, clamp(totalAlpha, 0.0, 1.0));
}

// Trochoidal wave surface height & normal derivation
struct OceanSample {
    float height;
    vec3 normal;
    float crest;
    float microChop;
};

OceanSample evaluateOcean(vec2 worldPos, float t) {
    OceanSample res;
    float h = 0.0;
    vec2 dH = vec2(0.0);
    float crestAccum = 0.0;

    // 5 harmonic octaves of rolling waves
    // Main rolling swell towards viewer
    float w1 = sin(worldPos.y * 1.6 - t * 1.8 + worldPos.x * 0.2);
    float w1_sharp = pow(max(0.5 + 0.5 * w1, 0.0), 2.2);
    h += w1_sharp * 0.38;
    dH.y += cos(worldPos.y * 1.6 - t * 1.8 + worldPos.x * 0.2) * 1.6 * w1_sharp * 0.38;
    crestAccum += pow(max(0.5 + 0.5 * w1, 0.0), 4.0) * 0.5;

    // Wave octave 2: slightly angled swell
    float w2 = sin(worldPos.y * 3.2 - t * 2.4 + worldPos.x * 0.9);
    float w2_sharp = pow(max(0.5 + 0.5 * w2, 0.0), 2.0);
    h += w2_sharp * 0.18;
    dH.y += cos(worldPos.y * 3.2 - t * 2.4 + worldPos.x * 0.9) * 3.2 * w2_sharp * 0.18;
    dH.x += cos(worldPos.y * 3.2 - t * 2.4 + worldPos.x * 0.9) * 0.9 * w2_sharp * 0.18;
    crestAccum += pow(max(0.5 + 0.5 * w2, 0.0), 3.5) * 0.3;

    // Wave octave 3: counter swell
    float w3 = sin(worldPos.y * 6.5 - t * 3.6 - worldPos.x * 1.4);
    float w3_sharp = pow(max(0.5 + 0.5 * w3, 0.0), 1.8);
    h += w3_sharp * 0.08;
    dH.y += cos(worldPos.y * 6.5 - t * 3.6 - worldPos.x * 1.4) * 6.5 * w3_sharp * 0.08;
    dH.x -= cos(worldPos.y * 6.5 - t * 3.6 - worldPos.x * 1.4) * 1.4 * w3_sharp * 0.08;

    // Wave octave 4: medium chop
    float w4 = sin(worldPos.y * 13.0 - t * 5.2 + worldPos.x * 3.5);
    h += w4 * 0.035;
    dH += vec2(3.5, 13.0) * cos(worldPos.y * 13.0 - t * 5.2 + worldPos.x * 3.5) * 0.035;

    // Wave octave 5: high frequency capillary ripples
    float w5 = sin(worldPos.y * 24.0 - t * 7.5 - worldPos.x * 6.0);
    h += w5 * 0.012;
    dH += vec2(-6.0, 24.0) * cos(worldPos.y * 24.0 - t * 7.5 - worldPos.x * 6.0) * 0.012;

    // Micro-chop modulation using smooth 2D noise
    float micro = noise(worldPos * 6.0 + vec2(0.0, -t * 2.2));
    float microD = noise(worldPos * 12.0 + vec2(t * 1.2, -t * 3.2));
    dH += vec2(micro - 0.5, microD - 0.5) * 0.45;

    res.height = h;
    res.normal = normalize(vec3(-dH.x, 1.0, -dH.y));
    res.crest = clamp(crestAccum, 0.0, 1.0);
    res.microChop = micro;
    return res;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = fragCoord / u_resolution.xy;
    vec2 p = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;
    float t = u_time;

    // Sun position: glowing disc just above horizon with gentle atmospheric wobble
    vec2 sunPos = vec2(0.0, 0.045 + 0.003 * sin(t * 1.5));

    vec3 finalColor = vec3(0.0);

    // Horizon line definition in screen space
    float horizonY = 0.0;

    // Camera setup for ocean raycasting
    vec3 ro = vec3(0.0, 1.2, 0.0);
    vec3 rd = normalize(vec3(p.x, p.y - 0.02, 1.0));

    if (rd.y < 0.0) {
        // --- OCEAN PASS ---
        // Project onto ocean plane with perspective depth
        float depth = -ro.y / rd.y;
        vec3 hitPos = ro + rd * depth;
        vec2 worldPos = hitPos.xz * 0.6;

        // Trochoidal waves
        OceanSample ocean = evaluateOcean(worldPos, t);

        // Perturb ray hit point by wave height for crisp silhouette & crest parallax
        float correctedDepth = -(ro.y - ocean.height * 0.2) / rd.y;
        worldPos = (ro + rd * correctedDepth).xz * 0.6;
        ocean = evaluateOcean(worldPos, t);

        vec3 N = ocean.normal;
        vec3 V = -rd;

        // Deep navy/teal water base color with rich contrast
        vec3 waterBase = mix(OCEAN_DEEP, OCEAN_SHALLOW, ocean.crest * 0.35);

        // Fresnel term (Schlick's approximation)
        float NdotV = clamp(dot(N, V), 0.0, 1.0);
        float fresnel = 0.02 + 0.55 * pow(1.0 - NdotV, 3.5);

        // Sky reflection sampled along reflected ray
        vec3 R = reflect(rd, N);
        vec2 skySampleP = vec2(R.x * 0.5, max(R.y, 0.0) * 0.8 + 0.02);
        vec3 reflectedSky = getSkyColor(skySampleP, sunPos);

        // Blend deep ocean base with sky reflection
        vec3 oceanColor = mix(waterBase, reflectedSky * 0.75, fresnel);

        // --- SUN REFLECTION COLUMN & ANTI-ALIASED SPECULAR ---
        vec3 sunDir = normalize(vec3(sunPos.x, sunPos.y + 0.02, 1.0));
        vec3 H = normalize(V + sunDir);
        float NdotH = clamp(dot(N, H), 0.0, 1.0);

        // Smooth anisotropic reflection column mask
        float colWidth = 0.24 * (1.0 + hitPos.z * 0.06);
        float xDist = abs(p.x - sunPos.x);
        float sunColMask = smoothstep(colWidth * 1.6, 0.0, xDist);
        sunColMask = pow(sunColMask, 1.4);

        // Distance roughness dampening to prevent sub-pixel shimmering/aliasing
        float distRoughness = 1.0 / (1.0 + depth * 0.05);

        // Anti-aliased specular highlights using smoothstep to eliminate jagged pixel steps
        float specTight = smoothstep(0.72, 0.98, NdotH);
        specTight = pow(specTight, 3.0) * 3.5 * distRoughness;

        float specBroad = smoothstep(0.40, 0.90, NdotH);
        specBroad = pow(specBroad, 2.0) * 1.0;

        // Smooth wave micro-facet shimmer (anti-aliased)
        vec3 Nshimmer = normalize(N + vec3(sin(worldPos.x * 12.0 + t * 3.0) * 0.08, 0.0, cos(worldPos.y * 10.0 + t * 3.5) * 0.08));
        float NdotH_shimmer = clamp(dot(Nshimmer, H), 0.0, 1.0);
        float specGlitter = smoothstep(0.75, 0.98, NdotH_shimmer);
        specGlitter = pow(specGlitter, 4.0) * 2.2 * distRoughness;

        vec3 sunReflection = (SUN_COLOR * 2.8 * specTight + SKY_HORIZON * 1.5 * specBroad + SUN_COLOR * 2.0 * specGlitter) * sunColMask;

        // Smoothly anti-aliased wave crest gilding & foam lines
        float smoothCrest = smoothstep(0.25, 0.82, ocean.crest);
        float crestHighlight = smoothCrest * (0.6 + 0.9 * sunColMask);
        vec3 gilded = CREST_COLOR * crestHighlight * 1.2 * smoothstep(0.2, 0.85, ocean.normal.y);

        // Subsurface scattering on backlit crests
        float sss = pow(clamp(dot(V, -sunDir + N * 0.4), 0.0, 1.0), 2.5) * smoothCrest;
        vec3 sssColor = SKY_HORIZON * sss * 1.2;

        oceanColor += sunReflection + gilded + sssColor;

        // Horizon atmospheric haze blend
        float horizonFade = smoothstep(0.0, -0.05, p.y);
        vec3 horizonHaze = SKY_HORIZON * 0.85 + SKY_MID * 0.25;
        float hazeAmount = clamp(exp(-0.06 * depth), 0.0, 1.0);
        oceanColor = mix(horizonHaze, oceanColor, hazeAmount * (1.0 - horizonFade * 0.3));

        finalColor = oceanColor;
    } else {
        // --- SKY PASS ---
        vec3 sky = getSkyColor(p, sunPos);

        // Sun disc (crisp radius 0.068 at sunPos)
        float dSun = length(p - sunPos);
        float sunDisc = smoothstep(0.068, 0.060, dSun);
        
        // Multi-ring glowing corona
        float sunCore = sunDisc * 2.2;
        float sunCorona = 0.5 * exp(-16.0 * max(dSun - 0.05, 0.0)) + 0.25 * exp(-4.0 * max(dSun - 0.05, 0.0));
        float sunRays = 0.02 * sin(atan(p.y - sunPos.y, p.x - sunPos.x) * 12.0 + t * 0.5) * exp(-10.0 * dSun);

        vec3 sunFinal = SUN_COLOR * (sunCore + sunCorona + sunRays);
        sky += sunFinal;

        // Clouds
        vec4 clouds = renderClouds(p, t, sunPos);
        sky = mix(sky, clouds.rgb, clouds.a);

        // Horizon haze blend
        float hazeNearHorizon = exp(-45.0 * max(p.y - horizonY, 0.0));
        sky = mix(sky, SKY_HORIZON * 1.05, hazeNearHorizon * 0.35);

        finalColor = sky;
    }

    // --- ATMOSPHERE & POSTPROCESS GRADE ---
    // Horizontal sun flare bloom
    float hFlare = exp(-4.0 * abs(p.y - sunPos.y)) * exp(-1.5 * abs(p.x - sunPos.x));
    finalColor += SUN_COLOR * hFlare * 0.08;

    // Edge Chromatic Aberration simulation
    float rDist = length(p);
    vec3 caOffset = vec3(1.0 + 0.003 * rDist, 1.0, 1.0 - 0.003 * rDist);
    finalColor.r *= caOffset.r;
    finalColor.b *= caOffset.b;

    // Radial vignette (1.0 at centre to ~0.74 at corners)
    float vignette = 1.0 - 0.35 * dot(p, p);
    finalColor *= max(vignette, 0.0);

    // Subtle dithering to eliminate banding
    finalColor += (hash12(fragCoord + vec2(t * 10.0)) - 0.5) / 255.0;

    // Filmic ACES Tone Mapping + Gamma
    vec3 graded = gamma(tonemapACES(finalColor));

    fragColor = vec4(graded, 1.0);
}
