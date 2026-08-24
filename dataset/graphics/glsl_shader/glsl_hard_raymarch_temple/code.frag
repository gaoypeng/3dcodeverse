// src/shader.frag — AncientTempleGodRays
// Temple sanctum with fluted columns, ceiling oculus, god rays and drifting dust motes.

// Color Palette
const vec3 SUN_COLOR     = vec3(1.00, 0.88, 0.65);
const vec3 SKY_BLUE      = vec3(0.20, 0.45, 0.85);
const vec3 SHADOW_AMB    = vec3(0.07, 0.09, 0.13);
const vec3 STONE_COLOR   = vec3(0.55, 0.48, 0.38);
const vec3 STONE_DARK    = vec3(0.32, 0.28, 0.23);
const vec3 GOLD_GLOW     = vec3(1.00, 0.75, 0.40);

// Sunlight vector (pointing TOWARDS sun in the sky)
const vec3 LIGHT_DIR     = normalize(vec3(0.42, 0.86, 0.28));
const vec3 OCULUS_CENTER = vec3(0.0, 4.0, 0.0);
const float OCULUS_RAD   = 1.85;

// Basic SDF primitives
float sdBox(vec3 p, vec3 b) {
    vec3 q = abs(p) - b;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}

float sdCylinder(vec3 p, float r, float h) {
    vec2 d = abs(vec2(length(p.xz), p.y)) - vec2(r, h);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));
}

// Single classical fluted column with plinth and capital
float sdColumn(vec3 p) {
    // Square base plinth
    float base1 = sdBox(p - vec3(0.0, -1.95, 0.0), vec3(0.65, 0.06, 0.65)) - 0.02;
    float base2 = sdBox(p - vec3(0.0, -1.82, 0.0), vec3(0.56, 0.08, 0.56)) - 0.02;
    float baseTor = length(vec2(length(p.xz) - 0.46, p.y - (-1.70))) - 0.06;
    float base = min(min(base1, base2), baseTor);

    // Fluted shaft
    float angle = atan(p.z, p.x);
    float flute = 0.018 * cos(angle * 16.0); // 16 classical flutes
    // Slight entasis (swelling in the middle)
    float entasis = 0.015 * (1.0 - smoothstep(0.0, 2.8, abs(p.y - 0.8)));
    float shaftRad = 0.42 + flute + entasis;
    float shaft = sdCylinder(p - vec3(0.0, 0.8, 0.0), shaftRad, 2.5);

    // Capital (Echinus & Abacus)
    float capTor = length(vec2(length(p.xz) - 0.48, p.y - 3.32)) - 0.06;
    float cap1   = sdBox(p - vec3(0.0, 3.45, 0.0), vec3(0.58, 0.07, 0.58)) - 0.02;
    float cap2   = sdBox(p - vec3(0.0, 3.58, 0.0), vec3(0.66, 0.06, 0.66)) - 0.02;
    float capital = min(min(capTor, cap1), cap2);

    return min(min(base, shaft), capital);
}

// Central altar / pedestal in the light beam
float sdAltar(vec3 p) {
    float step1 = sdBox(p - vec3(0.0, -1.94, 0.0), vec3(1.6, 0.06, 1.6)) - 0.02;
    float step2 = sdBox(p - vec3(0.0, -1.82, 0.0), vec3(1.3, 0.06, 1.3)) - 0.02;
    float step3 = sdBox(p - vec3(0.0, -1.70, 0.0), vec3(1.0, 0.06, 1.0)) - 0.02;
    float table = sdBox(p - vec3(0.0, -1.40, 0.0), vec3(0.70, 0.25, 0.70)) - 0.03;
    float topLip = sdBox(p - vec3(0.0, -1.12, 0.0), vec3(0.80, 0.04, 0.80)) - 0.02;
    // Ancient relic / bowl on the altar
    float bowl = max(length(p - vec3(0.0, -0.98, 0.0)) - 0.22, -(length(p - vec3(0.0, -0.93, 0.0)) - 0.20));
    bowl = max(bowl, p.y - (-0.98));
    return min(min(min(step1, step2), min(step3, table)), min(topLip, bowl));
}

// Scene Distance Field
// Returns vec2(distance, material_id)
vec2 map(vec3 p) {
    // 1. Floor & Steps
    float floorDist = p.y - (-2.0);

    // 2. Ceiling with Oculus Aperture
    float ceilDist = 3.65 - p.y;
    // Oculus opening at ceiling (circular cut)
    float oculusHole = length(p.xz - OCULUS_CENTER.xz) - OCULUS_RAD;
    // Add cracked stepped trim around oculus
    float oculusRim = abs(length(p.xz - OCULUS_CENTER.xz) - (OCULUS_RAD + 0.15)) - 0.12;
    oculusRim = max(oculusRim, abs(p.y - 3.60) - 0.08);
    ceilDist = max(ceilDist, -oculusHole);
    ceilDist = min(ceilDist, oculusRim);

    // 3. Outer Walls
    float walls = -sdBox(p - vec3(0.0, 1.0, 0.0), vec3(6.5, 3.2, 7.5));

    // 4. Repeated Rows of Colonnades
    // Colonnade left and right (x = -3.2, x = +3.2) spaced along z every 3.2 units
    vec3 pCol = p;
    pCol.x = abs(pCol.x) - 3.2;
    pCol.z = mod(pCol.z + 1.6, 3.2) - 1.6;
    float columns = sdColumn(pCol);

    // Front/back archway columns
    vec3 pCol2 = p;
    pCol2.z = abs(pCol2.z) - 4.8;
    pCol2.x = mod(pCol2.x + 1.6, 3.2) - 1.6;
    if (abs(p.x) < 1.8) {
        // Skip middle doorway
        pCol2.x += 10.0;
    }
    float columns2 = sdColumn(pCol2);
    columns = min(columns, columns2);

    // Architraves above columns
    float archL = sdBox(p - vec3(3.2, 3.75, 0.0), vec3(0.48, 0.12, 6.8));
    float archR = sdBox(p - vec3(-3.2, 3.75, 0.0), vec3(0.48, 0.12, 6.8));
    float architrave = min(archL, archR);

    // 5. Central Altar
    float altar = sdAltar(p);

    // Combine structural components
    float d = min(floorDist, ceilDist);
    d = min(d, walls);
    d = min(d, columns);
    d = min(d, architrave);
    d = min(d, altar);

    float mat = 1.0; // General stone
    if (d == altar) mat = 2.0;

    return vec2(d, mat);
}

// Compute normal via central differences
vec3 calcNormal(vec3 p) {
    vec2 e = vec2(1.5e-3, 0.0);
    return normalize(vec3(
        map(p + e.xyy).x - map(p - e.xyy).x,
        map(p + e.yxy).x - map(p - e.yxy).x,
        map(p + e.yyx).x - map(p - e.yyx).x
    ));
}

// Soft shadow raymarching
float softShadow(vec3 ro, vec3 rd, float mint, float maxt, float k) {
    float res = 1.0;
    float t = mint;
    for (int i = 0; i < 36; i++) {
        float h = map(ro + rd * t).x;
        res = min(res, k * max(h, 0.0) / t);
        t += clamp(h, 0.03, 0.35);
        if (res < 0.005 || t > maxt) break;
    }
    return clamp(res, 0.0, 1.0);
}

// Ambient Occlusion
float calcAO(vec3 pos, vec3 nor) {
    float occ = 0.0;
    float sca = 1.0;
    for (int i = 0; i < 5; i++) {
        float h = 0.05 + 0.12 * float(i);
        float d = map(pos + h * nor).x;
        occ += (h - d) * sca;
        sca *= 0.75;
    }
    return clamp(1.0 - 2.5 * occ, 0.0, 1.0);
}

// Check if a 3D point is inside the primary sunlight shaft entering through the oculus
float sunShaftCone(vec3 p) {
    // Light travels down along -LIGHT_DIR
    // Trace from p along LIGHT_DIR to the ceiling plane (y = 4.0)
    float distToCeil = (4.0 - p.y) / max(LIGHT_DIR.y, 0.01);
    if (distToCeil < 0.0 || p.y > 4.0 || p.y < -2.0) return 0.0;
    vec3 pAtCeil = p + LIGHT_DIR * distToCeil;
    float distFromOculus = length(pAtCeil.xz - OCULUS_CENTER.xz);
    
    // Soft cylindrical/conical beam edge
    float beam = smoothstep(OCULUS_RAD + 0.15, OCULUS_RAD - 0.25, distFromOculus);
    
    // Slight beam expansion downward
    float depth = clamp((4.0 - p.y) / 6.0, 0.0, 1.0);
    beam *= (1.0 - 0.25 * depth);
    return beam;
}

// Volumetric God Rays & Drifting Dust Motes
vec3 renderVolumetrics(vec3 ro, vec3 rd, float maxDist, float tTime) {
    vec3 accum = vec3(0.0);
    int numSteps = 32;
    float stepSize = min(maxDist, 14.0) / float(numSteps);
    
    // Dither step offset to eliminate banding
    float offset = hash12(gl_FragCoord.xy + fract(tTime) * 100.0);
    float t = stepSize * (0.5 + 0.5 * offset);

    // Dust velocity vector: slow upward and lateral drift
    vec3 dustVel = vec3(0.04 * tTime, 0.07 * tTime, -0.03 * tTime);

    for (int i = 0; i < 32; i++) {
        if (t >= maxDist) break;
        vec3 pos = ro + rd * t;

        // Inside temple room bounds?
        if (pos.y >= -2.0 && pos.y <= 4.0 && abs(pos.x) <= 6.2 && abs(pos.z) <= 7.2) {
            float beam = sunShaftCone(pos);
            if (beam > 0.01) {
                // Check shadow obstruction along light ray to oculus
                float shadow = softShadow(pos, LIGHT_DIR, 0.1, 7.0, 12.0);
                
                if (shadow > 0.05) {
                    // Atmospheric haze density with subtle fbm turbulence
                    float haze = 0.55 + 0.45 * fbm3(pos * 0.7 - dustVel * 0.4);
                    
                    // Drifting sparkling dust motes
                    vec3 dustP = pos * 3.5 - dustVel * 1.5;
                    float dustBase = noise3(dustP);
                    float sparkles = pow(max(noise3(dustP * 3.1 + vec3(1.7, 4.2, 9.1)), 0.0), 7.0) * 18.0;
                    float dustMotes = (dustBase * 0.6 + sparkles) * 1.2;

                    float density = beam * shadow * (haze * 0.7 + dustMotes * 0.8);
                    
                    // Mie forward scattering phase function
                    float cosTheta = dot(rd, LIGHT_DIR);
                    float g = 0.72;
                    float phase = (1.0 - g * g) / (4.0 * PI * pow(max(1.0 + g * g - 2.0 * g * cosTheta, 0.01), 1.5));
                    
                    vec3 stepCol = SUN_COLOR * density * phase * 4.2 * stepSize;
                    accum += stepCol;
                }
            }
        }
        t += stepSize;
    }
    return accum;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 p = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;
    float t = u_time;

    // Cinematic orbiting camera perspective
    float camAngle = t * 0.14 + 1.15;
    float camDist = 6.2 + 0.35 * cos(t * 0.25);
    float camHeight = -0.35 + 0.15 * sin(t * 0.3 * TAU);
    vec3 ro = vec3(camDist * sin(camAngle), camHeight, camDist * cos(camAngle));
    vec3 ta = vec3(0.0, -0.4 + 0.1 * sin(t * 0.15), 0.0); // Look at central sanctum

    // Camera ray construction
    vec3 ww = normalize(ta - ro);
    vec3 uu = normalize(cross(ww, vec3(0.0, 1.0, 0.0)));
    vec3 vv = cross(uu, ww);
    vec3 rd = normalize(p.x * uu + p.y * vv + 1.45 * ww);

    // 1. Raymarch Scene SDF
    float tMax = 22.0;
    float tHit = 0.05;
    bool hit = false;
    vec2 res = vec2(0.0);

    for (int i = 0; i < 75; i++) {
        vec3 pos = ro + rd * tHit;
        res = map(pos);
        if (res.x < 0.0018) {
            hit = true;
            break;
        }
        tHit += res.x * 0.95;
        if (tHit > tMax) break;
    }

    vec3 col = vec3(0.0);
    float surfaceDist = hit ? tHit : tMax;

    if (hit) {
        vec3 pos = ro + rd * tHit;
        vec3 nor = calcNormal(pos);
        
        // Procedural stone texture & weathering
        float stoneNoise = fbm(pos.xz * 2.5 + pos.y * 1.2);
        vec3 baseMat = mix(STONE_DARK, STONE_COLOR, 0.4 + 0.6 * stoneNoise);

        // Floor tile grooves
        if (pos.y < -1.85) {
            vec2 tile = abs(fract(pos.xz * 0.65) - 0.5);
            float groove = smoothstep(0.46, 0.49, max(tile.x, tile.y));
            baseMat *= 1.0 - 0.45 * groove;
            // Damp stone reflections in crevices
            baseMat += 0.04 * vec3(0.2, 0.3, 0.25) * (1.0 - groove);
        }

        // Altar gold inlay trim
        if (res.y == 2.0 && pos.y > -1.25) {
            baseMat = mix(baseMat, GOLD_GLOW * 0.8, 0.5 + 0.5 * sin(pos.y * 20.0));
        }

        // Lighting Evaluation
        vec3 lig = LIGHT_DIR;
        float dif = max(dot(nor, lig), 0.0);
        float sha = 0.0;
        if (dif > 0.001) {
            sha = softShadow(pos + nor * 0.015, lig, 0.05, 8.0, 16.0);
        }
        
        // Ambient Occlusion
        float ao = calcAO(pos, nor);
        
        // Skylight from Oculus opening
        vec3 toOculus = normalize(OCULUS_CENTER - pos);
        float skyDif = max(dot(nor, toOculus), 0.0);
        float skySha = softShadow(pos + nor * 0.015, toOculus, 0.05, 6.0, 8.0);
        
        // Deep ambient shadow tones with cool slate-blue bounce
        vec3 ambient = SHADOW_AMB * (0.6 + 0.4 * nor.y) * ao;
        vec3 skyLight = SKY_BLUE * (0.35 * skyDif * skySha * ao);
        
        // Direct golden sun illumination
        vec3 direct = SUN_COLOR * (dif * sha * 2.2);

        // Specular highlight on worn stone
        vec3 hal = normalize(lig - rd);
        float spec = pow(max(dot(nor, hal), 0.0), 18.0) * sha * (0.2 + 0.3 * stoneNoise);

        col = baseMat * (ambient + skyLight + direct) + SUN_COLOR * spec;

        // Warm bounce light on floor under the main sunspot
        float sunSpot = smoothstep(OCULUS_RAD + 0.5, 0.0, length(pos.xz - vec2(0.8, 0.5)));
        if (pos.y < -1.8) {
            col += SUN_COLOR * (sunSpot * sha * 0.6);
        }

        // Distance fog into ambient shadow
        col = mix(col, SHADOW_AMB * 0.8, 1.0 - exp(-0.025 * tHit * tHit));
    } else {
        // Looking up into the sky through the oculus
        float sunDisc = smoothstep(0.992, 0.999, dot(rd, LIGHT_DIR));
        float sunGlow = exp(-6.0 * (1.0 - max(dot(rd, LIGHT_DIR), 0.0)));
        vec3 skyGrad = mix(SKY_BLUE, vec3(0.65, 0.82, 0.98), smoothstep(0.0, 0.8, rd.y));
        col = skyGrad + SUN_COLOR * (sunDisc * 8.0 + sunGlow * 3.0);
    }

    // 2. Volumetric God Rays & Shimmering Dust
    vec3 godRays = renderVolumetrics(ro, rd, surfaceDist, t);
    col += godRays;

    // 3. Post-Processing & Grading
    // Halation / Bloom around bright areas
    vec2 sunScreen = vec2(0.2, 0.2); // Oculus general screen position proxy
    float screenGlow = exp(-2.2 * length(p - sunScreen)) * 0.15;
    col += SUN_COLOR * screenGlow;

    // Filmic ACES Tonemapping
    col = tonemapACES(col * 1.25);

    // Warm ancient film grading (slight lift in shadows, golden warmth in mids)
    col = mix(col, col * vec3(1.05, 0.98, 0.90), 0.35);

    // Lens Vignette
    float vig = 1.0 - 0.32 * dot(p, p);
    col *= max(vig, 0.0);

    // Fine temporal film grain to break quantization and enhance atmosphere
    float grain = (hash12(fragCoord + fract(t * 17.13) * 1000.0) - 0.5) * 0.025;
    col += grain;

    // Gamma correction
    fragColor = vec4(gamma(clamp(col, 0.0, 1.0)), 1.0);
}
