"""Direct tests of the node scripts: export CLI, serve.cjs, gpu_launch.cjs (need node)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.spatial.node import NodeError, run_node, runtime_js_dir
from codeverse.workspace import Workspace

pytestmark = pytest.mark.node


def test_export_cli_writes_census_and_error_json(stool_ws: Workspace, tmp_path: Path):
    rt = runtime_js_dir()
    res = run_node(rt / "export_glb.mjs", ["--ws", str(stool_ws.root), "--out", "artifacts/o.glb", "--census", "artifacts/c.json"], three_hook=True, timeout_s=60)
    rec = res.last_json
    assert rec["ok"] and rec["parts"] == 3 and (stool_ws.root / "artifacts/o.glb").is_file()
    census = json.loads((stool_ws.root / "artifacts/c.json").read_text())
    assert set(census) >= {"object_name", "parts", "tri_count", "bbox", "materials", "normalised_offset", "warnings", "tick_present"}
    assert census["three_revision"] == "182"
    # --normalise 1 (dataset canonicalisation only; never passed by ThreeJsRuntime) translates the group
    (stool_ws.src / "object.js").write_text((stool_ws.src / "object.js").read_text().replace("return root;", "root.position.set(0.5, 0.2, 0); return root;"))
    res = run_node(rt / "export_glb.mjs", ["--ws", str(stool_ws.root), "--out", "artifacts/n.glb", "--census", "artifacts/n.json", "--normalise", "1"], three_hook=True, timeout_s=60)
    census = json.loads((stool_ws.root / "artifacts/n.json").read_text())
    assert census["normalised_offset"] == census["placement_offset"] and abs(census["placement_offset"][0] + 0.5) < 1e-4
    assert abs(census["bbox"]["min"][1]) < 1e-3 and any("--normalise 1" in w for w in census["warnings"])
    # error path: unknown entry
    with pytest.raises(NodeError) as ei:
        run_node(rt / "export_glb.mjs", ["--ws", str(stool_ws.root), "--entry", "src/nope.js"], three_hook=True, timeout_s=60)
    err = ei.value.result.last_json["error"]
    assert err["type"] == "MissingEntry"
    assert (stool_ws.artifacts / "export_error.json").is_file()


def test_render_glb_driver_protocol_and_shared_plumbing(tmp_path: Path):
    """render_glb.mjs is a thin entry: shared arg parsing / JSON-last-line
    protocol (lib/cli.mjs), shared browser + static server (lib/host_env.mjs),
    shared release dance (lib/host_page.mjs) — and it still reports a failure as
    exit 1 with `{ok: false, error}` as the LAST stdout line."""
    rt = runtime_js_dir()
    src = (rt / "render_glb.mjs").read_text()
    assert "from './lib/cli.mjs'" in src and "from './lib/host_env.mjs'" in src
    assert "releaseBrowser" in src
    assert "createRequire" not in src and "parseArgs" not in src   # no second copy of either
    with pytest.raises(NodeError) as ei:
        run_node(rt / "render_glb.mjs",
                 ["--glb", str(tmp_path / "missing.glb"), "--out", str(tmp_path / "out"),
                  "--views", json.dumps([{"name": "front", "azimuth": 0.0, "elevation": 8.0}])],
                 timeout_s=60)
    rec = ei.value.result.last_json
    assert ei.value.result.rc == 1 and rec["ok"] is False and "GLB not found" in rec["error"]
    with pytest.raises(NodeError) as ei:
        run_node(rt / "render_glb.mjs", ["--glb", "x.glb", "--out", "o", "--views", "[]"], timeout_s=60)
    assert "non-empty JSON list" in ei.value.result.last_json["error"]


def test_one_webgl_renderer_factory():
    """Object rig and scene host share `lib/browser/renderer.js`: one place sets
    the colour pipeline (sRGB + ACES + PCF shadows, pixel ratio 1), so an object
    render and a scene render cannot drift apart."""
    rt = runtime_js_dir()
    sources = sorted(rt.glob("*.mjs")) + sorted(rt.glob("*.cjs")) + sorted((rt / "lib").rglob("*.mjs")) \
        + sorted((rt / "lib").rglob("*.js"))
    owners = [p for p in sources if "new THREE.WebGLRenderer(" in p.read_text()]
    assert [p.name for p in owners] == ["renderer.js"], owners
    assert "from './renderer.js'" in (rt / "lib/browser/studio.js").read_text()
    assert "from './browser/renderer.js'" in (rt / "lib/scene_host.mjs").read_text()


def test_serve_and_importmap(tmp_path: Path):
    script = tmp_path / "s.cjs"
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.glb").write_bytes(b"glTF1234")
    (root / "sub").mkdir()
    (root / "sub" / "b.json").write_text("{}")
    script.write_text(f"""
const {{ serveDirs, importMapHtml }} = require({json.dumps(str(runtime_js_dir() / 'serve.cjs'))});
(async () => {{
  const srv = await serveDirs({{ root: {json.dumps(str(root))}, routes: {{ '/__p.html': {{ body: '<html>hi</html>' }} }} }});
  const get = async (p) => {{ const r = await fetch(srv.url(p)); return {{ status: r.status, type: r.headers.get('content-type'), len: (await r.arrayBuffer()).byteLength }}; }};
  const out = {{
    glb: await get('/a.glb'), json: await get('/sub/b.json'), page: await get('/__p.html'),
    three: await get('/__runtime/node_modules/three/build/three.module.js'),
    escape: await get('/../s.cjs'), missing: await get('/nope.png'), fav: await get('/favicon.ico'),
    importmap: importMapHtml(),
  }};
  await srv.close();
  console.log(JSON.stringify(out));
}})().catch((e) => {{ console.error(e); process.exit(1); }});
""")
    out = run_node(script, [], timeout_s=60).last_json
    assert out["glb"] == {"status": 200, "type": "model/gltf-binary", "len": 8}
    assert out["json"]["type"] == "application/json"
    assert out["page"]["status"] == 200 and "text/html" in out["page"]["type"]
    assert out["three"]["status"] == 200 and out["three"]["len"] > 100000 and out["three"]["type"] == "text/javascript"
    assert out["escape"]["status"] == 404 and out["missing"]["status"] == 404 and out["fav"]["status"] == 204
    im = json.loads(out["importmap"].split(">", 1)[1].rsplit("<", 1)[0])["imports"]
    assert im["three"].startswith("/__runtime/") and im["three/addons/"].endswith("/examples/jsm/")
    assert "http" not in json.dumps(im)


def test_gpu_launch_probe(tmp_path: Path):
    script = tmp_path / "g.cjs"
    script.write_text(f"""
const {{ launchBrowser, rendererInfo }} = require({json.dumps(str(runtime_js_dir() / 'gpu_launch.cjs'))});
(async () => {{
  const t0 = Date.now();
  const handle = await launchBrowser({{ gpu: process.env.T_GPU || 'auto' }});
  const {{ browser, gpu, renderer }} = handle;
  const probe = await rendererInfo(browser);
  await handle.release();   // never browser.close(): the browser may be shared
  console.log(JSON.stringify({{ gpu, renderer, probe, shared: handle.shared, ms: Date.now() - t0 }}));
}})().catch((e) => {{ console.error(e); process.exit(1); }});
""")
    env = {"CV3D_CACHE_DIR": str(tmp_path / "cache")}
    auto = run_node(script, [], timeout_s=120, env_extra=env).last_json
    assert auto["renderer"] and auto["probe"]
    if auto["gpu"]:
        assert "swiftshader" not in auto["renderer"].lower()
    off = run_node(script, [], timeout_s=120, env_extra={**env, "T_GPU": "off"}).last_json
    assert off["gpu"] is False and "swiftshader" in off["probe"].lower()
    with pytest.raises(NodeError):
        run_node(script, [], timeout_s=60, env_extra={**env, "T_GPU": "bogus"})
    _reap_daemons(tmp_path / "cache")


def _reap_daemons(cache_dir: Path) -> None:
    """Kill any shared browser advertised under an ephemeral test cache dir."""
    import contextlib
    import os
    import signal

    for ep in cache_dir.glob("browser_*.json"):
        if ep.name.endswith(".failed.json"):
            continue
        info = json.loads(ep.read_text())
        for key in ("pid", "daemon_pid"):
            if info.get(key):
                with contextlib.suppress(OSError):
                    os.kill(int(info[key]), signal.SIGTERM)


def test_gpu_launch_browser_reuse(tmp_path: Path):
    """F21 phase 2: second launchBrowser connects to the daemon's shared browser
    (~ms, shared=true); release() disconnects and the browser survives; with
    CV3D_BROWSER_REUSE=off every launch is owned."""
    script = tmp_path / "r.cjs"
    script.write_text(f"""
const {{ launchBrowser }} = require({json.dumps(str(runtime_js_dir() / 'gpu_launch.cjs'))});
(async () => {{
  const a = await launchBrowser({{ gpu: 'off' }});
  const p = await a.browser.newPage();
  await p.close();
  await a.release();
  const t0 = Date.now();
  const b = await launchBrowser({{ gpu: 'off' }});
  const reconnect_ms = Date.now() - t0;
  const alive = b.browser.connected !== false;
  await b.release();
  console.log(JSON.stringify({{ a_shared: a.shared, b_shared: b.shared, reconnect_ms, alive }}));
}})().catch((e) => {{ console.error(e); process.exit(1); }});
""")
    env = {"CV3D_CACHE_DIR": str(tmp_path / "cache")}
    out = run_node(script, [], timeout_s=120, env_extra=env).last_json
    assert out["a_shared"] is True and out["b_shared"] is True and out["alive"]
    assert out["reconnect_ms"] < 1000  # connect, not a fresh ~550ms+ launch
    assert (tmp_path / "cache" / "browser_cpu.json").is_file()
    off = run_node(script, [], timeout_s=120, env_extra={**env, "CV3D_BROWSER_REUSE": "off"}).last_json
    assert off["a_shared"] is False and off["b_shared"] is False
    _reap_daemons(tmp_path / "cache")


def test_serve_refuses_symlinks_that_escape_the_root(tmp_path: Path):
    """resolveInside is lexical + realpath: a symlink inside the workspace pointing
    outside must 404 (workspace content is model-authored); in-root symlinks and
    plain files keep serving."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.txt").write_text("fine")
    (tmp_path / "secret.txt").write_text("s3cr3t")
    (root / "inside.txt").symlink_to(root / "ok.txt")
    (root / "leak.txt").symlink_to(tmp_path / "secret.txt")
    (root / "dirlink").symlink_to(tmp_path)
    script = tmp_path / "s.cjs"
    script.write_text(f"""
const {{ serveDirs }} = require({json.dumps(str(runtime_js_dir() / 'serve.cjs'))});
(async () => {{
  const srv = await serveDirs({{ root: {json.dumps(str(root))} }});
  const get = async (p) => (await fetch(srv.url(p))).status;
  const out = {{ ok: await get('/ok.txt'), inside: await get('/inside.txt'),
    leak: await get('/leak.txt'), dirleak: await get('/dirlink/secret.txt') }};
  await srv.close();
  console.log(JSON.stringify(out));
}})().catch((e) => {{ console.error(e); process.exit(1); }});
""")
    out = run_node(script, [], timeout_s=60).last_json
    assert out == {"ok": 200, "inside": 200, "leak": 404, "dirleak": 404}


def test_cli_safe_name_rejects_path_tricks(tmp_path: Path):
    """lib/cli.mjs safeName guards every filename composed from a camera/view name."""
    script = tmp_path / "n.mjs"
    cli = (runtime_js_dir() / "lib" / "cli.mjs").as_uri()
    script.write_text(f"""
const {{ safeName }} = await import({json.dumps(cli)});
const rejects = (v) => {{ try {{ safeName(v); return false; }} catch {{ return true; }} }};
const bad = ['x/../y', '/abs', 'a\\\\b', 'a b', 'a.png', '', 'x'.repeat(200)];
console.log(JSON.stringify({{ ok: true, good: safeName('cam_a-1'), all_rejected: bad.every(rejects) }}));
""")
    out = run_node(script, [], timeout_s=30).last_json
    assert out == {"ok": True, "good": "cam_a-1", "all_rejected": True}
