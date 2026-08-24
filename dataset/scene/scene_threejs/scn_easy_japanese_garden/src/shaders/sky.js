// src/shaders/sky.js — gradient sky dome (raw GLSL in a ShaderMaterial).
import * as THREE from 'three';

const vertexShader = /* glsl */ `
varying vec3 vWorldDir;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldDir = normalize(wp.xyz - cameraPosition);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const fragmentShader = /* glsl */ `
uniform vec3 uZenith;
uniform vec3 uHorizon;
uniform vec3 uSunDir;
uniform float uTime;
varying vec3 vWorldDir;

void main() {
  float h = clamp(vWorldDir.y, -0.15, 1.0);
  vec3 col = mix(uHorizon, uZenith, pow(max(h + 0.1, 0.0), 0.65));
  
  // Sun disc and golden atmospheric scattering
  vec3 V = normalize(vWorldDir);
  vec3 S = normalize(uSunDir);
  float sunDot = max(dot(V, S), 0.0);
  
  float sunDisc = pow(sunDot, 350.0) * 2.0;
  float sunHalo = pow(sunDot, 16.0) * 0.45;
  float warmHaze = pow(sunDot, 3.0) * 0.25 * (1.0 - max(h, 0.0));
  
  vec3 sunColor = vec3(1.0, 0.94, 0.83);
  vec3 amberGlow = vec3(0.96, 0.75, 0.45);
  
  col += sunColor * sunDisc + amberGlow * (sunHalo + warmHaze);
  
  // Subtle atmospheric shimmer
  col += 0.02 * sin(vWorldDir.x * 4.0 + uTime * 0.1) * (1.0 - max(h, 0.0));
  
  gl_FragColor = vec4(col, 1.0);
}
`;

export function makeSkyMaterial(T = THREE) {
  return new T.ShaderMaterial({
    name: 'SkyMaterial',
    uniforms: {
      uZenith: { value: new T.Color(0xa2c4d9) }, // soft pale cyan
      uHorizon: { value: new T.Color(0xf4d090) }, // warm amber
      uSunDir: { value: new T.Vector3(-0.45, 0.4, -0.65) },
      uTime: { value: 0 },
    },
    vertexShader,
    fragmentShader,
    side: T.BackSide,
    depthWrite: false,
    fog: false,
  });
}
