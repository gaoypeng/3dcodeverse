/**
 * Thin node-side fallbacks for what package C1 provides
 * (`runtime_js/gpu_launch.cjs` → launchBrowser, `runtime_js/serve.cjs` →
 * static server with import-map injection).  Each helper first tries the C1
 * module and uses it when it exposes the expected function; otherwise a local
 * implementation with the same behaviour is used so this package runs alone.
 */

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require_ = createRequire(import.meta.url);
export const RUNTIME_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const BASE_ARGS = ['--no-sandbox', '--disable-setuid-sandbox', '--enable-webgl', '--ignore-gpu-blocklist'];
const GPU_ARGS = [...BASE_ARGS, '--enable-gpu-rasterization', '--use-angle=gl-egl'];
const CPU_ARGS = [...BASE_ARGS, '--enable-unsafe-swiftshader', '--use-angle=swiftshader'];
const GPU_ENV = {
  GALLIUM_DRIVER: 'd3d12',
  MESA_LOADER_DRIVER_OVERRIDE: 'd3d12',
  MESA_D3D12_DEFAULT_ADAPTER_NAME: 'NVIDIA',
};
const SOFTWARE_RE = /swiftshader|llvmpipe|softpipe|software|basic render/i;

function tryRequire(rel) {
  try {
    return require_(path.join(RUNTIME_ROOT, rel));
  } catch (e) {
    if (e && e.code === 'MODULE_NOT_FOUND' && String(e.message).includes(rel.replace('./', ''))) return null;
    throw e;
  }
}

async function probeRenderer(browser) {
  const page = await browser.newPage();
  try {
    return await page.evaluate(() => {
      const c = document.createElement('canvas');
      const gl = c.getContext('webgl2') || c.getContext('webgl');
      if (!gl) return null;
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      return String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    });
  } finally {
    await page.close();
  }
}

async function launchWith(puppeteer, args, env) {
  const saved = {};
  for (const k of Object.keys(GPU_ENV)) {
    saved[k] = process.env[k];
    if (env[k] !== undefined) process.env[k] = env[k];
    else delete process.env[k];
  }
  try {
    return await puppeteer.launch({ headless: true, args, protocolTimeout: 600000 });
  } finally {
    for (const k of Object.keys(saved)) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  }
}

/**
 * Local launcher: GPU attempt (WSL2 d3d12 + gl-egl) verified via the
 * unmasked renderer string, else SwiftShader CPU.
 * @param {{gpu?: 'auto'|'on'|'off'}} opts
 * @returns {Promise<{browser: object, gpu: boolean, renderer: string}>}
 */
async function localLaunchBrowser({ gpu = 'auto' } = {}) {
  const puppeteer = require_('puppeteer');
  if (gpu !== 'off') {
    let browser = null;
    try {
      browser = await launchWith(puppeteer, GPU_ARGS, GPU_ENV);
      const renderer = await probeRenderer(browser);
      if (renderer && !SOFTWARE_RE.test(renderer)) return { browser, gpu: true, renderer };
      await browser.close().catch(() => {});
      if (gpu === 'on') throw new Error(`gpu=on but WebGL renderer is software/none: ${renderer}`);
    } catch (e) {
      if (browser) await browser.close().catch(() => {});
      if (gpu === 'on') throw e;
    }
  }
  const browser = await launchWith(puppeteer, CPU_ARGS, {});
  const renderer = (await probeRenderer(browser)) || 'SwiftShader (CPU)';
  return { browser, gpu: false, renderer };
}

/** Launch a headless browser; C1's gpu_launch.cjs when present. */
export async function launchBrowser(opts = {}) {
  const mod = tryRequire('./gpu_launch.cjs');
  if (mod && typeof mod.launchBrowser === 'function') {
    const res = await mod.launchBrowser(opts);
    if (res && res.browser) return { browser: res.browser, gpu: !!res.gpu, renderer: String(res.renderer || '') };
  }
  return localLaunchBrowser(opts);
}

const MIME = {
  '.js': 'text/javascript', '.mjs': 'text/javascript', '.cjs': 'text/javascript', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.hdr': 'application/octet-stream',
  '.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json', '.html': 'text/html', '.css': 'text/css', '.wasm': 'application/wasm',
  '.ktx2': 'image/ktx2', '.svg': 'image/svg+xml', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.txt': 'text/plain',
};

const LOCAL_MOUNT = '/__rt/';

/** URL prefix under which runtime_js is served ('/__runtime/' with C1's serve.cjs). */
export function runtimeMount() {
  const mod = tryRequire('./serve.cjs');
  return mod && typeof mod.RUNTIME_MOUNT === 'string' ? mod.RUNTIME_MOUNT : LOCAL_MOUNT;
}

/** Import map that resolves 'three' and 'three/addons/*' to the local runtime copy. */
export function importMapHtml() {
  const mod = tryRequire('./serve.cjs');
  if (mod && typeof mod.importMapHtml === 'function') return mod.importMapHtml();
  const m = LOCAL_MOUNT;
  const map = {
    imports: {
      three: `${m}node_modules/three/build/three.module.js`,
      'three/addons/': `${m}node_modules/three/examples/jsm/`,
      'three/examples/jsm/': `${m}node_modules/three/examples/jsm/`,
    },
  };
  return `<script type="importmap">${JSON.stringify(map)}</script>`;
}

function safeJoin(root, rel) {
  const abs = path.resolve(root, '.' + rel);
  if (abs !== root && !abs.startsWith(root + path.sep)) return null;
  return abs;
}

/**
 * Serve a workspace on 127.0.0.1:0 with these mounts:
 *   /src/** /public/**      → <ws>/src, <ws>/public
 *   /assets/**              → <ws>/public/assets  (contract: '/assets/x.glb')
 *   <runtimeMount()>/**     → runtime_js (node_modules/three, lib/*.mjs)
 *   /__host.html            → generated host page (import map + scene_host.mjs)
 * Uses C1's serve.cjs when present (same mounts); returns { server, base, close() }.
 */
export async function serveWorkspace(wsRoot, { runtimeRoot = RUNTIME_ROOT, hostHtml = '' } = {}) {
  wsRoot = path.resolve(wsRoot);
  runtimeRoot = path.resolve(runtimeRoot);
  const c1 = tryRequire('./serve.cjs');
  if (c1 && typeof c1.serveDirs === 'function') {
    const srv = await c1.serveDirs({
      root: wsRoot,
      mounts: { '/assets/': path.join(wsRoot, 'public', 'assets') },
      routes: { '/__host.html': { body: hostHtml, type: 'text/html; charset=utf-8' } },
    });
    return { server: srv.server, base: srv.base, close: srv.close };
  }
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent((req.url || '/').split('?')[0]);
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Cache-Control', 'no-store');
    if (rel === '/favicon.ico') { res.statusCode = 204; res.end(); return; }
    if (rel === '/__host.html') {
      res.setHeader('Content-Type', 'text/html');
      res.end(hostHtml);
      return;
    }
    let abs = null;
    if (rel.startsWith(LOCAL_MOUNT)) abs = safeJoin(runtimeRoot, rel.slice(LOCAL_MOUNT.length - 1));
    else if (rel.startsWith('/assets/')) abs = safeJoin(path.join(wsRoot, 'public'), rel);
    else abs = safeJoin(wsRoot, rel);
    if (!abs || !fs.existsSync(abs) || !fs.statSync(abs).isFile()) {
      res.statusCode = 404;
      res.end('not found: ' + rel);
      return;
    }
    res.setHeader('Content-Type', MIME[path.extname(abs).toLowerCase()] || 'application/octet-stream');
    fs.createReadStream(abs).pipe(res);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  return { server, base, close: () => new Promise((r) => server.close(() => r())) };
}
