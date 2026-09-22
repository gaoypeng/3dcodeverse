// src/shaders/sky.js — gradient sky dome (raw GLSL in a ShaderMaterial).
// Pattern: uniforms object + vertex/fragment strings + `#include` chunks ALONE on their line.
// No #version / precision lines (three.js prepends them).  gl_FragColor is fine (no glslVersion).
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
  float h = clamp(vWorldDir.y, -0.2, 1.0);
  vec3 col = mix(uHorizon, uZenith, pow(max(h, 0.0), 0.55));
  float sun = pow(max(dot(normalize(vWorldDir), normalize(uSunDir)), 0.0), 180.0);
  col += vec3(1.0, 0.9, 0.7) * sun * 1.5;
  // slow drifting haze band so the sky is visibly alive in update(t)
  col += 0.04 * sin(vWorldDir.x * 6.0 + uTime * 0.15) * (1.0 - h);
  gl_FragColor = vec4(col, 1.0);
}
`;

export function makeSkyMaterial(T = THREE) {
  return new T.ShaderMaterial({
    uniforms: {
      uZenith: { value: new T.Color(0x3f74c9) },
      uHorizon: { value: new T.Color(0xcfdcec) },
      uSunDir: { value: new T.Vector3(-0.45, 0.6, -0.65) },
      uTime: { value: 0 },
    },
    vertexShader,
    fragmentShader,
    side: T.BackSide,
    depthWrite: false,
    fog: false,
  });
}
