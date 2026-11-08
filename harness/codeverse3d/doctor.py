"""Environment checks behind ``3dcode doctor`` / ``python -m codeverse3d.doctor``: python deps, Blender (+ Cycles),
node / three / puppeteer, the headless-Chrome GPU probe.  No keys, no model calls.
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
import sys
from pathlib import Path

from codeverse3d.config import BLENDER_MIN, get_settings
from codeverse3d.proc import read_json_or_none, run_subprocess, version_line

Row = tuple[str, str, str]
_PY_DEPS = ("pydantic", "pydantic_settings", "yaml", "numpy", "trimesh", "fcl", "PIL",
            "scipy", "shapely", "networkx", "matplotlib", "yourdfpy", "moderngl", "cadquery")
#: modules from an OPTIONAL extra: missing is a WARN naming the extra, not a broken install.
#: Every module of every extra must be listed here or the base install misreports itself.
_OPTIONAL_DEPS = {"shapely": "mesh", "networkx": "mesh", "matplotlib": "mesh",
                  "yourdfpy": "urdf", "scipy": "urdf", "moderngl": "graphics", "cadquery": "cad"}


def _ver(cmd: list[str]) -> tuple[bool, str]:
    if not shutil.which(cmd[0]) and not Path(cmd[0]).is_file():
        return False, "not found"
    rc, first = version_line(cmd)
    if rc is None:
        return False, first
    return rc == 0, first[:100] or f"exit {rc}"


def check_python_deps() -> list[Row]:
    missing, present = [], []
    for mod in _PY_DEPS:
        try:
            importlib.import_module(mod)
            present.append(mod)
        except Exception:  # ImportError or a broken native lib
            missing.append(mod)
    rows: list[Row] = [("python", "OK", sys.version.split()[0] + f" ({sys.executable})")]
    status = "OK" if not missing else ("WARN" if set(missing) <= set(_OPTIONAL_DEPS) else "FAIL")
    detail = f"{len(present)}/{len(_PY_DEPS)} importable" + (f"; missing: {', '.join(missing)}" if missing else "")
    extras = sorted({_OPTIONAL_DEPS[m] for m in missing if m in _OPTIONAL_DEPS})
    if extras:  # name the fix, not just the symptom
        detail += f"; fix: pip install -e 'harness[{','.join(extras)}]'"
    rows.append(("python deps", status, detail))
    return rows


def check_blender() -> list[Row]:
    s = get_settings()
    b = s.resolve_blender()
    if not b:
        return [("blender", "FAIL", f"C3D_BINARIES__BLENDER={s.binaries.blender} is not an executable"
                 if s.binaries.blender else "no binary (set C3D_BINARIES__BLENDER or put blender-5.2 on PATH)")]
    okk, v = _ver([b, "--version"])
    m = re.search(r"Blender (\d+)\.(\d+)", v)
    if okk and m and (int(m[1]), int(m[2])) < BLENDER_MIN:
        return [("blender", "FAIL", f"{v} @ {b} — too old, need >= {'.'.join(map(str, BLENDER_MIN))}")]
    return [("blender", "OK" if okk else "FAIL", f"{v} @ {b}")]


#: one headless Blender run: the Cycles CUDA device list and a timed Cycles frame.  OptiX is never
#: probed (it crashes Blender under WSL2).
_BLENDER_RENDER_PROBE = """
import json, time
import bpy
out = {}
p = bpy.context.preferences.addons["cycles"].preferences
try:
    p.compute_device_type = "CUDA"
    p.refresh_devices()
    out["cuda"] = [d.name for d in p.devices if d.type == "CUDA"]
except Exception as e:
    out["cuda"], out["cuda_error"] = [], str(e)[:200]
if out["cuda"]:
    try:
        for d in p.devices:
            d.use = d.type == "CUDA"
        sc = bpy.context.scene
        sc.render.engine = "CYCLES"
        sc.cycles.device = "GPU"
        sc.cycles.samples = 32
        sc.cycles.use_adaptive_sampling = True
        sc.cycles.use_denoising = True
        sc.cycles.denoiser = "OPENIMAGEDENOISE"
        sc.cycles.denoising_use_gpu = True
        sc.render.use_persistent_data = True
        sc.render.resolution_x, sc.render.resolution_y = 256, 144
        t = time.perf_counter()
        bpy.ops.render.render()
        out["cycles_ms"] = int((time.perf_counter() - t) * 1000)
    except Exception as e:
        out["cycles_error"] = str(e)[:200]
print("C3D_DOCTOR " + json.dumps(out))
"""


def check_blender_render(*, timeout_s: int = 120) -> list[Row]:
    """What scene_blender's judged frames render on: Cycles CUDA / CPU, GPU slots."""
    from codeverse3d.languages.blender import blender_env
    from codeverse3d.spatial.render_blender import gpu_slots

    s = get_settings()
    b = s.resolve_blender()
    if not b:
        return [("blender render", "SKIP", "no Blender binary")]
    cmd = [b, "-b", "--factory-startup", "--python-expr", _BLENDER_RENDER_PROBE, "--"]
    try:
        proc = run_subprocess(cmd, cwd=Path.cwd(), env=blender_env(), timeout_s=timeout_s)
    except OSError as e:
        return [("blender render", "FAIL", f"could not run {b}: {e}")]
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("C3D_DOCTOR ")), "")
    if proc.timed_out or not line:
        why = f"timed out after {timeout_s}s" if proc.timed_out else f"exit {proc.returncode}, no probe line"
        return [("blender render", "FAIL", why)]
    res = json.loads(line.removeprefix("C3D_DOCTOR "))
    r = s.render
    cuda = res.get("cuda") or []
    timed = (f", 32 spp 256x144 frame {res['cycles_ms']} ms (GPU OIDN + persistent data)" if res.get("cycles_ms") is not None
             else f", timed frame failed: {res['cycles_error']}" if res.get("cycles_error") else "")
    rows: list[Row] = [(
        "blender cycles", "OK" if cuda and not res.get("cycles_error") else "WARN",
        (f"CUDA: {', '.join(cuda)}{timed}" if cuda else f"no CUDA device{' (' + res['cuda_error'] + ')' if res.get('cuda_error') else ''}"
         " — scene_blender frames render on the CPU (~4x slower)")
        + f"; judged frames: {r.blender_samples} spp, device {r.blender_device}, "
          f"frame cap {r.blender_frame_time_limit_s:g}s")]
    slots = gpu_slots(s)
    busy = slots.busy()
    rows.append(("blender gpu slots", "WARN" if busy >= slots.n else "OK",
                 f"{busy}/{slots.n} busy machine-wide ({slots.root}); a render waits {r.blender_gpu_wait_s:g}s, then the CPU"))
    return rows


def check_node() -> list[Row]:
    from codeverse3d.spatial.node import NODE_MIN_STR, node_version_error, parse_node_version

    s = get_settings()
    rows: list[Row] = []
    okk, v = _ver([s.binaries.node, "--version"])
    too_old = node_version_error(parse_node_version(v)) if okk else ""
    if too_old:
        rows.append(("node", "FAIL", f"{v} — too old, need >= {NODE_MIN_STR} (nvm install --lts)"))
    else:
        rows.append(("node", "OK" if okk else "FAIL", v if okk else f"{v} (need >= {NODE_MIN_STR})"))
    try:
        rj = s.runtime_js_dir()
    except RuntimeError as e:   # non-editable install without C3D_RUNTIME_JS
        rows.append(("runtime_js", "FAIL", str(e)))
        return rows
    three = read_json_or_none(rj / "node_modules" / "three" / "package.json")
    pup = read_json_or_none(rj / "node_modules" / "puppeteer" / "package.json")
    if three is not None:
        rows.append(("three", "OK", f"{three.get('version', '?')} ({rj / 'node_modules'})"))
    else:
        rows.append(("three", "FAIL", f"runtime_js/node_modules/three missing or unreadable — run `npm install` in {rj}"))
    if pup is not None:
        from codeverse3d.spatial.node import puppeteer_cache_dir

        cache = puppeteer_cache_dir()
        chrome = any(cache.rglob("chrome")) if cache.is_dir() else False
        rows.append(("puppeteer", "OK" if chrome else "WARN",
                     f"v{pup.get('version', '?')}; chrome cache {'found' if chrome else 'missing'} ({cache})"))
    else:
        rows.append(("puppeteer", "FAIL", "not installed in runtime_js (or its package.json is unreadable)"))
    return rows


#: ``release()``, never ``browser.close()``: the handle may be the daemon's shared browser, which
#: close() would take down under concurrent runs.
_PROBE_JS = (
    "const {launchBrowser}=require(process.argv[1]);"
    "launchBrowser({gpu:'auto'}).then(async r=>{console.log(JSON.stringify({gpu:r.gpu,renderer:r.renderer}));"
    "await r.release();process.exit(0)}).catch(e=>{console.log(JSON.stringify({error:String(e&&e.message||e)}));process.exit(1)})"
)


def check_gpu_probe(timeout_s: int = 120) -> list[Row]:
    """Launch headless Chrome through runtime_js/gpu_launch.cjs and report the WebGL renderer."""
    from codeverse3d.spatial.node import parse_last_json

    s = get_settings()
    try:
        rj = s.runtime_js_dir()
    except RuntimeError as e:
        return [("chrome webgl", "FAIL", str(e))]
    launcher = rj / "gpu_launch.cjs"
    if not launcher.is_file():
        return [("chrome webgl", "SKIP", f"{launcher} missing (spatial render package not installed yet)")]
    cmd = [s.binaries.node, "-e", _PROBE_JS, str(launcher)]
    try:
        proc = run_subprocess(cmd, cwd=rj, timeout_s=timeout_s)
    except OSError as e:
        return [("chrome webgl", "FAIL", str(e))]
    if proc.timed_out:
        return [("chrome webgl", "FAIL", f"probe timed out after {timeout_s}s")]
    res = parse_last_json(proc.stdout) or {}
    if "error" in res or proc.returncode != 0:
        detail = str(res.get("error") or (proc.stderr.strip().splitlines() or ["unknown error"])[-1])
        return [("chrome webgl", "FAIL", detail[:160])]
    renderer = str(res.get("renderer", "?"))
    return [("chrome webgl", "OK" if res.get("gpu") else "WARN",
             f"{'GPU' if res.get('gpu') else 'CPU/software'}: {renderer[:120]}")]


def run_doctor(*, gpu: bool = True) -> list[Row]:
    rows: list[Row] = []
    rows += check_python_deps()
    rows += check_blender()
    rows += check_blender_render()
    rows += check_node()
    if gpu:
        rows += check_gpu_probe()
    okk, v = _ver(["git", "--version"])
    rows.append(("git", "OK" if okk else "WARN", v))
    return rows


def format_rows(rows: list[Row]) -> str:
    width = max(len(c) for c, _, _ in rows)
    return "\n".join(f"{c.ljust(width)}  {s:<4}  {d}" for c, s, d in rows)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m codeverse3d.doctor", description=__doc__)
    ap.add_argument("--gpu", action=argparse.BooleanOptionalAction, default=True, help="probe headless Chrome WebGL")
    ap.add_argument("--json", dest="as_json", action="store_true")
    ns = ap.parse_args(argv)
    rows = run_doctor(gpu=ns.gpu)
    print(json.dumps([{"check": c, "status": s, "detail": d} for c, s, d in rows], indent=1) if ns.as_json else format_rows(rows))
    return 1 if any(s == "FAIL" for _, s, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
