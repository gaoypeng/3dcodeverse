"""Environment checks behind ``3dcv doctor`` — python deps, Blender, node/three/
puppeteer, the GPU probe, keys, the pool admission numbers, the vendor CLIs, MCP
and the skill library wiring.  Moved out of ``cli/`` 2026-08-28: only the typer
shim is a CLI concern (tests/install imports these checks as documentation facts).
"""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

from codeverse.config import get_settings
from codeverse.proc import version_line

Row = tuple[str, str, str]
_PY_DEPS = ("pydantic", "pydantic_settings", "typer", "rich", "jinja2", "yaml", "numpy", "trimesh", "fcl", "PIL", "pyarrow",
            "scipy", "shapely", "networkx", "manifold3d", "matplotlib", "google.genai", "anthropic", "openai",
            "yourdfpy", "mcp", "moderngl", "cadquery")
#: modules that come from an OPTIONAL extra: missing means one track is unavailable, not a
#: broken install, so they are a WARN naming the extra to install.  `moderngl` and `cadquery`
#: were absent from the list entirely until 2026-08-24, so an environment installed without
#: [graphics] passed doctor "21/21 importable" and then failed in the graphics track with
#: "no usable OpenGL context: No module named 'moderngl'" — a missing pip package reported
#: as a GPU/driver problem (docs/INSTALL.md §4 promises every extra is listed here).
#: EVERY module of every extra must be here, or the base install misreports itself: the
#: map was missing scipy/networkx/pyarrow, so a fresh clone installed exactly as
#: docs/INSTALL.md §1 documents (no extras) got `python deps FAIL 14/23` — "your install
#: is broken" for an install that is correct — and a remedy that named four of the five
#: extras and could not clear the row.  Verified on a 3.13 clean clone, 2026-08-24.
_OPTIONAL_DEPS = {"manifold3d": "mesh", "shapely": "mesh", "networkx": "mesh", "matplotlib": "mesh",
                  "yourdfpy": "urdf", "scipy": "urdf", "mcp": "mcp",
                  "moderngl": "graphics", "cadquery": "cad", "pyarrow": "flywheel"}
_CLI_TIMEOUT = 25


def _ver(cmd: list[str], timeout: int = _CLI_TIMEOUT) -> tuple[bool, str]:
    if not shutil.which(cmd[0]) and not Path(cmd[0]).is_file():
        return False, "not found"
    rc, first = version_line(cmd, timeout=timeout)
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
    import sys

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
    try:
        rj = s.runtime_js_dir()
    except RuntimeError as e:   # non-editable install without CV3D_RUNTIME_JS
        rows.append(("runtime_js", "FAIL", str(e)))
        return rows
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
    from codeverse.models.retry import all_gates

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
        from codeverse.models.health import pool_budget

        pb = pool_budget()
        cap = _rate_cap()
        rows.append((
            "pool sharing",
            "OK" if pb.fits(cap) else "WARN",
            (f"{pb}; this process at {cap} {'fits' if pb.fits(cap) else 'does NOT fit'} "
             f"(docs/COST.md §23: keep the SUM at or under {pb.knee}"
             + ("" if pb.fits(cap) else f" — set CV3D_MAX_IN_FLIGHT={max(1, pb.headroom)} or wait") + ")")
            if pb.siblings else "sole harness process: max_in_flight applies as configured",
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


def check_skills() -> list[Row]:
    """The static half of the live CLI smoke: is the library there, valid, and reachable?

    A CLI upgrade that moves its skills root ships an empty index and the run still
    passes — the failure is invisible except as a read rate of zero a battery later.  So
    the wiring gets a check you can run before spending money."""
    from codeverse.agents.backends import ALLOWED_TOOLS
    from codeverse.skills import bundle_dirs, skills_dir, validate_bundle
    from codeverse.skills.config import skills_enabled
    from codeverse.skills.materialize import SKILL_ROOTS
    from codeverse.skills.registry import ROUTED_SKILLS

    rows: list[Row] = [("skills switch", "OK" if skills_enabled() else "WARN",
                        "CV3D_SKILLS=on" if skills_enabled() else "CV3D_SKILLS is off (default): no skill is attached")]
    dirs = bundle_dirs()
    if not dirs:
        rows.append(("skills library", "WARN", f"no bundles under {skills_dir()}"))
    else:
        bad = {d.name: validate_bundle(d) for d in dirs}
        bad = {k: v for k, v in bad.items() if v}
        rows.append(("skills library", "FAIL" if bad else "OK",
                     f"{len(dirs)} bundles, {len(bad)} invalid" + (f": {', '.join(bad)}" if bad else "")))
    missing = [n for n in ROUTED_SKILLS if n not in {d.name for d in dirs}]
    if missing:
        rows.append(("skills routing", "WARN", f"{len(missing)} routed skill(s) have no bundle: {', '.join(missing[:4])}…"))
    rows.append(("skills discovery", "OK", "materialised into " + " + ".join(SKILL_ROOTS)))
    rows.append(("claude-code Skill tool", "OK" if "Skill" in ALLOWED_TOOLS else "FAIL",
                 "in --allowedTools" if "Skill" in ALLOWED_TOOLS else "missing: claude-code would deny skill activation"))
    return rows


def run_doctor(*, live: bool = False, gpu: bool = True, skills: bool = False) -> list[Row]:
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
    if skills:
        rows += check_skills()
    return rows
