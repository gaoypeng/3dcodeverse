#!/usr/bin/env node
/** Exercise the staged gallery through its real browser controls. */
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {launchBrowser} from '../../runtime_js/lib/host_env.mjs';
import {releaseBrowser} from '../../runtime_js/lib/host_page.mjs';
const require = createRequire(import.meta.url);
const {serveDirs} = require('../../runtime_js/serve.cjs');
const here = path.dirname(fileURLToPath(import.meta.url));
const {values} = parseArgs({options: {out: {type: 'string', default: path.join(here, 'output')}}});
const root = path.resolve(values.out);
// Include comments and documentation, not just currently visible UI text.
const sourceExtensions = new Set(['.js', '.mjs', '.py', '.html', '.css', '.md', '.glsl', '.vert', '.frag']);
let englishFilesChecked = 0;
function checkEnglish(directory) {
  for (const entry of fs.readdirSync(directory, {withFileTypes: true})) {
    if (['.git', 'node_modules', '__pycache__'].includes(entry.name)) continue;
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) checkEnglish(file);
    else if (entry.isFile() && sourceExtensions.has(path.extname(entry.name))) {
      assert(!/\p{Script=Han}/u.test(fs.readFileSync(file, 'utf8')), `Non-English example: ${file}`);
      englishFilesChecked++;
    }
  }
}
checkEnglish(here);
if (!root.startsWith(here + path.sep)) checkEnglish(root);
const server = await serveDirs({root, routes: {
  '/': {body: fs.readFileSync(path.join(root, 'index.html'), 'utf8')},
  '/favicon.ico': {body: ''},
}});
const launched = await launchBrowser({gpu: 'auto'});
let page;
const errors = [], results = [];
try {
  page = await launched.browser.newPage();
  await page.setViewport({width: 1440, height: 1000, deviceScaleFactor: 1});
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('response', response => {
    if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  await page.goto(server.base, {waitUntil: 'networkidle0'});
  await page.waitForFunction(() => !!window.graphicsLab);
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json')));
  assert.equal(await page.$$eval('.card', cards => cards.length), manifest.cases.length);
  assert.equal(await page.evaluate(() => document.documentElement.lang), 'en');
  await page.screenshot({path: path.join(root, 'gallery.png'), fullPage: true});

  for (let index = 0; index < manifest.cases.length; index++) {
    const item = manifest.cases[index];
    console.log(`Checking ${item.id}`);
    await page.click(`.card:nth-child(${index + 1})`);
    const videoExists = fs.existsSync(path.join(root, `${item.id}.mp4`));
    if (videoExists) {
      await page.waitForFunction(() => document.querySelector('#video').readyState >= 2);
      const video = await page.evaluate(async () => {
        const element = document.querySelector('#video');
        element.muted = true;
        await element.play();
        return {width: element.videoWidth, height: element.videoHeight, duration: element.duration};
      });
      assert(video.width > 0 && video.height > 0 && video.duration > 0);
    }
    await page.click('#explore');
    await page.waitForFunction(() => !!window.graphicsLab.active && !document.querySelector('#status').textContent,
      {timeout: 60000});
    await page.waitForFunction(() => window.graphicsLab.time > .1);
    await page.click('#pause');
    const paused = await page.evaluate(() => window.graphicsLab.time);
    // Two rendered browser frames must leave simulation time unchanged while paused.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await page.evaluate(() => window.graphicsLab.time), paused);
    await page.$eval('#time', slider => {
      slider.value = '2.5';
      slider.dispatchEvent(new Event('input', {bubbles: true}));
    });
    assert.equal(await page.evaluate(() => window.graphicsLab.time), 2.5);
    const cameras = await page.$$eval('#camera option', options => options.map(option => option.value));
    for (const camera of cameras) {
      await page.select('#camera', camera);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    }
    await page.select('#camera', '0');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const screen = await page.$('#canvas');
    await screen.screenshot({path: path.join(root, `${item.id}.live.png`)});
    const canvasOk = await page.$eval('#canvas', canvas => {
      const gl = canvas.getContext('webgl2');
      return !!gl && !gl.isContextLost() && canvas.width > 0 && canvas.height > 0;
    });
    assert(canvasOk, `${item.id}: live WebGL context lost`);
    await page.click('#watch');
    await page.click('#explore');
    await page.waitForFunction(() => window.graphicsLab.time > 2.5);
    await page.click('#close');
    assert.equal(await page.evaluate(() => !!window.graphicsLab.active), false);
    results.push({case: item.id, cameras: cameras.length, video: videoExists, controls: 'pass'});
  }
  // Cancelling a boot must not resurrect the old scene after returning to the gallery.
  await page.evaluate(async () => {
    document.querySelector('.card').click();
    const loading = window.graphicsLab.explore();
    document.querySelector('#close').click();
    await loading;
  });
  assert.equal(await page.evaluate(() => !!window.graphicsLab.active), false);
  assert.equal(await page.evaluate(() => /\p{Script=Han}/u.test(document.body.innerText)), false);
  let comparisons = 0;
  if (fs.existsSync(path.join(root, 'comparison.html'))) {
    await page.goto(`${server.base}/comparison.html`, {waitUntil: 'networkidle0'});
    await page.waitForFunction(() => [...document.images].every(image => image.complete && image.naturalWidth > 0));
    const comparisonManifest = path.join(root, 'comparison', 'manifest.json');
    assert(fs.existsSync(comparisonManifest), 'Comparison page has no artifact manifest');
    const records = JSON.parse(fs.readFileSync(comparisonManifest, 'utf8'));
    assert(Array.isArray(records) && records.length > 0, 'Comparison manifest has no pairs');
    const actualPairs = await page.$$eval('.compare', elements => elements.map(element =>
      [...element.querySelectorAll('img')].map(image => image.getAttribute('src'))));
    comparisons = actualPairs.length;
    assert.equal(comparisons, records.length, 'Comparison page and artifact manifest disagree');
    for (let index = 0; index < records.length; index++) {
      const expected = ['before', 'after'].map(variant => records[index].variants?.[variant]?.image);
      assert(expected.every(image => typeof image === 'string'), 'Comparison pair has missing images');
      assert.deepEqual(actualPairs[index], expected, `Wrong comparison images for ${records[index].case}`);
      for (const relative of expected) {
        const image = path.resolve(root, relative);
        assert(image.startsWith(root + path.sep) && fs.existsSync(image), `Missing comparison artifact: ${relative}`);
      }
    }
    await page.$$eval('input[type="range"]', inputs => inputs.forEach(input => {
      input.value = '27'; input.dispatchEvent(new Event('input', {bubbles: true}));
    }));
    assert(await page.$$eval('.compare', elements => elements.every(element => element.style.getPropertyValue('--split') === '27%')));
    assert.equal(await page.evaluate(() => document.documentElement.lang), 'en');
    await page.screenshot({path: path.join(root, 'comparison-page.png'), fullPage: true});
  }
  assert.deepEqual(errors, []);
  const report = {ok: true, gpu: launched.gpu, renderer: launched.renderer,
    englishFilesChecked, cases: results, comparisons, errors};
  fs.writeFileSync(path.join(root, 'browser-check.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await page?.close();
  await releaseBrowser(launched);
  await server.close();
}
