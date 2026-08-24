// src/shader.frag — DeepSpaceNebulaFlythrough
// Cosmic first-person voyage gliding through multi-layered volumetric interstellar clouds
// sculpted with domain warping, surrounded by twinkling multi-depth stars.

// Palette constants
const vec3 VOID_BASE    = vec3(0.012, 0.016, 0.035);
const vec3 NEBULA_MAG   = vec3(0.72, 0.14, 0.58);
const vec3 NEBULA_PURP  = vec3(0.32, 0.08, 0.48);
const vec3 NEBULA_CYAN  = vec3(0.06, 0.75, 0.85);
const vec3 NEBULA_TEAL  = vec3(0.04, 0.45, 0.55);
const vec3 NEBULA_GOLD  = vec3(1.00, 0.82, 0.45);
const vec3 STAR_WARM    = vec3(1.00, 0.95, 0.82);
const vec3 STAR_BLUE    = vec3(0.75, 0.88, 1.00);

// Multi-tier Parallax Star Field
vec3 renderStarField(vec2 p, float t, vec3 ro) {
    vec3 starsCol = vec3(0.0);
    
    // 4 parallax depth tiers (from far background to near foreground)
    for (int layer = 0; layer < 4; layer++) {
        float fl = float(layer);
        
        // Cyclic depth progression along camera Z trajectory
        float layerDepth = fract(0.95 - fl * 0.25 - t * 0.045);
        float z = layerDepth * 8.0 + 0.6;
        
        // Parallax projection: stars expand outward as z decreases
        vec2 uvLayer = p * z * (3.5 + fl * 1.8);
        vec2 cellId = floor(uvLayer);
        vec2 cellUv = fract(uvLayer) - 0.5;
        
        vec2 starPos = hash22(cellId + fl * 19.3) * 0.7 - 0.35;
        vec2 delta = cellUv - starPos;
        float dist = length(delta);
        
        float rnd = hash12(cellId + fl * 31.7);
        
        // Twinkling modulation
        float freq = 1.5 + rnd * 3.5;
        float twinkle = 0.55 + 0.45 * sin(t * freq + rnd * TAU);
        
        // Star size and intensity fade with depth
        float starScale = 1.0 / max(z, 0.1);
        float coreRadius = (0.018 + 0.022 * rnd) * starScale;
        
        // Soft gaussian star core
        float core = exp(-dist * dist / max(coreRadius * coreRadius, 1e-5));
        
        // 4-point cross diffraction spikes on brightest stars (top ~8%)
        float spikes = 0.0;
        if (rnd > 0.92) {
            float armLen = coreRadius * 16.0;
            float spikeX = exp(-abs(delta.x) * 70.0 / starScale) * exp(-abs(delta.y) * 4.0 / starScale);
            float spikeY = exp(-abs(delta.y) * 70.0 / starScale) * exp(-abs(delta.x) * 4.0 / starScale);
            spikes = (spikeX + spikeY) * 0.75 * smoothstep(armLen, 0.0, dist);
        }
        
        // Color variation (warm golden vs cool blue)
        vec3 starTint = mix(STAR_WARM, STAR_BLUE, hash11(rnd * 43.1));
        
        // Depth-based fade (fade in at far plane, fade out as passing camera)
        float depthFade = smoothstep(0.0, 0.18, layerDepth) * smoothstep(1.0, 0.8, layerDepth);
        
        float starIntensity = (core * 2.2 + spikes * 1.8) * twinkle * depthFade;
        starsCol += starTint * starIntensity * (0.8 + 0.8 * rnd);
    }
    
    return starsCol;
}

// Volumetric Nebula Raymarching Pass
vec4 marchNebula(vec3 ro, vec3 rd, float t, vec2 fragCoord) {
    vec3 accumCol = vec3(0.0);
    float transmittance = 1.0;
    
    const int STEPS = 54;
    const float MARCH_LEN = 7.5;
    float stepSize = MARCH_LEN / float(STEPS);
    
    // Interleaved spatial jitter to break volumetric stepping bands
    float jitter = hash12(fragCoord + vec2(t * 13.7, 0.0));
    float marchDist = 0.35 + jitter * stepSize;
    
    for (int i = 0; i < STEPS; i++) {
        if (transmittance < 0.02) break;
        
        vec3 pos = ro + rd * marchDist;
        
        // Compute domain-warped density at current 3D position
        float density = nebulaDensity(pos, t);
        
        if (density > 0.005) {
            // Density gradient along sample offsets for volumetric lighting
            float densityOffset = nebulaDensity(pos + vec3(0.08, 0.06, 0.1), t);
            float rim = max(0.0, density - densityOffset);
            
            // Dual-tone chromatic ionization mapping
            float toneMix = smoothstep(0.08, 0.75, density);
            vec3 gasColor = mix(NEBULA_PURP, NEBULA_MAG, smoothstep(0.0, 0.6, density));
            gasColor = mix(gasColor, NEBULA_CYAN, smoothstep(0.45, 0.85, density));
            
            // Vibrant teal emission edges & golden dust highlights on high density
            gasColor += NEBULA_TEAL * rim * 3.2;
            gasColor += NEBULA_GOLD * pow(density, 3.5) * 2.2;
            
            // Volumetric forward-scattering glow
            float stepDensity = density * 0.42;
            float stepAbsorb = exp(-stepDensity * stepSize * 4.8);
            
            // Light accumulation
            accumCol += gasColor * stepDensity * transmittance * stepSize * 6.5;
            transmittance *= stepAbsorb;
        }
        
        marchDist += stepSize;
    }
    
    return vec4(accumCol, transmittance);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 p = (fragCoord - 0.5 * u_resolution.xy) / u_resolution.y;
    float t = u_time;
    
    // Continuous forward camera progression along Z-axis
    float speed = 0.38;
    float camZ = t * speed;
    
    // Gentle camera sway and banking
    vec3 ro = vec3(0.35 * sin(t * 0.18), 0.22 * cos(t * 0.14), camZ);
    vec3 lookTarget = ro + vec3(0.18 * sin(t * 0.18 + 0.4), 0.12 * cos(t * 0.14 + 0.3), 2.5);
    
    // Camera ray construction with subtle roll
    vec3 fwd = normalize(lookTarget - ro);
    vec3 upRef = vec3(sin(t * 0.08) * 0.15, 1.0, 0.0);
    vec3 right = normalize(cross(fwd, upRef));
    vec3 up = cross(right, fwd);
    
    vec3 rd = normalize(p.x * right + p.y * up + 1.25 * fwd);
    
    // 1. Base deep space background gradient
    vec3 col = VOID_BASE + vec3(0.015, 0.008, 0.025) * smoothstep(-0.5, 0.5, p.y);
    
    // 2. Parallax starfield (background and midground)
    vec3 starField = renderStarField(p, t, ro);
    
    // 3. Volumetric nebula raymarching
    vec4 nebula = marchNebula(ro, rd, t, fragCoord);
    
    // Composite: background stars shine through transparent cosmic gas
    col = col * nebula.w + starField * (nebula.w * 0.85 + 0.15) + nebula.rgb;
    
    // 4. Foreground floating stellar dust motes
    for (int m = 0; m < 2; m++) {
        float fm = float(m);
        float moteZ = fract(0.8 - t * 0.12 - fm * 0.5);
        vec2 moteUv = p * (1.2 + moteZ * 6.0) + vec2(sin(t * 0.3 + fm), cos(t * 0.25 + fm)) * 0.2;
        vec2 moteCell = floor(moteUv * 6.0);
        vec2 moteFrac = fract(moteUv * 6.0) - 0.5;
        vec2 motePos = hash22(moteCell + fm * 47.1) * 0.6 - 0.3;
        float moteDist = length(moteFrac - motePos);
        float moteVal = exp(-moteDist * 32.0) * smoothstep(0.0, 0.3, moteZ) * smoothstep(1.0, 0.7, moteZ);
        col += (NEBULA_CYAN * 0.6 + NEBULA_GOLD * 0.8) * moteVal * 0.65;
    }
    
    // 5. Subtle radial exposure glow from deep nebula core
    float coreGlow = exp(-length(p) * 2.2) * 0.18;
    col += vec3(0.25, 0.08, 0.35) * coreGlow;
    
    // 6. Subtle chromatic aberration at edges
    float distCenter = dot(p, p);
    vec2 caOffset = p * distCenter * 0.008;
    vec3 caCol;
    caCol.r = col.r;
    caCol.g = col.g * (1.0 - 0.05 * distCenter);
    caCol.b = col.b * (1.0 + 0.08 * distCenter);
    col = mix(col, caCol, 0.6);
    
    // 7. Vignette
    float vignette = smoothstep(1.6, 0.45, length(p) * 1.15);
    col *= vignette;
    
    // 8. Dither against color banding
    col += (hash12(fragCoord + t) - 0.5) / 255.0;
    
    // 9. ACES filmic tonemapping & 2.2 gamma correction
    vec3 tonemapped = tonemapACES(col);
    vec3 finalColor = pow(max(tonemapped, vec3(0.0)), vec3(1.0 / 2.2));
    
    fragColor = vec4(clamp(finalColor, 0.0, 1.0), 1.0);
}
