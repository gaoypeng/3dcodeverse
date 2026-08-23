// Headless-Chrome launcher for the WebGL renderers (render_glb / render_scene / probes).
//
// On this WSL2 box hardware WebGL needs BOTH the ANGLE gl-egl flags AND the
// Mesa d3d12 env trio; either alone silently lands on llvmpipe.  So every GPU
// attempt is verified through UNMASKED_RENDERER_WEBGL before it is trusted,
// and a negative verdict is cached (with a TTL) so the slow-to-fail GPU launch
// is not repeated on every render.
//
//   const { launchBrowser, rendererInfo } = require('./gpu_launch.cjs');
//   const { browser, gpu, renderer } = await launchBrowser({ gpu: 'auto' });
//
// gpu: 'auto' (probe, cached) | 'on' (require hardware; throw otherwise) | 'off' (CPU).
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const CACHE_DIR = process.env.C3V_CACHE_DIR || path.join(os.homedir(), '.cache', 'codeverse');
const CACHE_PATH = path.join(CACHE_DIR, 'gpu_probe.json');
const NEGATIVE_TTL_MS = 20 * 60 * 1000; // how long a "no GPU" verdict is trusted
const PROTOCOL_TIMEOUT_MS = 15 * 60 * 1000; // big frame batches exceed puppeteer's 180 s default

const BASE_ARGS = ['--no-sandbox', '--disable-setuid-sandbox', '--enable-webgl', '--ignore-gpu-blocklist'];
const GPU_ARGS = [...BASE_ARGS, '--enable-gpu-rasterization', '--use-angle=gl-egl'];
const CPU_ARGS = [...BASE_ARGS, '--enable-unsafe-swiftshader', '--use-angle=swiftshader'];
const GPU_ENV = {
  GALLIUM_DRIVER: 'd3d12',
  MESA_LOADER_DRIVER_OVERRIDE: 'd3d12',
  MESA_D3D12_DEFAULT_ADAPTER_NAME: 'NVIDIA',
};
const SOFTWARE_RE = /swiftshader|llvmpipe|softpipe|software|basic render/i;

function readCache() {
  try {
    return JSON.parse(fs.readFileSync(CACHE_PATH, 'utf8'));
  } catch (_e) {
    return null;
  }
}

function writeCache(decision) {
  try {
    fs.mkdirSync(CACHE_DIR, { recursive: true });
    fs.writeFileSync(CACHE_PATH, JSON.stringify({ ...decision, at: Date.now() }, null, 2));
  } catch (_e) {
    /* cache is an optimisation only */
  }
}

/**
 * Probe the browser's WebGL backend. Returns the UNMASKED_RENDERER string or null
 * when no WebGL context can be created.
 */
async function rendererInfo(browser) {
  const page = await browser.newPage();
  try {
    return await page.evaluate(() => {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
      if (!gl) return null;
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      const r = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      return String(r);
    });
  } finally {
    await page.close().catch(() => {});
  }
}

function loadPuppeteer(puppeteer) {
  return puppeteer || require('puppeteer');
}

async function launchCpu(puppeteer) {
  const env = { ...process.env };
  for (const k of Object.keys(GPU_ENV)) delete env[k];
  const browser = await puppeteer.launch({
    headless: true,
    args: CPU_ARGS,
    env,
    protocolTimeout: PROTOCOL_TIMEOUT_MS,
  });
  let renderer = 'SwiftShader (CPU)';
  try {
    renderer = (await rendererInfo(browser)) || renderer;
  } catch (_e) {
    /* keep the default label */
  }
  return { browser, gpu: false, renderer };
}

async function tryGpu(puppeteer) {
  let browser = null;
  try {
    browser = await puppeteer.launch({
      headless: true,
      args: GPU_ARGS,
      env: { ...process.env, ...GPU_ENV },
      protocolTimeout: PROTOCOL_TIMEOUT_MS,
    });
    const renderer = await rendererInfo(browser);
    if (!renderer || SOFTWARE_RE.test(renderer)) {
      await browser.close().catch(() => {});
      return { browser: null, renderer: renderer || 'no WebGL context' };
    }
    return { browser, renderer };
  } catch (e) {
    if (browser) await browser.close().catch(() => {});
    return { browser: null, renderer: `launch failed: ${e.message}` };
  }
}

/**
 * Launch headless Chrome on the best available WebGL backend.
 * @param {{gpu?: 'auto'|'on'|'off', puppeteer?: object}} opts
 * @returns {Promise<{browser: object, gpu: boolean, renderer: string}>}
 */
async function launchBrowser(opts = {}) {
  const mode = String(opts.gpu || process.env.C3V_RENDER_GPU || 'auto').toLowerCase();
  const puppeteer = loadPuppeteer(opts.puppeteer);
  if (!['auto', 'on', 'off'].includes(mode)) throw new Error(`gpu must be auto|on|off, got ${mode}`);

  if (mode === 'off') return launchCpu(puppeteer);

  if (mode === 'on') {
    const attempt = await tryGpu(puppeteer);
    if (!attempt.browser) throw new Error(`gpu=on but no hardware WebGL: ${attempt.renderer}`);
    return { browser: attempt.browser, gpu: true, renderer: attempt.renderer };
  }

  // auto: a fresh negative verdict skips the slow GPU attempt; a positive one is
  // always re-verified live (cheap, and the GPU can go away).
  const cached = readCache();
  const fresh = cached && typeof cached.at === 'number' && Date.now() - cached.at < NEGATIVE_TTL_MS;
  if (cached && cached.use_gpu === false && fresh) return launchCpu(puppeteer);

  const attempt = await tryGpu(puppeteer);
  if (attempt.browser) {
    writeCache({ use_gpu: true, renderer: attempt.renderer });
    return { browser: attempt.browser, gpu: true, renderer: attempt.renderer };
  }
  writeCache({ use_gpu: false, renderer: attempt.renderer });
  return launchCpu(puppeteer);
}

module.exports = { launchBrowser, rendererInfo, GPU_ARGS, CPU_ARGS, GPU_ENV, CACHE_PATH };
