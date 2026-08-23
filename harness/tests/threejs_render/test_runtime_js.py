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
