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
const { _internal } = require('./gpu_launch.cjs');

const IDLE_REAP_MS = 90 * 1000;
const HARD_REAP_MS = 10 * 60 * 1000;
const POLL_MS = 15 * 1000;
// A page older than this belongs to nobody: every harness render/probe finishes or
// times out well under it (ceilings <= 330 s), and a SIGKILLed client never closes
// its page.  Leaked pages hold WebGL contexts that eventually wedge the browser.
const PAGE_TTL_MS = Number(process.env.C3D_PAGE_TTL_MS || 8 * 60 * 1000);

function arg(name, dflt) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}

async function main() {
  const backend = arg('backend', 'cpu');
  if (!['gpu', 'cpu'].includes(backend)) throw new Error(`--backend must be gpu|cpu, got ${backend}`);
  const puppeteer = _internal.loadPuppeteer();
  const epPath = _internal.endpointPath(backend);
  const lockPath = _internal.spawnLockPath(backend);   // one spelling, in gpu_launch.cjs
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

  // One CDP probe, bounded: a wedged browser answers pages() only after
  // protocolTimeout (15 min), which must never stall the reaper or the exit path.
  const boundedPages = () => Promise.race([
    browser.pages(),
    new Promise((_res, rej) => { const t = setTimeout(() => rej(new Error('pages timeout')), 5000); if (t.unref) t.unref(); }),
  ]);

  const bail = async (code, { pages = null, advertise = true } = {}) => {
    // Never close a browser somebody is using: pages beyond the initial about:blank
    // mean live work, and closing it kills their page mid-call.  Re-advertise instead
    // (never when a NEWER daemon owns the endpoint — its advertisement must not be
    // clobbered); the hard reap in the poll loop is the backstop for a client that
    // never came back.
    try {
      const n = pages !== null ? pages : (await boundedPages()).length;
      if (n > 1) {
        if (advertise) fs.writeFileSync(epPath, JSON.stringify(record, null, 2));
        return;
      }
    } catch (_e) { /* browser gone or wedged: fall through and exit */ }
    const cur = _internal.readJson(epPath);
    if (cur && cur.ws === record.ws) fs.rmSync(epPath, { force: true });
    try { await Promise.race([browser.close(), new Promise((res) => setTimeout(res, 5000))]); } catch (_e) { /* already dead */ }
    process.exit(code);
  };

  browser.on('disconnected', () => {
    // our own connection died with the browser process
    try { const cur = _internal.readJson(epPath); if (cur && cur.ws === record.ws) fs.rmSync(epPath, { force: true }); } catch (_e) { /* ignore */ }
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
    const cur = _internal.readJson(epPath);
    if (!cur || cur.ws !== record.ws) return bail(0, { advertise: false });   // superseded: never clobber the newer daemon
    const idle = Date.now() - st.mtimeMs;
    // Reap leaked pages only when NOBODY is working: clients tick the heartbeat every
    // 30 s while connected, so a fresh one means an old page belongs to live work.
    if (idle < IDLE_REAP_MS) return;
    let pages = 2;
    try {
      const open = await boundedPages();   // ONE probe per tick (it used to be three)
      const now = Date.now();
      const live = new Set(open);
      for (const p of open.slice(1)) if (!firstSeen.has(p)) firstSeen.set(p, now);
      for (const [p, at] of firstSeen) {
        if (!live.has(p)) firstSeen.delete(p);
        else if (now - at > PAGE_TTL_MS) { firstSeen.delete(p); live.delete(p); await p.close().catch(() => {}); }
      }
      pages = live.size;
    } catch (_e) {
      return bail(0, { pages: 0 });   // wedged or mid-shutdown: nothing left to protect
    }
    // Very stale + still >1 page = a crashed client's leak: FORCE the exit instead of
    // re-advertising (the old disjunct re-wrote the endpoint, reset its mtime, and so
    // could never fire — the hard reap was unreachable).
    if (idle > HARD_REAP_MS) return bail(0, { pages: 1 });
    if (pages <= 1) return bail(0, { pages });
  }, POLL_MS);
}

main().catch((e) => {
  process.stderr.write(String((e && e.stack) || e) + '\n');
  process.exit(1);
});
