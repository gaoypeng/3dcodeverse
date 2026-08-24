// src/shader.frag — Cyberpunk Rain on Window with Neon Bokeh and Lightning

// Procedural lightning flash profile over time (peaks at t ~ 2.4s and t ~ 6.8s)
float getLightningFlash(float t) {
    float cycle = mod(t, 8.0);
    float flash = 0.0;

    // First multi-strike sequence around 2.3s - 2.7s
    float dt1 = cycle - 2.35;
    if (dt1 >= 0.0 && dt1 < 0.6) {
        float strike1 = exp(-dt1 * 28.0) * 1.3;
        float dt1b = dt1 - 0.09;
        float strike2 = (dt1b > 0.0) ? exp(-dt1b * 16.0) * 1.8 : 0.0;
        float dt1c = dt1 - 0.22;
        float strike3 = (dt1c > 0.0) ? exp(-dt1c * 10.0) * 0.9 : 0.0;
        flash += strike1 + strike2 + strike3;
    }

    // Second dramatic thunder strike around 6.7s - 7.2s
    float dt2 = cycle - 6.72;
    if (dt2 >= 0.0 && dt2 < 0.7) {
        float strike1 = exp(-dt2 * 32.0) * 1.6;
        float dt2b = dt2 - 0.07;
        float strike2 = (dt2b > 0.0) ? exp(-dt2b * 18.0) * 2.2 : 0.0;
        float dt2c = dt2 - 0.18;
        float strike3 = (dt2c > 0.0) ? exp(-dt2c * 8.0) * 1.1 : 0.0;
        flash += strike1 + strike2 + strike3;
    }

    // Low background ambient rumble glow
    float rumble = sin(cycle * 3.5) * 0.5 + 0.5;
    rumble = pow(rumble, 8.0) * 0.08;
    return flash + rumble;
}

// Bokeh disc helper with soft chromatic rim and lens diffraction core
vec3 renderBokehDisc(vec2 p, vec2 center, float radius, vec3 color, float intensity) {
    vec2 d = p - center;
    float dist = length(d);
    float r = max(radius, 0.005);
    
    // Soft blurred bokeh disc with bright circular perimeter ring
    float circle = smoothstep(r, r * 0.75, dist);
    float rim = smoothstep(r * 0.65, r * 0.95, dist) * smoothstep(r * 1.05, r * 0.95, dist) * 1.6;
    float core = exp(-dist * dist / (r * r * 0.45)) * 0.7;
    
    float shape = circle + rim + core;
    return color * shape * intensity;
}

// Background cityscape skyline with illuminated windows and neon signs
vec3 renderCityscape(vec2 p, float flash, float t) {
    vec3 col = BASE_OBSIDIAN;
    
    // Night sky vertical gradient
    float skyGrad = clamp((p.y + 0.4) * 0.8, 0.0, 1.0);
    vec3 nightSky = mix(vec3(0.015, 0.02, 0.05), vec3(0.04, 0.08, 0.16), skyGrad);
    
    // Distant city atmospheric haze / neon glow on clouds
    float clouds = fbm(p * 2.5 + vec2(t * 0.02, 0.0));
    vec3 neonHaze = mix(NEON_VIOLET * 0.35, NEON_CYAN * 0.25, sin(p.x * 1.8 + t * 0.2) * 0.5 + 0.5);
    nightSky += neonHaze * clouds * 0.35;
    
    // Lightning ambient sky flare
    nightSky += LIGHTNING_COL * (flash * 1.4 * (1.0 - p.y * 0.4));
    col = nightSky;

    // Distant skyline buildings (3 layers)
    for (int layer = 0; layer < 3; layer++) {
        float fLayer = float(layer);
        float scale = 7.0 + fLayer * 4.0;
        float speed = 0.008 + fLayer * 0.006;
        float px = p.x + t * speed + fLayer * 3.71;
        
        float cell = floor(px * scale);
        float uInCell = fract(px * scale);
        
        // Pseudo-random building height and width
        float h = hash11(cell + fLayer * 41.17) * 0.45 + 0.15 - fLayer * 0.08;
        float wMargin = hash11(cell + 93.31) * 0.15 + 0.05;
        
        // Building box mask
        if (p.y < h - 0.25 && uInCell > wMargin && uInCell < (1.0 - wMargin)) {
            // Silhouette dark body
            float silShade = 0.015 + fLayer * 0.01;
            vec3 buildCol = vec3(silShade, silShade * 1.2, silShade * 1.8);
            
            // Lightning rim lighting on building rooftops
            float edgeDist = min(abs(uInCell - wMargin), abs(uInCell - (1.0 - wMargin)));
            float topDist = abs(p.y - (h - 0.25));
            if (topDist < 0.015 || edgeDist < 0.02) {
                buildCol += LIGHTNING_COL * flash * (0.8 - fLayer * 0.2);
            }
            
            // Lit window grid
            vec2 winGrid = vec2(fract(uInCell * 10.0), fract(p.y * 45.0));
            float winHash = hash12(vec2(cell, floor(p.y * 45.0)));
            if (winHash > 0.65 && winGrid.x > 0.25 && winGrid.x < 0.75 && winGrid.y > 0.3 && winGrid.y < 0.8) {
                vec3 winCol = (winHash > 0.88) ? NEON_CYAN : ((winHash > 0.78) ? NEON_AMBER : vec3(0.9, 0.85, 0.7));
                buildCol += winCol * (0.6 + 0.4 * sin(t * 1.5 + winHash * 20.0));
            }
            
            // Roof warning blinker
            if (topDist < 0.03 && abs(uInCell - 0.5) < 0.08) {
                float blink = step(0.6, sin(t * 4.0 + cell * 5.0));
                buildCol += vec3(1.0, 0.1, 0.1) * blink * 1.5;
            }
            
            col = mix(col, buildCol, 0.95);
        }
    }
    
    return col;
}

// Background layer: Defocused neon bokeh discs drifting horizontally
vec3 renderNeonBokeh(vec2 p, float flash, float t) {
    vec3 bokehAcc = vec3(0.0);
    
    // Multi-layer bokeh clusters (Far, Mid, Near)
    // Layer 1: Small background bokeh field
    for (int i = 0; i < 22; i++) {
        float fi = float(i);
        vec3 h = hash32(vec2(fi, 13.7));
        vec2 center = vec2(
            (h.x - 0.5) * 2.2 - mod(t * 0.012 + fi * 0.08, 2.4) + 1.2,
            h.y * 0.85 - 0.48
        );
        float radius = 0.022 + h.z * 0.028;
        
        vec3 col = (h.x > 0.65) ? NEON_CYAN : ((h.x > 0.35) ? NEON_MAGENTA : NEON_AMBER);
        float pulse = 0.7 + 0.3 * sin(t * 2.0 + fi * 1.7);
        bokehAcc += renderBokehDisc(p, center, radius, col, (0.55 + h.z * 0.45) * pulse);
    }
    
    // Layer 2: Mid-ground vibrant neon signs & street lights bokeh
    for (int j = 0; j < 18; j++) {
        float fj = float(j);
        vec3 h = hash32(vec2(fj, 87.3));
        vec2 center = vec2(
            (h.x - 0.5) * 2.0 - mod(t * 0.02 + fj * 0.13, 2.6) + 1.3,
            h.y * 0.7 - 0.42
        );
        float radius = 0.045 + h.z * 0.045;
        
        // Cyberpunk neon palettes
        vec3 col = NEON_CYAN;
        if (h.y > 0.66) col = NEON_MAGENTA;
        else if (h.y > 0.33) col = NEON_AMBER;
        else col = NEON_VIOLET;
        
        float pulse = 0.8 + 0.35 * sin(t * 3.2 + fj * 2.4);
        bokehAcc += renderBokehDisc(p, center, radius, col, (0.75 + h.y * 0.6) * pulse);
    }
    
    // Layer 3: Foreground huge defocused glowing bokeh orbs
    for (int k = 0; k < 10; k++) {
        float fk = float(k);
        vec3 h = hash32(vec2(fk, 149.1));
        vec2 center = vec2(
            (h.x - 0.5) * 2.4 - mod(t * 0.028 + fk * 0.22, 2.8) + 1.4,
            h.y * 0.75 - 0.45
        );
        float radius = 0.075 + h.z * 0.065;
        
        vec3 col = (h.x > 0.5) ? NEON_CYAN : NEON_MAGENTA;
        if (h.z > 0.7) col = NEON_AMBER;
        
        float pulse = 0.85 + 0.25 * sin(t * 1.8 + fk * 3.1);
        bokehAcc += renderBokehDisc(p, center, radius, col, 0.9 * pulse);
    }

    // Lightning flash brightens and saturates the bokeh glow
    bokehAcc *= (1.0 + flash * 0.8);
    return bokehAcc;
}

// Background combined scene lookup function (for Snell refraction sampling)
vec3 sampleBackground(vec2 p, float flash, float t) {
    vec3 city = renderCityscape(p, flash, t);
    vec3 bokeh = renderNeonBokeh(p, flash, t);
    return city + bokeh;
}

// Rain Drop & Streaking System on Window Glass
// Returns vec4(normal.xy, dropletMask, streakHighlight)
vec4 getRainWindowDetails(vec2 p, float t) {
    vec2 normal = vec2(0.0);
    float dropMask = 0.0;
    float highlight = 0.0;
    
    // 1) STATIC MICRO-DROPLETS (clinging moisture layer)
    vec2 microGridP = p * 32.0;
    vec2 microCell = floor(microGridP);
    vec2 microUv = fract(microGridP) - 0.5;
    vec2 microHash = hash22(microCell);
    
    if (microHash.x > 0.42) {
        vec2 dropOffset = (microHash - 0.5) * 0.55;
        vec2 d = microUv - dropOffset;
        float r = 0.12 + microHash.y * 0.18;
        float dist = length(d);
        if (dist < r) {
            float h = sqrt(max(r * r - dist * dist, 0.0)) / r;
            vec2 n = -d / max(dist, 1e-4) * h;
            normal += n * 0.45;
            dropMask = max(dropMask, smoothstep(r, r * 0.5, dist));
            highlight += pow(max(dot(normalize(vec3(n, 1.0)), normalize(vec3(0.2, 0.6, 0.77))), 0.0), 16.0) * 0.4;
        }
    }
    
    // 2) RUNNING VERTICAL STREAKS WITH JERKY GRAVITY MOTION
    // Multi-scale streak columns
    for (int layer = 0; layer < 2; layer++) {
        float fLayer = float(layer);
        float colWidth = 6.0 + fLayer * 4.0;
        vec2 sp = p * vec2(colWidth, 1.0);
        float colIdx = floor(sp.x);
        float colU = fract(sp.x) - 0.5;
        
        float hCol = hash11(colIdx * 17.13 + fLayer * 53.7);
        if (hCol > 0.25) {
            // Jerky, intermittent slide motion typical of raindrops
            float speed = 0.32 + hCol * 0.22;
            float slideCycle = t * speed + hCol * 12.0;
            // Introduce stepped jerky pauses in slide
            float jerkyTime = floor(slideCycle) + pow(fract(slideCycle), 2.2);
            float yPos = mod(1.0 - jerkyTime * 0.45 + hCol, 1.8) - 0.9;
            
            // Meandering horizontal wiggle of streak path
            float wiggle = sin(p.y * 14.0 + hCol * 20.0) * 0.06 * (1.0 - p.y);
            float xDist = colU - wiggle;
            
            // A) Main sliding teardrop head
            vec2 headD = vec2(xDist * 1.5, p.y - yPos);
            float headDist = length(headD);
            float headRadius = 0.075 + hCol * 0.045;
            
            if (headDist < headRadius) {
                float headH = sqrt(max(headRadius * headRadius - headDist * headDist, 0.0)) / headRadius;
                vec2 headN = -headD / max(headDist, 1e-4) * headH;
                normal += headN * 1.2;
                dropMask = max(dropMask, smoothstep(headRadius, headRadius * 0.4, headDist));
                
                // Specular glint on head
                highlight += pow(max(dot(normalize(vec3(headN, 0.9)), normalize(vec3(-0.3, 0.5, 0.8))), 0.0), 22.0) * 1.4;
            }
            
            // B) Trailing streak path behind the moving head
            if (p.y > yPos && p.y < yPos + 0.75) {
                float trailAge = (p.y - yPos) / 0.75;
                float trailWidth = (0.045 + hCol * 0.03) * (1.0 - trailAge * 0.65);
                float trailDist = abs(xDist);
                
                if (trailDist < trailWidth) {
                    float trailH = cos(trailDist / trailWidth * (PI * 0.5));
                    vec2 trailN = vec2(-sign(xDist) * (1.0 - trailH), (1.0 - trailAge) * 0.2);
                    normal += trailN * 0.75 * (1.0 - trailAge);
                    dropMask = max(dropMask, (1.0 - trailAge) * 0.8);
                    highlight += pow(trailH, 8.0) * (1.0 - trailAge) * 0.5;
                }
                
                // Small residue droplets left in the wake
                float dropletGrid = fract(p.y * 18.0 + hCol * 7.0);
                if (dropletGrid < 0.35 && abs(xDist) < trailWidth * 1.8) {
                    vec2 dDrop = vec2(xDist, dropletGrid - 0.17);
                    float dLen = length(dDrop);
                    if (dLen < 0.045) {
                        float dH = sqrt(max(0.002 - dLen * dLen, 0.0)) / 0.045;
                        normal += -dDrop / max(dLen, 1e-4) * dH * 0.8;
                        dropMask = max(dropMask, 0.75);
                    }
                }
            }
        }
    }
    
    return vec4(normal, dropMask, highlight);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = fragCoord / u_resolution.xy;
    vec2 p = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;
    float t = u_time;
    
    // Dynamic lightning trigger
    float lightningFlash = getLightningFlash(t);
    
    // Raindrop normals & refraction map on glass
    vec4 rain = getRainWindowDetails(p, t);
    vec2 refractOffset = rain.xy * 0.07;
    
    // Chromatic dispersion during Snell glass refraction
    vec2 pRed   = p + refractOffset * 1.08;
    vec2 pGreen = p + refractOffset * 1.00;
    vec2 pBlue  = p + refractOffset * 0.92;
    
    // Sample background with optical chromatic aberration
    float rChannel = sampleBackground(pRed, lightningFlash, t).r;
    float gChannel = sampleBackground(pGreen, lightningFlash, t).g;
    float bChannel = sampleBackground(pBlue, lightningFlash, t).b;
    
    vec3 sceneCol = vec3(rChannel, gChannel, bChannel);
    
    // Droplet internal reflections & edge fresnel
    float fresnel = pow(1.0 - sqrt(max(1.0 - dot(rain.xy, rain.xy), 0.0)), 2.5);
    vec3 waterGlint = mix(NEON_CYAN, LIGHTNING_COL, 0.5 + 0.5 * lightningFlash) * (rain.w + fresnel * 0.6);
    waterGlint += LIGHTNING_COL * (rain.z * lightningFlash * 1.8);
    
    sceneCol += waterGlint;
    
    // Atmospheric condensation mist on un-wiped glass areas
    float mist = fbm(p * 5.0 + vec2(t * 0.01, 0.0)) * 0.12;
    vec3 mistGlow = mix(NEON_MAGENTA * 0.3, NEON_CYAN * 0.3, sin(p.x * 2.0 + t * 0.4) * 0.5 + 0.5);
    sceneCol = mix(sceneCol, sceneCol + mistGlow, mist * (1.0 - rain.z));

    // Post Process Grading:
    // 1) Chromatic aberration toward screen corners
    vec2 distFromCenter = uv - 0.5;
    float caStrength = dot(distFromCenter, distFromCenter) * 0.008;
    sceneCol.r += sampleBackground(p + vec2(caStrength, 0.0), lightningFlash, t).r * 0.08;
    sceneCol.b += sampleBackground(p - vec2(caStrength, 0.0), lightningFlash, t).b * 0.08;

    // 2) Cinematic lens vignette
    float vignette = 1.0 - dot(distFromCenter * 1.25, distFromCenter * 1.25);
    vignette = clamp(pow(max(vignette, 0.0), 1.6), 0.0, 1.0);
    sceneCol *= vignette;

    // 3) Film grain
    float grain = (hash12(fragCoord + fract(t) * 100.0) - 0.5) * 0.035;
    sceneCol += grain;

    // ACES Tone mapping & Gamma curve
    vec3 graded = gamma(tonemapACES(sceneCol));
    fragColor = vec4(clamp(graded, 0.0, 1.0), 1.0);
}
