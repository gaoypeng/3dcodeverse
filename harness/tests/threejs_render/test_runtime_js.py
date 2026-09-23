"""Direct tests of the node scripts: export CLI, serve.cjs, gpu_launch.cjs (need node)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse3d.spatial.node import NodeError, run_node, runtime_js_dir
from codeverse3d.workspace import Workspace

pytestmark = pytest.mark.node


def test_export_cli_writes_census_and_error_json(stool_ws: Workspace, tmp_path: Path):
    rt = runtime_js_dir()
    res = run_node(rt / "export_glb.mjs", ["--ws", str(stool_ws.root), "--out", "artifacts/o.glb", "--census", "artifacts/c.json"], three_hook=True, timeout_s=60)
    rec = res.last_json
    assert rec["ok"] and (stool_ws.root / "artifacts/o.glb").is_file()
    census = json.loads((stool_ws.root / "artifacts/c.json").read_text())
    assert set(census) == {"placement_offset", "instanced_meshes_baked", "selfcheck_ran", "tick_present",
                           "unnamed_meshes", "warnings", "glb_bytes", "three_revision"}
    assert census["three_revision"] == "182"
    # error path: unknown entry
    with pytest.raises(NodeError) as ei:
        run_node(rt / "export_glb.mjs", ["--ws", str(stool_ws.root), "--entry", "src/nope.js"], three_hook=True, timeout_s=60)
    err = ei.value.result.last_json["error"]
    assert err["type"] == "MissingEntryFile"
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
    malformed: await get('/100%.png'), after: await get('/a.glb'),   // decodeURIComponent throws on '%.p'
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
    assert out["malformed"]["status"] == 404 and out["after"]["status"] == 200   # the server survived it
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
    env = {"C3D_CACHE_DIR": str(tmp_path / "cache")}
    auto = run_node(script, [], timeout_s=120, env_extra=env).last_json
    assert auto["renderer"] and auto["probe"]
    if auto["gpu"]:
        assert "swiftshader" not in auto["renderer"].lower()
    off = run_node(script, [], timeout_s=120, env_extra={**env, "T_GPU": "off"}).last_json
    assert off["gpu"] is False and "swiftshader" in off["probe"].lower()
    with pytest.raises(NodeError):
        run_node(script, [], timeout_s=60, env_extra={**env, "T_GPU": "bogus"})
    _reap_daemons(tmp_path / "cache")


def test_two_runtime_trees_never_share_one_browser_endpoint(tmp_path: Path):
    """The browser endpoint name carries the runtime_js tree, so two trees share one cache dir safely."""
    cache = tmp_path / "cache"
    paths = []
    for tree in ("worktree", "checkout"):
        d = tmp_path / tree
        d.mkdir()
        shutil.copy(runtime_js_dir() / "gpu_launch.cjs", d / "gpu_launch.cjs")
        script = tmp_path / f"{tree}.cjs"
        script.write_text(f"""
const {{ _internal }} = require({json.dumps(str(d / 'gpu_launch.cjs'))});
console.log(JSON.stringify({{
  endpoint: _internal.endpointPath('cpu'),
  lock: _internal.spawnLockPath('cpu'),
  failed: _internal.daemonFailPath('cpu'),
}}));
""")
        paths.append(run_node(script, [], timeout_s=60, env_extra={"C3D_CACHE_DIR": str(cache)}).last_json)

    a, b = paths
    for key in ("endpoint", "lock", "failed"):
        assert a[key] != b[key], f"both trees would advertise into the same {key}"
        # the separation must be the NAME: one cache dir is still one cache dir
        assert Path(a[key]).parent == Path(b[key]) .parent == cache
    # the reaper and the daemon-failure skip both key off these shapes
    assert Path(a["endpoint"]).name.startswith("browser_cpu_")
    assert a["failed"].endswith(".failed.json")


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
    """A second launch reconnects to the shared browser; C3D_BROWSER_REUSE=off owns every launch."""
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
    env = {"C3D_CACHE_DIR": str(tmp_path / "cache")}
    out = run_node(script, [], timeout_s=120, env_extra=env).last_json
    assert out["a_shared"] is True and out["b_shared"] is True and out["alive"]
    assert out["reconnect_ms"] < 1000  # connect, not a fresh ~550ms+ launch
    # the endpoint is keyed by the runtime_js that spawned the daemon, so a worktree
    # and the main checkout sharing one cache dir cannot advertise over each other
    endpoints = list((tmp_path / "cache").glob("browser_cpu_*.json"))
    assert len(endpoints) == 1 and not endpoints[0].name.endswith(".failed.json")
    off = run_node(script, [], timeout_s=120, env_extra={**env, "C3D_BROWSER_REUSE": "off"}).last_json
    assert off["a_shared"] is False and off["b_shared"] is False
    _reap_daemons(tmp_path / "cache")


def test_serve_refuses_symlinks_that_escape_the_root(tmp_path: Path):
    """Workspace content is model-authored: a symlink out of the root 404s."""
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
