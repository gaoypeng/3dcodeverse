"""Direct tests of the node scripts: export CLI, serve.cjs, gpu_launch.cjs (need node)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.spatial.node import run_node, runtime_js_dir
from codeverse3d.workspace import Workspace

pytestmark = pytest.mark.node


def test_serve_and_importmap(tmp_path: Path):
    """serve.cjs types, routes and escapes; workspace content is model-authored, so a
    symlink out of the root 404s."""
    script = tmp_path / "s.cjs"
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.glb").write_bytes(b"glTF1234")
    (root / "ok.txt").write_text("fine")
    (tmp_path / "secret.txt").write_text("s3cr3t")
    (root / "inside.txt").symlink_to(root / "ok.txt")
    (root / "leak.txt").symlink_to(tmp_path / "secret.txt")
    (root / "dirlink").symlink_to(tmp_path)
    script.write_text(f"""
const {{ serveDirs, importMapHtml }} = require({json.dumps(str(runtime_js_dir() / 'serve.cjs'))});
(async () => {{
  const srv = await serveDirs({{ root: {json.dumps(str(root))}, routes: {{}} }});
  const get = async (p) => {{ const r = await fetch(srv.url(p)); return {{ status: r.status, type: r.headers.get('content-type'), len: (await r.arrayBuffer()).byteLength }}; }};
  const out = {{
    glb: await get('/a.glb'), three: await get('/__runtime/node_modules/three/build/three.module.js'),
    escape: await get('/../s.cjs'), missing: await get('/nope.png'),
    malformed: await get('/100%.png'), after: await get('/a.glb'),   // decodeURIComponent throws on '%.p'
    importmap: importMapHtml(),
    links: {{ ok: (await get('/ok.txt')).status, inside: (await get('/inside.txt')).status,
             leak: (await get('/leak.txt')).status, dirleak: (await get('/dirlink/secret.txt')).status }},
  }};
  await srv.close();
  console.log(JSON.stringify(out));
}})().catch((e) => {{ console.error(e); process.exit(1); }});
""")
    out = run_node(script, [], timeout_s=60).last_json
    assert out["glb"] == {"status": 200, "type": "model/gltf-binary", "len": 8}
    assert out["three"]["status"] == 200 and out["three"]["type"] == "text/javascript"
    assert out["escape"]["status"] == 404 and out["missing"]["status"] == 404
    assert out["malformed"]["status"] == 404 and out["after"]["status"] == 200   # the server survived it
    im = json.loads(out["importmap"].split(">", 1)[1].rsplit("<", 1)[0])["imports"]
    assert im["three"].startswith("/__runtime/") and "http" not in json.dumps(im)
    assert out["links"] == {"ok": 200, "inside": 200, "leak": 404, "dirleak": 404}


def _reap_daemons(cache_dir: Path) -> None:
    """Kill any shared browser advertised under an ephemeral test cache dir."""
    import contextlib
    import os
    import signal

    for ep in cache_dir.glob("browser_*.json"):
        for key in ("pid", "daemon_pid"):
            pid = json.loads(ep.read_text()).get(key) if not ep.name.endswith(".failed.json") else None
            if pid:
                with contextlib.suppress(OSError):
                    os.kill(int(pid), signal.SIGTERM)


def test_gpu_launch_probe_and_browser_reuse(tmp_path: Path):
    """The GPU/SwiftShader probe; a second launch reconnects to the shared browser;
    C3D_BROWSER_REUSE=off owns every launch."""
    script = tmp_path / "g.cjs"
    script.write_text(f"""
const {{ launchBrowser, rendererInfo }} = require({json.dumps(str(runtime_js_dir() / 'gpu_launch.cjs'))});
(async () => {{
  const gpu = process.env.T_GPU || 'auto';
  const a = await launchBrowser({{ gpu }});
  const probe = await rendererInfo(a.browser);
  const p = await a.browser.newPage();
  await p.close();
  await a.release();   // never browser.close(): the browser may be shared
  const t0 = Date.now();
  const b = await launchBrowser({{ gpu }});
  const reconnect_ms = Date.now() - t0;
  const alive = b.browser.connected !== false;
  await b.release();
  console.log(JSON.stringify({{ gpu: a.gpu, renderer: a.renderer, probe, a_shared: a.shared, b_shared: b.shared,
                                reconnect_ms, alive }}));
}})().catch((e) => {{ console.error(e); process.exit(1); }});
""")
    env = {"C3D_CACHE_DIR": str(tmp_path / "cache")}
    auto = run_node(script, [], timeout_s=120, env_extra=env).last_json
    assert auto["renderer"] and auto["probe"]
    if auto["gpu"]:
        assert "swiftshader" not in auto["renderer"].lower()
    off = run_node(script, [], timeout_s=120, env_extra={**env, "T_GPU": "off"}).last_json
    assert off["gpu"] is False and "swiftshader" in off["probe"].lower()
    assert off["a_shared"] is True and off["b_shared"] is True and off["alive"]
    assert off["reconnect_ms"] < 1000  # connect, not a fresh ~550ms+ launch
    own = run_node(script, [], timeout_s=120, env_extra={**env, "T_GPU": "off", "C3D_BROWSER_REUSE": "off"}).last_json
    assert own["a_shared"] is False and own["b_shared"] is False
    _reap_daemons(tmp_path / "cache")


def test_scene_server_only_mounts_src_public_assets(tmp_path: Path):
    """Scene code must not read the run's spec, state, git or judge output."""
    ws = Workspace(tmp_path / "run").create()
    (ws.src / "scene.js").write_text("export function createScene() {}\n")
    (ws.public / "assets").mkdir(parents=True, exist_ok=True)
    (ws.public / "assets" / "a.glb").write_bytes(b"glTF1234")
    (ws.public / "page.txt").write_text("public")
    for name in ("spec.json", "run_state.json", "events.jsonl"):
        (ws.root / name).write_text("{}\n")
    (ws.artifacts / "judge").mkdir(parents=True, exist_ok=True)
    (ws.artifacts / "judge" / "r00.json").write_text('{"score": 1}')
    serve = ('/src/scene.js', '/public/page.txt', '/assets/a.glb', '/__host.html')
    denied = ('/spec.json', '/run_state.json', '/events.jsonl', '/.git/config', '/artifacts/judge/r00.json')
    script = tmp_path / "s.mjs"
    script.write_text(f"""
import {{ serveWorkspace }} from {json.dumps(str(runtime_js_dir() / 'lib' / 'host_env.mjs'))};
const srv = await serveWorkspace({json.dumps(str(ws.root))}, {{ hostHtml: '<html>host</html>' }});
const out = {{}};
for (const p of {json.dumps([*serve, *denied])}) out[p] = (await fetch(srv.base + p)).status;
await srv.close();
console.log(JSON.stringify(out));
""")
    out = run_node(script, [], timeout_s=60).last_json
    assert [out[p] for p in serve] == [200] * len(serve), out
    assert [out[p] for p in denied] == [404] * len(denied), out
