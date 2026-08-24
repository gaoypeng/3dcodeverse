"""``3dcv doctor`` — environment checks (deps, Blender, node/three/chrome, keys, CLIs, MCP)."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from codeverse.cli._fmt import console, doctor_table
from codeverse.config import get_settings

doctor_app = typer.Typer(invoke_without_command=True)

Row = tuple[str, str, str]
_PY_DEPS = ("pydantic", "pydantic_settings", "typer", "rich", "jinja2", "yaml", "numpy", "trimesh", "fcl", "PIL", "pyarrow",
            "scipy", "shapely", "networkx", "manifold3d", "playwright", "google.genai", "anthropic", "openai",
            "yourdfpy", "mcp")
_CLI_TIMEOUT = 25


def _ver(cmd: list[str], timeout: int = _CLI_TIMEOUT) -> tuple[bool, str]:
    if not shutil.which(cmd[0]) and not Path(cmd[0]).is_file():
        return False, "not found"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except OSError as e:
        return False, str(e)
    out = (p.stdout or p.stderr).strip().splitlines()
    first = out[0].strip() if out else ""
    return p.returncode == 0, first[:100] or f"exit {p.returncode}"


def check_python_deps() -> list[Row]:
    missing, present = [], []
    for mod in _PY_DEPS:
        try:
            importlib.import_module(mod)
            present.append(mod)
        except Exception:  # ImportError or a broken native lib
            missing.append(mod)
    import sys

    rows: list[Row] = [("python", "OK", sys.version.split()[0] + f" ({sys.executable})")]
    status = "OK" if not missing else ("WARN" if set(missing) <= {"manifold3d", "yourdfpy", "mcp", "shapely"} else "FAIL")
    rows.append(("python deps", status, f"{len(present)}/{len(_PY_DEPS)} importable" + (f"; missing: {', '.join(missing)}" if missing else "")))
    return rows


def check_blender() -> list[Row]:
    s = get_settings()
    b = s.resolve_blender()
    if not b:
        return [("blender", "FAIL", "no binary (set CV3D_BINARIES__BLENDER or put blender-5.0 on PATH)")]
    okk, v = _ver([b, "--version"], timeout=60)
    return [("blender", "OK" if okk else "FAIL", f"{v} @ {b}")]


def check_node() -> list[Row]:
    from codeverse.spatial.node import NODE_MIN_STR, node_version_error, parse_node_version

    s = get_settings()
    rows: list[Row] = []
    okk, v = _ver([s.binaries.node, "--version"])
    too_old = node_version_error(parse_node_version(v)) if okk else ""
    if too_old:
        rows.append(("node", "FAIL", f"{v} — too old, need >= {NODE_MIN_STR} (nvm install --lts)"))
    else:
        rows.append(("node", "OK" if okk else "FAIL", v if okk else f"{v} (need >= {NODE_MIN_STR})"))
    rj = s.runtime_js_dir()
    three = rj / "node_modules" / "three" / "package.json"
    pup = rj / "node_modules" / "puppeteer" / "package.json"
    if three.is_file():
        rows.append(("three", "OK", json.loads(three.read_text()).get("version", "?") + f" ({rj / 'node_modules'})"))
    else:
        rows.append(("three", "FAIL", f"runtime_js/node_modules/three missing — run `npm install` in {rj}"))
    if pup.is_file():
        cache = Path.home() / ".cache" / "puppeteer"
        chrome = any(cache.rglob("chrome")) if cache.is_dir() else False
        rows.append(("puppeteer", "OK" if chrome else "WARN",
                     f"v{json.loads(pup.read_text()).get('version', '?')}; chrome cache {'found' if chrome else 'missing'} ({cache})"))
    else:
        rows.append(("puppeteer", "FAIL", "not installed in runtime_js"))
    return rows


_PROBE_JS = (
    "const {launchBrowser}=require(process.argv[1]);"
    "launchBrowser({gpu:'auto'}).then(async r=>{console.log(JSON.stringify({gpu:r.gpu,renderer:r.renderer}));"
    "await r.browser.close();process.exit(0)}).catch(e=>{console.log(JSON.stringify({error:String(e&&e.message||e)}));process.exit(1)})"
)


def check_gpu_probe(timeout_s: int = 120) -> list[Row]:
    """Launch headless Chrome through runtime_js/gpu_launch.cjs and report the WebGL renderer."""
    from codeverse.proc import run_subprocess

    s = get_settings()
    rj = s.runtime_js_dir()
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
    line = next((ln for ln in reversed(proc.stdout.strip().splitlines()) if ln.startswith("{")), "")
    try:
        res = json.loads(line) if line else {}
    except ValueError:
        res = {}
    if "error" in res or proc.returncode != 0:
        detail = str(res.get("error") or (proc.stderr.strip().splitlines() or ["unknown error"])[-1])
        return [("chrome webgl", "FAIL", detail[:160])]
    renderer = str(res.get("renderer", "?"))
    return [("chrome webgl", "OK" if res.get("gpu") else "WARN",
             f"{'GPU' if res.get('gpu') else 'CPU/software'}: {renderer[:120]}")]


def check_pool(live: bool) -> list[Row]:
    """The key pool's live scheduling picture: quota, headroom, storm counters.

    Only meaningful once something has used the pool this process, so it is part
    of ``--live`` (``check_keys`` makes one call just before)."""
    from codeverse.models.gemini import shared_pool
    from codeverse.models.storm import all_gates

    s = get_settings()
    if not s.gemini_api_keys:
        return []
    r = s.rate
    pool = shared_pool(list(s.gemini_api_keys))
    st = pool.stats()
    rows: list[Row] = [(
        "gemini quota", "OK",
        f"{st['n_keys']} keys x {r.rpm_per_key} RPM / {r.tpm_per_key:,} TPM "
        f"= {st['rpm_capacity']:,} RPM / {st['tpm_capacity']:,} TPM pool-wide"
        f"; max_in_flight={r.max_in_flight or 'off'}",
    )]
    if live:
        used_rpm = 100 * (1 - st["rpm_headroom"])
        used_tpm = "n/a" if st["tpm_headroom"] is None else f"{100 * (1 - st['tpm_headroom']):.1f}%"
        bad = st["429"] + st["5xx"] + st["dead"]
        rows.append((
            "gemini pool", "WARN" if st["n_dead"] else "OK",
            f"in-flight {st['in_flight']} (peak {st['peak_in_flight']}) · "
            f"RPM used {used_rpm:.1f}% · TPM used {used_tpm} · "
            f"{st['acquired']} acquired, {st['ok']} ok, {st['429']} x429, {st['5xx']} x5xx, "
            f"{st['n_dead']} dead, {st['n_cooling']} cooling"
            + ("" if not bad else " (this process)"),
        ))
        for gate in all_gates():
            g = gate.snapshot()
            rows.append((
                f"storm gate {g['name'].split(':')[-1]}"[:24],
                "WARN" if g["storming"] else "OK",
                f"{g['storms']} storm(s), {g['hits']} x503, {g['probes']} probes, "
                f"{g['parked_s']}s parked"
                + (f" — CLOSED for {g['closed_for_s']}s" if g["storming"] else ""),
            ))
    return rows


def _rate_cap() -> int:
    from codeverse.config import get_settings

    return int(getattr(get_settings().rate, "max_in_flight", 0) or 0)


def check_keys(live: bool) -> list[Row]:
    s = get_settings()
    n = len(s.gemini_api_keys)
    rows: list[Row] = [("gemini keys", "OK" if n else "FAIL", f"{n} key(s)")]
    if n:
        from codeverse.models.health import sibling_processes

        sib = sibling_processes()
        cap = _rate_cap()
        rows.append((
            "pool sharing",
            "OK" if sib == 0 else "WARN",
            f"{sib} other harness process(es) running — each keeps its OWN key pool, so the real "
            f"concurrency is ~{(sib + 1) * cap} in-flight against one shared quota "
            f"(docs/COST.md §23; run one battery at a time or set CV3D_MAX_IN_FLIGHT={max(1, cap // (sib + 1))})"
            if sib else "sole harness process: max_in_flight applies as configured",
        ))
    rows.append(("anthropic key", "OK" if s.anthropic_api_key else "WARN", "set" if s.anthropic_api_key else "not set (anthropic:* backends unavailable)"))
    rows.append(("openai key", "OK" if s.openai_api_key else "WARN", "set" if s.openai_api_key else "not set (openai:* backends unavailable)"))
    if live and n:
        try:
            from codeverse.contracts.chat import ChatMessage, ChatRequest
            from codeverse.models import get_chat_model

            m = get_chat_model("gemini:gemini-3.7-flash")
            r = m.generate(ChatRequest(messages=[ChatMessage.user("Reply with the single word: pong")],
                                       # 256, not 16: gemini-3.x spends output tokens on thoughts even
                                       # with thinking="off", and a 16-token budget comes back empty
                                       temperature=0.0, max_output_tokens=256, thinking="off", label="doctor"))
            rows.append(("gemini live call", "OK" if "pong" in r.text.lower() else "WARN",
                         f"{r.text.strip()[:40]!r} cost=${r.usage.cost_usd:.5f}"))
        except Exception as e:
            rows.append(("gemini live call", "FAIL", f"{type(e).__name__}: {str(e)[:120]}"))
    return rows


def check_clis() -> list[Row]:
    s = get_settings()
    rows: list[Row] = []
    for name, binary in (("gemini-cli", s.binaries.gemini_cli), ("claude", s.binaries.claude_cli),
                         ("codex", s.binaries.codex_cli), ("agy", s.binaries.agy_cli)):
        okk, v = _ver([binary, "--version"])
        rows.append((name, "OK" if okk else "WARN", v if okk else f"{v} (backend {name} unavailable)"))
    okk, v = _ver(["git", "--version"])
    rows.append(("git", "OK" if okk else "FAIL", v))
    okk, v = _ver([s.binaries.ffmpeg, "-version"])
    rows.append(("ffmpeg", "OK" if okk else "WARN", v if okk else "not found (turntable mp4 disabled)"))
    return rows


def check_mcp() -> list[Row]:
    try:
        importlib.import_module("mcp")
    except ImportError:
        return [("mcp", "WARN", "python package `mcp` missing (MCP server for CLIs unavailable)")]
    try:
        importlib.import_module("codeverse.spatial.mcp_server")
        return [("mcp", "OK", "mcp + codeverse.spatial.mcp_server importable")]
    except ImportError as e:
        return [("mcp", "WARN", f"codeverse.spatial.mcp_server not importable ({e})")]


def run_doctor(*, live: bool = False, gpu: bool = True) -> list[Row]:
    rows: list[Row] = []
    rows += check_python_deps()
    rows += check_blender()
    rows += check_node()
    if gpu:
        rows += check_gpu_probe()
    rows += check_keys(live)
    rows += check_pool(live)
    rows += check_clis()
    rows += check_mcp()
    return rows


@doctor_app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
    live: Annotated[bool, typer.Option("--live", help="make one tiny Gemini call")] = False,
    gpu: Annotated[bool, typer.Option("--gpu/--no-gpu", help="probe headless Chrome WebGL")] = True,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check python deps, Blender, node/three/puppeteer, keys, CLIs and MCP."""
    if ctx.invoked_subcommand:
        return
    rows = run_doctor(live=live, gpu=gpu)
    if as_json:
        console.print_json(json.dumps([{"check": c, "status": s, "detail": d} for c, s, d in rows]))
    else:
        console.print(doctor_table(rows))
    if any(s == "FAIL" for _, s, _ in rows):
        raise typer.Exit(code=1)
