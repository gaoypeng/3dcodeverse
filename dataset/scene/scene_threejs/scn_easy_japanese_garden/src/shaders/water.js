// src/shaders/water.js — animated reflective water: ShaderMaterial WITH scene fog support.
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
  
  // Dual-frequency gentle animated ripples
  float w1 = sin(p.x * 3.5 + uTime * 2.2) * cos(p.z * 2.8 + uTime * 1.8) * 0.012;
  float w2 = sin((p.x + p.z) * 5.0 - uTime * 2.5) * 0.008;
  p.y += w1 + w2;
  
  vec4 wp = modelMatrix * vec4(p, 1.0);
  vWorldPos = wp.xyz;
  
  // Compute animated surface normal
  float dx = cos(p.x * 3.5 + uTime * 2.2) * 3.5 * cos(p.z * 2.8 + uTime * 1.8) * 0.012 + cos((p.x + p.z) * 5.0 - uTime * 2.5) * 5.0 * 0.008;
  float dz = -sin(p.x * 3.5 + uTime * 2.2) * sin(p.z * 2.8 + uTime * 1.8) * 2.8 * 0.012 + cos((p.x + p.z) * 5.0 - uTime * 2.5) * 5.0 * 0.008;
  vec3 nLocal = normalize(vec3(-dx, 1.0, -dz));
  vNormalW = normalize(mat3(modelMatrix) * nLocal);
  
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
uniform vec3 uSkyReflect;
varying vec2 vUv;
varying vec3 vWorldPos;
varying vec3 vNormalW;

void main() {
  vec3 V = normalize(cameraPosition - vWorldPos);
  vec3 N = normalize(vNormalW);
  
  // Fresnel reflection factor
  float NdotV = max(dot(N, V), 0.0);
  float fresnel = 0.04 + 0.96 * pow(1.0 - NdotV, 4.0);
  
  // Base pond water tone
  vec3 baseWater = mix(uDeep, uShallow, 0.25);
  vec3 waterColor = mix(baseWater, uSkyReflect, fresnel * 0.85);
  
  // Golden sun specular glint
  vec3 S = normalize(uSunDir);
  vec3 H = normalize(V + S);
  float spec = pow(max(dot(N, H), 0.0), 90.0);
  vec3 sunSpecular = vec3(1.0, 0.95, 0.82) * spec * 1.6;
  
  gl_FragColor = vec4(waterColor + sunSpecular, 0.94);
  #include <fog_fragment>
}
`;

export function makeWaterMaterial(T = THREE) {
  const uniforms = T.UniformsUtils.merge([
    T.UniformsLib.fog,
    {
      uTime: { value: 0 },
      uDeep: { value: new T.Color(0x18382c) },       // deep murky pond green/teal
      uShallow: { value: new T.Color(0x356358) },    // shallow clear mossy water
      uSkyReflect: { value: new T.Color(0xa2c4d9) },  // sky reflection
      uSunDir: { value: new T.Vector3(-0.45, 0.6, -0.65) },
    },
  ]);
  return new T.ShaderMaterial({
    name: 'Water',
    uniforms,
    vertexShader,
    fragmentShader,
    transparent: true,
    fog: true,
    side: T.DoubleSide,
  });
}
