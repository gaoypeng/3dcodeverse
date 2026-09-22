// Compile every `make*Material(THREE, opts)` export of a module in headless Chrome (real WebGL)
// and report shader errors.  Usage: node glsl_compile.mjs <dir-with-node_modules-and-module> <module.mjs>
// Prints one JSON line: {ok, gpu, results:[{name, mesh:{ok, errors}, instanced:{ok, errors}}]}
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import url from 'node:url';

const RUNTIME = process.env.C3D_RUNTIME_JS
  || path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '../../../runtime_js');
const require_ = createRequire(path.join(RUNTIME, 'package.json'));
const puppeteer = require_('puppeteer');

const [,, rootArg, moduleRel] = process.argv;
const root = path.resolve(rootArg);
const MIME = { '.js': 'text/javascript', '.mjs': 'text/javascript', '.html': 'text/html', '.json': 'application/json', '.glb': 'model/gltf-binary' };

const html = `<!doctype html><html><head><meta charset="utf-8">
<script type="importmap">{"imports":{"three":"/node_modules/three/build/three.module.js","three/addons/":"/node_modules/three/examples/jsm/"}}</script>
</head><body><script type="module">
import * as THREE from 'three';
import * as mod from '/${moduleRel}';
const out = { results: [], gpu: '' };
const canvas = document.createElement('canvas'); canvas.width = 256; canvas.height = 256; document.body.appendChild(canvas);
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, logarithmicDepthBuffer: true });
renderer.setSize(256, 256, false);
renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.shadowMap.enabled = true;
const gl = renderer.getContext(); const ext = gl.getExtension('WEBGL_debug_renderer_info');
out.gpu = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'unknown';
let errs = [];
renderer.debug.onShaderError = (gl, program, vs, fs) => {
  const vlog = gl.getShaderInfoLog(vs) || '', flog = gl.getShaderInfoLog(fs) || '', plog = gl.getProgramInfoLog(program) || '';
  errs.push(('VS:' + vlog + ' FS:' + flog + ' P:' + plog).slice(0, 1500));
};
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 500); camera.position.set(3, 3, 5); camera.lookAt(0, 0, 0);
const names = Object.keys(mod).filter(k => /^make\\w*Material$/.test(k) && typeof mod[k] === 'function');
for (const name of names) {
  const res = { name };
  for (const kind of ['mesh', 'instanced']) {
    errs = [];
    const scene = new THREE.Scene(); scene.fog = new THREE.Fog(0x8899aa, 5, 50);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1), new THREE.DirectionalLight(0xffffff, 1));
    let mat;
    try { mat = mod[name](THREE, {}); } catch (e) { res[kind] = { ok: false, errors: ['factory threw: ' + (e && e.message)] }; continue; }
    const geo = new THREE.PlaneGeometry(2, 2, 4, 4);
    const obj = kind === 'mesh' ? new THREE.Mesh(geo, mat) : new THREE.InstancedMesh(geo, mat, 4);
    if (kind === 'instanced') { const m = new THREE.Matrix4(); for (let i = 0; i < 4; i++) { m.makeTranslation(i, 0, 0); obj.setMatrixAt(i, m); } }
    scene.add(obj);
    try {
      if (mat.uniforms && mat.uniforms.uTime) mat.uniforms.uTime.value = 1.0;
      if (typeof mat.userData?.update === 'function') mat.userData.update(1.0, 0.016);
      renderer.compile(scene, camera); renderer.render(scene, camera);
    } catch (e) { errs.push('render threw: ' + (e && e.message)); }
    res[kind] = { ok: errs.length === 0, errors: errs.slice() };
  }
  out.results.push(res);
}
out.ok = out.results.every(r => r.mesh.ok && r.instanced.ok) && out.results.length > 0;
window.__result = out;
</script></body></html>`;

const server = http.createServer((req, res) => {
  const url = decodeURIComponent(req.url.split('?')[0]);
  if (url === '/' || url === '/index.html') { res.writeHead(200, { 'content-type': 'text/html' }); res.end(html); return; }
  const abs = path.join(root, url);
  if (!abs.startsWith(root) || !fs.existsSync(abs) || !fs.statSync(abs).isFile()) { res.writeHead(404); res.end('nf'); return; }
  res.writeHead(200, { 'content-type': MIME[path.extname(abs)] || 'application/octet-stream' }); res.end(fs.readFileSync(abs));
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;
const GPU_ENV = { GALLIUM_DRIVER: 'd3d12', MESA_LOADER_DRIVER_OVERRIDE: 'd3d12', MESA_D3D12_DEFAULT_ADAPTER_NAME: 'NVIDIA' };
const BASE = ['--no-sandbox', '--disable-setuid-sandbox', '--enable-webgl', '--ignore-gpu-blocklist'];
async function launch(gpu) {
  return puppeteer.launch({ headless: true, protocolTimeout: 120000,
    args: gpu ? [...BASE, '--enable-gpu-rasterization', '--use-angle=gl-egl'] : [...BASE, '--enable-unsafe-swiftshader', '--use-angle=swiftshader'],
    env: gpu ? { ...process.env, ...GPU_ENV } : process.env });
}
let result = null, lastErr = '';
for (const gpu of [true, false]) {
  let browser;
  try {
    browser = await launch(gpu);
    const page = await browser.newPage();
    const consoleErrs = [];
    page.on('console', m => { if (m.type() === 'error') consoleErrs.push(m.text().slice(0, 400)); });
    page.on('pageerror', e => consoleErrs.push('pageerror: ' + e.message));
    await page.goto(base + '/', { waitUntil: 'load', timeout: 60000 });
    await page.waitForFunction('window.__result !== undefined', { timeout: 90000 });
    result = await page.evaluate(() => window.__result);
    result.console = consoleErrs.filter(t => !/GL_INVALID|THREE\.WebGLProgram/.test(t));
    result.mode = gpu ? 'gpu' : 'cpu';
    if (!result.gpu || /swiftshader|llvmpipe|software/i.test(result.gpu)) { if (gpu) { await browser.close(); continue; } }
    break;
  } catch (e) { lastErr = String(e && e.message); } finally { if (browser) await browser.close().catch(() => {}); }
}
server.close();
if (!result) { console.log(JSON.stringify({ ok: false, error: lastErr || 'no result' })); process.exit(2); }
console.log(JSON.stringify(result));
process.exit(result.ok ? 0 : 1);
