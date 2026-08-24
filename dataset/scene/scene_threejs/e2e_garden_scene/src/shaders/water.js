// src/shaders/water.js — animated water: ShaderMaterial WITH scene fog support.
//   makeWaterMaterial(THREE) → ShaderMaterial (uniforms.uTime driven from update)
// Copyable boilerplate for "a custom material that still fogs":
//   fog: true + UniformsUtils.merge([UniformsLib.fog, {...}]) + fog chunks in both stages.
import * as THREE from 'three';

const vertexShader = /* glsl */ `
#include <fog_pars_vertex>
uniform float uTime;
varying vec2 vUv;
varying vec3 vWorldPos;
varying vec3 vNormalW;
void main() {
  vUv = uv;
  vec3 p = position;
  float w = sin(p.x * 1.7 + uTime * 1.3) * 0.04 + cos(p.z * 2.3 - uTime * 1.1) * 0.03;
  p.y += w;
  vec4 wp = modelMatrix * vec4(p, 1.0);
  vWorldPos = wp.xyz;
  vNormalW = normalize(mat3(modelMatrix) * vec3(-cos(p.x * 1.7 + uTime * 1.3) * 0.07, 1.0, sin(p.z * 2.3 - uTime * 1.1) * 0.07));
  vec4 mvPosition = viewMatrix * wp;
  gl_Position = projectionMatrix * mvPosition;
  #include <fog_vertex>
}
`;

const fragmentShader = /* glsl */ `
#include <fog_pars_fragment>
uniform float uTime;
uniform vec3 uDeep;
uniform vec3 uShallow;
uniform vec3 uSunDir;
varying vec2 vUv;
varying vec3 vWorldPos;
varying vec3 vNormalW;
void main() {
  vec3 V = normalize(cameraPosition - vWorldPos);
  vec3 N = normalize(vNormalW);
  float fres = pow(1.0 - max(dot(N, V), 0.0), 3.0);
  float ripple = 0.5 + 0.5 * sin((vUv.x + vUv.y) * 40.0 + uTime * 2.0);
  vec3 col = mix(uDeep, uShallow, 0.35 * ripple + 0.65 * fres);
  vec3 H = normalize(V + normalize(uSunDir));
  col += vec3(1.0, 0.95, 0.85) * pow(max(dot(N, H), 0.0), 120.0) * 0.8;
  gl_FragColor = vec4(col, 0.92);
  #include <fog_fragment>
}
`;

export function makeWaterMaterial(T = THREE) {
  const uniforms = T.UniformsUtils.merge([
    T.UniformsLib.fog,
    {
      uTime: { value: 0 },
      uDeep: { value: new T.Color(0x1a4d6b) },
      uShallow: { value: new T.Color(0x6fb3c9) },
      uSunDir: { value: new T.Vector3(-0.45, 0.6, -0.65) },
    },
  ]);
  return new T.ShaderMaterial({ uniforms, vertexShader, fragmentShader, transparent: true, fog: true, side: T.DoubleSide });
}
