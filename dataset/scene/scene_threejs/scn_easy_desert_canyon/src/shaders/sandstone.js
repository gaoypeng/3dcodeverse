import * as THREE from 'three';

// Procedural Sandstone Shader Material with 3D noise for natural striations and grain
export function makeSandstoneMaterial(T = THREE, opts = {}) {
  const baseColor = opts.color !== undefined ? new T.Color(opts.color) : new T.Color(0xb55a36);
  const darkColor = opts.darkColor !== undefined ? new T.Color(opts.darkColor) : new T.Color(0x752e18);
  const lightColor = opts.lightColor !== undefined ? new T.Color(opts.lightColor) : new T.Color(0xd98a58);
  const roughness = opts.roughness !== undefined ? opts.roughness : 0.88;

  const mat = new T.MeshStandardMaterial({
    color: baseColor,
    roughness: roughness,
    metalness: 0.02,
    flatShading: opts.flatShading || false
  });

  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uDarkColor = { value: darkColor };
    shader.uniforms.uLightColor = { value: lightColor };
    shader.uniforms.uStrataScale = { value: opts.strataScale || 0.4 };

    shader.vertexShader = `
      varying vec3 vWorldPosition;
      varying vec3 vLocalPosition;
      ${shader.vertexShader}
    `.replace(
      '#include <begin_vertex>',
      `
      #include <begin_vertex>
      vLocalPosition = position;
      vWorldPosition = (modelMatrix * vec4(position, 1.0)).xyz;
      `
    );

    shader.fragmentShader = `
      uniform vec3 uDarkColor;
      uniform vec3 uLightColor;
      uniform float uStrataScale;
      varying vec3 vWorldPosition;
      varying vec3 vLocalPosition;

      // Simple hash & 3D value noise
      float hash(vec3 p) {
        p = fract(p * 0.3183099 + 0.1);
        p *= 17.0;
        return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
      }

      float noise(vec3 x) {
        vec3 p = floor(x);
        vec3 f = fract(x);
        f = f * f * (3.0 - 2.0 * f);
        return mix(
          mix(mix(hash(p + vec3(0,0,0)), hash(p + vec3(1,0,0)), f.x),
              mix(hash(p + vec3(0,1,0)), hash(p + vec3(1,1,0)), f.x), f.y),
          mix(mix(hash(p + vec3(0,0,1)), hash(p + vec3(1,0,1)), f.x),
              mix(hash(p + vec3(0,1,1)), hash(p + vec3(1,1,1)), f.x), f.y), f.z);
      }

      ${shader.fragmentShader}
    `.replace(
      '#include <color_fragment>',
      `
      #include <color_fragment>
      
      // Geological sedimentary horizontal strata bands
      float yPos = vWorldPosition.y + noise(vWorldPosition * 0.15) * 1.5;
      float strata = sin(yPos * uStrataScale * 3.14159) * 0.5 + 0.5;
      strata = smoothstep(0.2, 0.8, strata);
      
      // Fine rock grain and micro-roughness
      float grain = noise(vWorldPosition * 1.8) * 0.6 + noise(vWorldPosition * 4.5) * 0.4;
      
      // Secondary weathered patina / mineral streaks
      float streak = noise(vec3(vWorldPosition.x * 0.5, vWorldPosition.y * 0.08, vWorldPosition.z * 0.5));
      
      vec3 rockCol = mix(diffuseColor.rgb, uDarkColor, (1.0 - strata) * 0.55);
      rockCol = mix(rockCol, uLightColor, strata * 0.45);
      rockCol += (grain - 0.5) * 0.12;
      rockCol = mix(rockCol, uDarkColor, streak * 0.25);

      diffuseColor.rgb = clamp(rockCol, 0.0, 1.0);
      `
    );
  };

  return mat;
}
