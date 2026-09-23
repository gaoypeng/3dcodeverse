import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {makeRenderer} from '/__runtime/lib/browser/renderer.js';
import {makePostChain} from '/__runtime/lib/browser/post.js';

const $ = (selector) => document.querySelector(selector);
const manifest = await (await fetch('./manifest.json')).json();
let current, bundle, camera, controls;
let running = false, time = 0, last = 0, bootSerial = 0;

for (const item of manifest.cases) {
  const card = document.createElement('button');
  card.className = 'card';
  const thumb = document.createElement('div');
  thumb.className = 'thumbnail';
  const img = document.createElement('img');
  img.src = `./${item.id}.png`;
  img.alt = item.subtitle;
  const play = document.createElement('span');
  play.className = 'play';
  play.textContent = '▶';
  thumb.append(img, play);
  const caption = document.createElement('div');
  caption.className = 'caption';
  const num = document.createElement('span');
  num.className = 'number';
  num.textContent = item.number;
  const copy = document.createElement('div');
  const title = document.createElement('h3');
  title.textContent = item.title;
  const subtitle = document.createElement('p');
  subtitle.textContent = item.subtitle;
  copy.append(title, subtitle);
  caption.append(num, copy);
  card.append(thumb, caption);
  card.onclick = () => open(item);
  $('#gallery').append(card);
}

function disposeBundle(value) {
  if (!value || value.disposed) return;
  value.disposed = true;
  const errors = [];
  const attempt = operation => {
    try { operation(); } catch (error) { errors.push(error); }
  };
  const result = value.result;
  attempt(() => {
    if (typeof result?.dispose === 'function') {
      // An explicit scene disposer is authoritative about owned resources.
      result.dispose();
    } else if (result?.scene?.isObject3D) {
      const unmanaged = new Set(), protectedResources = new Set(), owners = [];
      const collect = (object, resources) => {
        if (object.geometry) resources.add(object.geometry);
        if (object.isInstancedMesh) resources.add(object);
        for (const material of [].concat(object.material || [],
          object.customDepthMaterial || [], object.customDistanceMaterial || [])) {
          if (material) resources.add(material);
        }
      };
      const visit = object => {
        if (typeof object.userData?.dispose === 'function') {
          // Do not cross an ownership boundary: it may contain borrowed assets
          // or later caller attachments that its disposer deliberately leaves.
          owners.push(() => object.userData.dispose());
          object.traverse(child => collect(child, protectedResources));
        } else {
          collect(object, unmanaged);
          object.children.forEach(visit);
        }
      };
      visit(result.scene);
      owners.forEach(attempt);
      for (const resource of unmanaged) {
        // A resource can also be referenced outside the subtree that owns it.
        // Cached materials and all textures remain borrowed by this fallback.
        if (!protectedResources.has(resource) && !resource.userData?.shared && !resource.userData?.astraShared) {
          attempt(() => resource.dispose());
        }
      }
    }
  });
  // The production post chain has no disposal API. Release its entire context.
  // Each new scene gets a NEW canvas; a lost context cannot safely be reused.
  attempt(() => value.renderer?.dispose());
  attempt(() => value.renderer?.forceContextLoss());
  attempt(() => value.canvas?.remove());
  if (errors.length) console.warn('Scene cleanup failed', new AggregateError(errors, 'Scene cleanup failed'));
}

function release() {
  running = false;
  const previousControls = controls, previousBundle = bundle;
  controls = null;
  bundle = null;
  try { previousControls?.dispose(); }
  catch (error) { console.warn('Camera controls cleanup failed', error); }
  disposeBundle(previousBundle);
}

function open(item) {
  bootSerial++;
  release();
  current = item;
  time = 0;
  $('.intro').hidden = true;
  $('#gallery').hidden = true;
  $('#stage').hidden = false;
  $('#title').textContent = item.title;
  $('#subtitle').textContent = item.subtitle;
  $('#category').textContent = `${item.number} / ${item.kind.toUpperCase()}`;
  $('#still').href = `./${item.id}.png`;
  $('#video').src = `./${item.id}.mp4`;
  $('#video').poster = `./${item.id}.png`;
  watch();
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function watch() {
  bootSerial++; // Cancel a pending asynchronous live scene boot.
  running = false;
  $('#live').hidden = true;
  $('#video').hidden = false;
  $('.live-controls').hidden = true;
  $('#watch').classList.add('active');
  $('#explore').classList.remove('active');
  $('#status').textContent = '';
  $('#video').play().catch(() => {});
}

async function explore() {
  $('#video').pause();
  $('#video').hidden = true;
  $('#live').hidden = false;
  $('.live-controls').hidden = false;
  $('#explore').classList.add('active');
  $('#watch').classList.remove('active');
  $('#pause').textContent = 'Pause';
  if (bundle) { running = true; return; }
  $('#status').textContent = 'Preparing scene…';
  const serial = ++bootSerial;
  const item = current;
  let pending;
  try {
    const mod = await import(item.module);
    if (serial !== bootSerial) return;
    const width = Math.min(1600, Math.round($('#screen').clientWidth));
    const height = Math.round(width * 9 / 16);
    const canvas = document.createElement('canvas');
    canvas.id = 'canvas';
    const renderer = makeRenderer(canvas, width, height);
    pending = {canvas, renderer};
    const manager = new THREE.LoadingManager();
    const loaders = {
      gltf: new GLTFLoader(manager), texture: new THREE.TextureLoader(manager),
      cube: new THREE.CubeTextureLoader(manager), manager,
    };
    pending.result = await mod.createScene({THREE, renderer, loaders});
    if (serial !== bootSerial) { disposeBundle(pending); return; }
    const {scene, cameras, update} = pending.result || {};
    if (!scene?.isScene || !cameras?.length || typeof update !== 'function') {
      throw new Error('Scene does not implement the scene contract');
    }
    pending.post = makePostChain(renderer, scene, {width, height});
    bundle = pending;
    $('#live').replaceChildren(canvas);
    camera = new THREE.PerspectiveCamera(45, width / height, .04, 3000);
    controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    $('#camera').replaceChildren();
    cameras.forEach((spec, index) => {
      const option = document.createElement('option');
      option.value = index;
      option.textContent = spec.name;
      $('#camera').append(option);
    });
    setCamera(0);
    update(time, 0);
    pending.post.render(camera);
    $('#status').textContent = '';
    running = true;
  } catch (error) {
    disposeBundle(pending);
    if (serial !== bootSerial) return;
    release();
    $('#status').textContent = `Scene could not load: ${error.message}`;
    console.error(error);
  }
}

function setCamera(index) {
  const spec = bundle?.result.cameras[index];
  if (!spec) return;
  camera.position.fromArray(spec.position);
  camera.fov = spec.fov ?? 45;
  camera.updateProjectionMatrix();
  controls.target.fromArray(spec.lookAt);
  controls.update();
}

$('#watch').onclick = watch;
$('#explore').onclick = explore;
$('#camera').onchange = event => setCamera(Number(event.target.value));
$('#pause').onclick = () => {
  running = !running;
  $('#pause').textContent = running ? 'Pause' : 'Resume';
};
$('#time').oninput = event => {
  time = Number(event.target.value);
  bundle?.result.update(time, 0);
};
$('#close').onclick = () => {
  bootSerial++;
  release();
  $('#video').pause();
  $('#video').removeAttribute('src');
  $('#stage').hidden = true;
  $('.intro').hidden = false;
  $('#gallery').hidden = false;
};
$('#video').onerror = () => {
  // A late film error must not overwrite a live scene's boot failure.
  if (!$('#video').hidden) $('#status').textContent = 'Choose “Explore live” to view this scene.';
};

function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min((now - last) / 1000, .05);
  last = now;
  if (!bundle || $('#live').hidden) return;
  if (running) {
    time += dt;
    bundle.result.update(time, dt);
  }
  controls.update();
  bundle.post.refreshGrade();
  bundle.post.render(camera);
  $('#time').value = Math.min(time, 20);
  $('#seconds').textContent = `${time.toFixed(2)} s`;
}
requestAnimationFrame(frame);

// Diagnostics used by the browser smoke test.
window.graphicsLab = {
  open, explore,
  get active() { return bundle?.result; },
  get time() { return time; },
};
