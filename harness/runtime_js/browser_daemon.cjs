#!/usr/bin/env node
// Detached keeper of ONE shared headless-Chrome per backend ('gpu'|'cpu').
//
//   node browser_daemon.cjs --backend gpu|cpu
//
// Spawned (detached, stdio ignored) by gpu_launch.cjs when no shared browser
// is advertised.  Launches the browser (same verified GPU/CPU paths as a
// direct launch), advertises its ws endpoint in CACHE_DIR/browser_<backend>.json
// and reaps it when idle: clients touch that file's mtime on connect/release,
// so a stale heartbeat (~90 s) with no open pages means nobody is using it.
// A very stale heartbeat (10 min) reaps even with leaked pages (crashed
// client).  On browser death or a superseded endpoint file the daemon exits.
'use strict';

const fs = require('fs');
const path = require('path');
const { _internal } = require('./gpu_launch.cjs');

const IDLE_REAP_MS = 90 * 1000;
const HARD_REAP_MS = 10 * 60 * 1000;
const POLL_MS = 15 * 1000;
// A page older than this belongs to nobody: every harness render/probe finishes or
// times out well under it (ceilings <= 330 s), and a SIGKILLed client never closes
// its page.  Leaked pages hold WebGL contexts that eventually wedge the browser.
const PAGE_TTL_MS = Number(process.env.CV3D_PAGE_TTL_MS || 8 * 60 * 1000);

function arg(name, dflt) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}

async function main() {
  const backend = arg('backend', 'cpu');
  if (!['gpu', 'cpu'].includes(backend)) throw new Error(`--backend must be gpu|cpu, got ${backend}`);
  const puppeteer = _internal.loadPuppeteer();
  const epPath = _internal.endpointPath(backend);
  const lockPath = path.join(_internal.CACHE_DIR, `browser_${backend}.lock`);
  fs.mkdirSync(_internal.CACHE_DIR, { recursive: true });

  let browser = null;
  let gpu = false;
  let renderer = '';
  try {
    if (backend === 'gpu') {
      const attempt = await _internal.tryGpu(puppeteer);
      if (!attempt.browser) throw new Error(attempt.renderer);
      browser = attempt.browser;
      gpu = true;
      renderer = attempt.renderer;
    } else {
      const h = await _internal.launchCpu(puppeteer);
      browser = h.browser;
      renderer = h.renderer;
    }
  } catch (e) {
    fs.writeFileSync(_internal.daemonFailPath(backend), JSON.stringify({ error: String(e.message || e), at: Date.now() }));
    fs.rmSync(lockPath, { force: true });
    process.exit(1);
  }

  const record = { ws: browser.wsEndpoint(), pid: browser.process() ? browser.process().pid : null, gpu, renderer, daemon_pid: process.pid, at: Date.now() };
  fs.writeFileSync(epPath, JSON.stringify(record, null, 2));
  fs.rmSync(lockPath, { force: true });

  const bail = async (code) => {
    const cur = (() => { try { return JSON.parse(fs.readFileSync(epPath, 'utf8')); } catch (_e) { return null; } })();
    if (cur && cur.ws === record.ws) fs.rmSync(epPath, { force: true });
    try { await browser.close(); } catch (_e) { /* already dead */ }
    process.exit(code);
  };

  browser.on('disconnected', () => {
    // our own connection died with the browser process
    try { const cur = JSON.parse(fs.readFileSync(epPath, 'utf8')); if (cur.ws === record.ws) fs.rmSync(epPath, { force: true }); } catch (_e) { /* ignore */ }
    process.exit(0);
  });

  const firstSeen = new Map();   // Page -> first-seen ms (puppeteer caches Page objects per target)
  setInterval(async () => {
    let st = null;
    try {
      st = fs.statSync(epPath);
    } catch (_e) {
      return bail(0);   // endpoint deleted (poisoned verdict or manual cleanup)
    }
    const cur = (() => { try { return JSON.parse(fs.readFileSync(epPath, 'utf8')); } catch (_e) { return null; } })();
    if (!cur || cur.ws !== record.ws) return bail(0);   // superseded by another daemon
    try {   // reap pages leaked by killed clients (never the initial about:blank)
      const open = await browser.pages();
      const now = Date.now();
      const live = new Set(open);
      for (const p of open.slice(1)) if (!firstSeen.has(p)) firstSeen.set(p, now);
      for (const [p, at] of firstSeen) {
        if (!live.has(p)) firstSeen.delete(p);
        else if (now - at > PAGE_TTL_MS) { firstSeen.delete(p); await p.close().catch(() => {}); }
      }
    } catch (_e) { /* browser mid-shutdown; the checks below handle it */ }
    const idle = Date.now() - st.mtimeMs;
    if (idle < IDLE_REAP_MS) return;
    let pages = 2;
    try {
      pages = (await browser.pages()).length;
    } catch (_e) {
      return bail(0);
    }
    // >1 page = a client is mid-render (its heartbeat only ticks on connect/
    // release); reap anyway once VERY stale — that is a crashed client's leak.
    if (pages <= 1 || idle > HARD_REAP_MS) return bail(0);
  }, POLL_MS);
}

main().catch((e) => {
  process.stderr.write(String((e && e.stack) || e) + '\n');
  process.exit(1);
});
