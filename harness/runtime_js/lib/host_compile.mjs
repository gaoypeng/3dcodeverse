/**
 * Compile-time material machinery (page-side): capture the FINAL GLSL sources
 * of every custom material (ShaderMaterial directly; onBeforeCompile via a
 * cache-key-preserving wrapper), attribute compiler errors to materials by
 * source-line match, audit runtime materials (fog/uTime), and swap custom
 * shaders for plain ones for the counterfactual presence probe.
 */

import { isCustomShader } from './host_census.mjs';

export const captured = []; // {name, type, vs, fs} final shader sources per custom material

export function captureMaterialSources(scene, THREE) {
  const seen = new Set();
  scene.traverse((o) => {
    if (!o.material) return;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      if (!m || seen.has(m.uuid) || m.__c3vCaptured) continue;
      seen.add(m.uuid);
      const name = m.name || `${m.type} on ${o.name || o.type}`;
      if (m.isShaderMaterial || m.isRawShaderMaterial) {
        captured.push({ name, type: m.type, vs: m.vertexShader, fs: m.fragmentShader, uuid: m.uuid });
        m.__c3vCaptured = true;
        continue;
      }
      if (!Object.prototype.hasOwnProperty.call(m, 'onBeforeCompile')) continue;
      // Wrap the patch to record the FINAL sources.  Keep the program cache key
      // identical so wrapped materials never share a program by accident.
      const orig = m.onBeforeCompile;
      const origKey = m.customProgramCacheKey();
      m.onBeforeCompile = function (shader, renderer) {
        orig.call(this, shader, renderer);
        captured.push({ name, type: m.type, vs: shader.vertexShader, fs: shader.fragmentShader, uuid: m.uuid });
      };
      m.customProgramCacheKey = () => origKey;
      m.__c3vCaptured = true;
    }
  });
}

export function attributeErrors(errs) {
  for (const e of errs) {
    if (e.material || !e.source_line) continue;
    const needle = e.source_line.trim();
    const hits = captured.filter((c) => (e.stage === 'vertex' ? c.vs : c.fs).split('\n').some((l) => l.trim() === needle));
    if (hits.length === 1) { e.material = hits[0].name; e.material_type = hits[0].type; }
    else if (hits.length > 1) { e.material = hits.map((h) => h.name).slice(0, 3).join(' | '); e.material_type = 'ambiguous'; }
  }
}

export function materialAudit(scene, THREE) {
  const out = [];
  const fog = !!scene.fog;
  const seen = new Set();
  scene.traverse((o) => {
    if (!o.material) return;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      if (!m || seen.has(m.uuid) || !m.isShaderMaterial) continue;
      seen.add(m.uuid);
      const name = m.name || `${m.type} on ${o.name || o.type}`;
      const fs = String(m.fragmentShader || '');
      const skyLike = /sky|dome|stars|cloud|sun|moon/i.test(`${o.name} ${m.name}`) || (m.side === THREE.BackSide && m.depthWrite === false);
      if (fog && !skyLike && !(m.fog && m.uniforms && m.uniforms.fogColor) && !/fog_fragment|fogColor/.test(fs)) {
        out.push({ kind: 'no_fog', severity: 'warn', material: name, message: `ShaderMaterial '${name}' ignores scene.fog (set fog: true, merge THREE.UniformsLib.fog, include fog_pars_fragment/fog_fragment)` });
      }
      if (/\buTime\b/.test(fs + String(m.vertexShader || '')) && !(m.uniforms && m.uniforms.uTime)) {
        out.push({ kind: 'unbound_uniform', severity: 'error', material: name, message: `ShaderMaterial '${name}' reads uTime but uniforms.uTime is missing` });
      }
    }
  });
  return out;
}



/**
 * Counterfactual: swap custom-shader materials for plain ones (ShaderMaterial →
 * grey MeshStandardMaterial, onBeforeCompile → unpatched clone).  Returns an
 * undo function.  Used by the shader-presence probe (pixel diff vs normal).
 */
export function stripCustomShaders(scene, THREE) {
  const undo = [];
  const cloneCache = new Map();
  scene.traverse((o) => {
    if (!o.material) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    const swapped = mats.map((m) => {
      if (!m || !isCustomShader(m, THREE)) return m;
      if (cloneCache.has(m.uuid)) return cloneCache.get(m.uuid);
      let rep;
      if (m.isShaderMaterial || m.isRawShaderMaterial) {
        rep = new THREE.MeshStandardMaterial({ color: 0x808080, roughness: 0.8, side: m.side, transparent: m.transparent, opacity: m.opacity, depthWrite: m.depthWrite });
      } else {
        rep = m.clone();
        delete rep.onBeforeCompile; // clone() does not copy the own patch, but be explicit
        rep.customProgramCacheKey = THREE.Material.prototype.customProgramCacheKey;
      }
      cloneCache.set(m.uuid, rep);
      return rep;
    });
    if (swapped.some((m, i) => m !== mats[i])) {
      const orig = o.material;
      o.material = Array.isArray(orig) ? swapped : swapped[0];
      undo.push(() => { o.material = orig; });
    }
  });
  return () => { for (const u of undo) u(); for (const m of cloneCache.values()) m.dispose(); };
}

