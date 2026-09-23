uniform float uTime;
uniform float uRoughness;
uniform float uFilmStrength;
uniform vec3 uBaseReflectance;
in vec3 alloyObjectPosition;
in vec3 alloyViewPosition;
in vec3 alloyViewNormal;
out vec4 alloyOutput;

void main() {
    vec3 p = alloyObjectPosition;
    vec3 n = normalize(alloyViewNormal);
    vec3 v = normalize(-alloyViewPosition);
    float forging = alloyNoise(p * 19.0) + 0.4 * alloyNoise(p * 47.0);
    float brushPhase = p.y * 380.0 + sin(p.x * 4.0) * 5.0;
    float brushFilter = exp(-0.8 * pow(fwidth(brushPhase), 2.0));
    float height = forging * 0.0005 + sin(brushPhase) * brushFilter * 0.000035;

    // Surface-gradient normal relief, with a finite grazing-angle fallback.
    vec3 dx = dFdx(alloyViewPosition), dy = dFdy(alloyViewPosition);
    vec3 r1 = cross(dy, n), r2 = cross(n, dx);
    float determinant = dot(dx, r1);
    if (abs(determinant) > 1e-10) {
        vec3 gradient = (dFdx(height) * r1 + dFdy(height) * r2) / determinant;
        n = normalize(n - gradient);
    }
    float roughness = clamp(uRoughness + (forging - 0.7) * 0.06, 0.16, 0.7);
    float phase = p.y * 1.45 + p.x * 0.22 + 0.30 * sin(uTime * 0.55)
                + pow(clamp(1.0 - dot(n, v), 0.0, 1.0), 1.5) * 1.8;
    vec3 film = 0.47 + 0.30 * cos(phase * 3.2 + vec3(0.0, 2.0, 4.0));
    vec3 f0 = mix(uBaseReflectance, film, uFilmStrength);
    vec3 worldN = normalize(transpose(mat3(viewMatrix)) * n);
    vec3 worldV = normalize(transpose(mat3(viewMatrix)) * v);
    vec3 reflected = reflect(-worldV, worldN);
    vec3 colour = alloyStudio(reflected, roughness) * alloyFresnel(f0, max(dot(n, v), 0.0));
    colour += alloyGgx(n, v, normalize(mat3(viewMatrix) * vec3(-3, 4, 3)), f0,
                      roughness, vec3(1.6, 1.3, 0.95));
    colour += alloyGgx(n, v, normalize(mat3(viewMatrix) * vec3(4, 2, -3)), f0,
                      roughness, vec3(0.45, 0.7, 1.3));
    alloyOutput = vec4(colour, 1.0);
    // These standard output chunks use the host's fixed tone/exposure policy.
    // GLSL3 names the fragment output explicitly; no gl_FragColor alias is used.
    #ifdef TONE_MAPPING
        alloyOutput.rgb = toneMapping(alloyOutput.rgb);
    #endif
    alloyOutput = linearToOutputTexel(alloyOutput);
}
