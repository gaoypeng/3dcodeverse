"""Optional GLB viewer, served from the **vendored** three.js in ``runtime_js/``.

No CDN: the page declares an import map that points ``three`` and
``three/addons/`` at ``/vendor/...``, which the server maps onto
``runtime_js/node_modules/three/``.  When that package is not installed the
viewer is simply not offered and the GLB link stays a plain download.
"""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.addons.gallery.model import RunEntry
from codeverse3d.addons.gallery.theme import esc, footer, page_shell, top_bar
from codeverse3d.addons.gallery.urls import PathError, UrlMaker, safe_join
from codeverse3d.config import get_settings

#: URL prefix → path under node_modules/three
VENDOR_MAP = {"three/build/": "build/", "three/examples/jsm/": "examples/jsm/"}

VIEWER_CSS = """
#stage{width:100%;height:min(78vh,900px);background:var(--sunken);border:1px solid var(--line);
  border-radius:var(--r-3);overflow:hidden;position:relative}
#stage canvas{display:block;width:100%;height:100%}
.touch-only{display:none}
@media (pointer:coarse){.mouse-only{display:none}.touch-only{display:inline}}
#hint{position:absolute;left:10px;bottom:10px;font-size:var(--fs-xs);color:var(--fg-3);
  background:color-mix(in srgb,var(--surface) 80%,transparent);padding:3px 8px;border-radius:999px}
#vmsg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  color:var(--fg-2);font-size:var(--fs-sm);padding:var(--s-4);text-align:center}
"""

VIEWER_JS = r"""
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const stage = document.getElementById('stage');
const msg = document.getElementById('vmsg');
const src = stage.dataset.src;
const dark = getComputedStyle(document.body).getPropertyValue('--sunken').trim() || '#eee';

const renderer = new THREE.WebGLRenderer({antialias:true, alpha:true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(stage.clientWidth, stage.clientHeight);
stage.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, stage.clientWidth/stage.clientHeight, 0.01, 500);
camera.position.set(1.6, 1.2, 2.0);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 2.2));
const key = new THREE.DirectionalLight(0xffffff, 2.0);
key.position.set(2.5, 4, 3);
scene.add(key);

new GLTFLoader().load(src, (gltf) => {
  msg.remove();
  scene.add(gltf.scene);
  const box = new THREE.Box3().setFromObject(gltf.scene);
  const size = box.getSize(new THREE.Vector3());
  const centre = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() * 0.5, 0.05);
  const grid = new THREE.GridHelper(Math.max(2, radius * 4), 20, 0x888888, 0xcccccc);
  grid.material.opacity = 0.35; grid.material.transparent = true;
  grid.position.y = box.min.y;
  scene.add(grid);
  controls.target.copy(centre);
  camera.near = radius / 100; camera.far = radius * 100;
  camera.position.copy(centre).add(new THREE.Vector3(radius*1.6, radius*1.1, radius*2.0));
  camera.updateProjectionMatrix();
  controls.update();
  document.getElementById('dims').textContent =
    size.x.toFixed(3)+' × '+size.y.toFixed(3)+' × '+size.z.toFixed(3)+' m';
}, undefined, (err) => { msg.textContent = 'could not load this GLB: ' + err; });

addEventListener('resize', () => {
  renderer.setSize(stage.clientWidth, stage.clientHeight);
  camera.aspect = stage.clientWidth/stage.clientHeight;
  camera.updateProjectionMatrix();
});
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
"""


def three_root(runtime_js: Path | str | None = None) -> Path | None:
    """``runtime_js/node_modules/three`` when the vendored copy is installed."""
    if runtime_js is None:
        runtime_js = get_settings().runtime_js_dir()
    p = Path(runtime_js) / "node_modules" / "three"
    return p if (p / "build" / "three.module.js").is_file() else None


def viewer_available(runtime_js: Path | str | None = None) -> bool:
    return three_root(runtime_js) is not None


def vendor_path(rel: str, runtime_js: Path | str | None = None) -> Path | None:
    """Map a ``/vendor/<rel>`` URL onto the vendored three.js tree (or ``None``).

    Only the two whitelisted subtrees are reachable, and :func:`safe_join` still
    checks the result — a vendor URL can never leave ``node_modules/three``."""
    root = three_root(runtime_js)
    if root is None:
        return None
    for prefix, sub in VENDOR_MAP.items():
        if rel.startswith(prefix):
            try:
                target = safe_join(root, sub + rel[len(prefix):])
            except PathError:
                return None
            return target if target.is_file() else None
    return None


def render_viewer(entry: RunEntry, urls: UrlMaker, rel: str) -> str:
    """The GLB viewer page for one artifact of a run."""
    import_map = json.dumps({"imports": {"three": "/vendor/three/build/three.module.js",
                                         "three/addons/": "/vendor/three/examples/jsm/"}})
    src = urls.file(entry, rel)
    body = (
        top_bar("3dcode gallery", entry.slug,
                crumbs=f"<a href='/'>gallery</a> <span class='faint'>/</span> "
                       f"<a href='{esc(urls.detail(entry))}'>{esc(entry.slug)}</a> "
                       f"<span class='faint'>/</span> <b>{esc(rel)}</b>",
                right=f"<a class='btn' href='{esc(urls.detail(entry))}'>← back to the run</a>")
        + "<main class='wrap'>"
        + f"<div id='stage' data-src='{esc(src)}'><div id='vmsg'>loading {esc(rel)}…</div>"
          f"<div id='hint'><span class='mouse-only'>drag to orbit · scroll to zoom · right-drag to pan"
          f"</span><span class='touch-only'>drag to orbit · pinch to zoom · two-finger drag to pan"
          f"</span></div></div>"
        + f"<p class='small muted' style='margin-top:var(--s-3)'>bounding box: <span id='dims' class='num'>—</span>"
          f" · <a href='{esc(src)}'>download the raw GLB</a>"
          f" · <a href='{esc(urls.detail(entry))}'>back to the run</a></p>"
        + "</main>" + footer("three.js served from runtime_js/node_modules — no network required")
    )
    return page_shell(f"{rel} — {entry.slug}", body, extra_css=VIEWER_CSS,
                      head_extra=f"<script type='importmap'>{import_map}</script>\n",
                      modules=VIEWER_JS)
