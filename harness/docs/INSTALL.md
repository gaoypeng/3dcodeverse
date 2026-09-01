# Installing the 3dcodeverse harness

Everything below was executed on this machine (WSL2 Ubuntu, RTX 5090, Python
3.13.9, node v24.14.0, Blender 5.0.1) on 2026-08-23; the version numbers and the
`3dcodeverse doctor` output in §9 are real, not illustrative.  Paths are relative
to the harness root `/home/yipeng/3dcodeverse/harness` unless absolute.

Once installed, read `docs/RUNBOOK.md` (how to run), `docs/ARCHITECTURE.md`
(what a run does) and `CLAUDE.md` (working rules).

1. [TL;DR](#1-tldr) · 2. [Prerequisites](#2-prerequisites)
(incl. [supported versions](#21-supported-versions)) ·
3. [Get the code](#3-get-the-code) · 4. [Python package + extras](#4-python-package--extras) ·
5. [runtime_js — the node side](#5-runtime_js--the-node-side) · 6. [Blender](#6-blender) ·
7. [GPU / headless rendering](#7-gpu--headless-rendering) ·
8. [Keys, CLIs and settings](#8-keys-clis-and-settings) ·
9. [Verify — `3dcodeverse doctor`](#9-verify--3dcodeverse-doctor) ·
10. [5-minute smoke](#10-5-minute-smoke) · 11. [Uninstall / cleanup](#11-uninstall--cleanup) ·
12. [Appendix: verified versions](#12-appendix-verified-versions-on-this-box)

---

## 1. TL;DR

```bash
git clone <repo> 3dcodeverse && cd 3dcodeverse
python3 -m venv .venv && source .venv/bin/activate    # see §3 — a distro python3 is
                                                      # PEP 668 and pip will refuse it
bash harness/setup.sh            # python + node deps + chrome + doctor
```

`setup.sh` is idempotent — it checks the interpreter versions, runs
`pip install -e 'harness[all,dev]'`, runs `npm ci` in `runtime_js/` **only when the
lockfile actually moved**, makes sure puppeteer's Chrome is downloaded, and
finishes by printing `3dcodeverse doctor`.  Useful flags:
`--extras cad,urdf`, `--python /path/to/python`, `--no-node`, `--no-chrome`,
`--no-doctor`, `--no-gpu`, `--force-npm`, `--help`.

The manual equivalent:

```bash
pip install -e 'harness[all,dev]'
cd harness/runtime_js && npm ci && npx puppeteer browsers install chrome && cd ..
3dcodeverse doctor
```

> **Running installs on a busy box.**  `npm ci` deletes `node_modules/` before
> re-creating it, and `pip install -e` rewrites the entry points.  If benches or
> renders are running against this checkout, use `setup.sh` (which skips `npm ci`
> when the lockfile is unchanged) or wait for them to finish.

---

## 2. Prerequisites

### 2.1 Supported versions

One fixed Python version — **3.13** — by the owner's decision (2026-08-26): no floor, no
matrix, no compatibility shims, and — since the same day — no CI: the owner removed the
workflow, so the offline suite and `ruff` are run locally before every push (see CLAUDE.md).

| component | version | how it is enforced | notes |
|---|---|---|---|
| **python** | **3.13** | `requires-python = ">=3.13"`; ruff `target-version = "py313"`; `PY_FLOOR` in `tests/core/test_portability.py`; `MIN_PY_MINOR` in `setup.sh` | the four places are pinned together by the portability test |
| **node** | **20.6.0+** (developed on 24 LTS) | `runtime_js/package.json` `engines.node`; `codeverse.spatial.node.NODE_MIN` fails every node workload with an actionable message | 20.6 is the `--import` module-hook floor |
| **Blender** | 4.2+ (developed on 5.0.1) | runtime probe only (`Settings.resolve_blender()`) | `blender` / `urdf_blender` are the only users |
| **OS** | Linux x86_64 (WSL2 Ubuntu here) | — | macOS should work (nothing is Linux-specific except `resource.setrlimit` guards) but is not tested |

The offline suite on this box, 2026-08-29: **1928 passed of the 1928 selected**
(25 deselected are `live`).

Moving to another Python later is the same four-line change (`requires-python`, ruff
`target-version`, `PY_FLOOR`, `MIN_PY_MINOR`).

### 2.2 What you need installed

| what | required? | verified here | how it degrades without it |
|---|---|---|---|
| **Python 3.13** | yes | 3.13.9 (`/home/yipeng/miniconda3/bin/python`) | nothing runs; `requires-python = ">=3.13"` (§2.1) |
| **pip + venv** | yes | pip 25.3, `python -m venv` | `setup.sh` cannot install the package.  A stock Debian/Ubuntu `/usr/bin/python3` ships **without** pip and is PEP 668 `EXTERNALLY-MANAGED`, so `pip install -e harness` refuses even once pip is present: `sudo apt install python3-venv python3-pip`, then use a virtualenv |
| **git** | yes | 2.53.0 | run workspaces are git repos (one commit per round); `Workspace.create()` and the flywheel trajectory/pair miners fail |
| **node ≥ 20.6** | yes, except for the `graphics` track | v24.14.0 (npm 11.9.0) | the `threejs` / `scene_threejs` languages disappear **and no object renders happen at all**: `spatial/render.py` renders *every* GLB (Blender-built and CadQuery-built included) with three.js in headless Chrome. Only `graphics` (moderngl) is node-free |
| **Blender 4.2+ / 5.x** | optional | 5.0.1 (`~/.local/bin/blender-5.0`) | `blender` and `urdf_blender` languages unavailable → the `static_object` default language and the whole `articulated_object` track cannot build (`BlenderNotFoundError`); scenes lose planner-chosen bpy GLB assets |
| **EGL-capable GPU stack** | optional | ANGLE / D3D12 / RTX 5090 Laptop | headless Chrome falls back to **SwiftShader** and moderngl to **llvmpipe** — everything still renders, just several times slower; no correctness change |
| C toolchain | usually no | — | only if pip has to build a wheel from source (`python-fcl`, `manifold3d` ship wheels for cp310–cp313 x86_64) |

Disk: ~100 MB for `runtime_js/node_modules`, ~640 MB for puppeteer's Chrome,
~1 GB for a Blender tarball, plus whatever `runs/` and `~/.cache/codeverse` grow
to (619 MB here after a week of benches).

---

## 3. Get the code

```bash
git clone <repo> 3dcodeverse
cd 3dcodeverse
```

Layout: the harness is the `harness/` subdirectory (python dist `3dcodeverse`,
import package `codeverse`, CLIs `3dcodeverse` and `3dcv`).  See the repo
`README.md` for the other top-level components.

Use a virtualenv (or a conda env — this box installs into a conda base env).  It is
only optional when your interpreter already owns its site-packages: a distro
`/usr/bin/python3` is PEP 668 `EXTERNALLY-MANAGED` and pip will refuse to install into
it.  `setup.sh` checks for pip and for that marker up front and tells you which
of the two you hit.

```bash
python -m venv .venv && source .venv/bin/activate     # or: conda create -n cv3d python=3.13
```

---

## 4. Python package + extras

```bash
pip install -e harness            # core only
pip install -e 'harness[all,dev]' # everything (what setup.sh does)
```

`-e` (editable) is the intended mode: the harness is developed in place and the
console scripts (`3dcodeverse`, `3dcv`) point back at the checkout.

Core dependencies (always installed): pydantic + pydantic-settings (contracts,
settings), typer + rich (CLI), jinja2 (prompt templates), pyyaml, numpy, trimesh
(mesh measurement), python-fcl (collision), pillow, google-genai, anthropic,
openai.

| extra | pulls | unlocks |
|---|---|---|
| `cad` | cadquery | the `cadquery` language (`--language cadquery`) |
| `urdf` | yourdfpy, scipy | the `articulated_object` track: URDF parse/validate + joint math |
| `graphics` | moderngl | the `graphics` track (`glsl_shader`, `opengl_python`) — headless GL rendering in `spatial/gl_render.py` |
| `mesh` | shapely, networkx, manifold3d, matplotlib | `cross_section` filled-area/hollow ratio (shapely), mesh split / connectivity (networkx), joint-sweep boolean intersections (`trimesh.boolean(engine="manifold")`), judge cross-section slices on gate-ERROR rounds (`judge_slices`, D48; without the extra the judge logs a warning and sends the pre-D48 payload) |
| `mcp` | mcp | `3dcv mcp` / `codeverse.spatial.mcp_server`, i.e. spatial tools for the `gemini-cli`, `claude-code` and `codex` backends |
| `flywheel` | pyarrow | `3dcv flywheel export --pack` (parquet shards). A plain `flywheel export` needs nothing: without pyarrow it writes `metadata.jsonl` and skips `metadata.parquet` with a warning. |
| `all` | all of the above | — |
| `dev` | pytest, pytest-timeout, ruff | the test suite and the linter |

`graphics`, `mesh`, `mcp` and `flywheel` are lazily imported: without them the
harness installs and starts fine and only the named feature raises `ImportError`
at the moment you use it.  `3dcodeverse doctor` lists every one of these modules
under `python deps` and only tolerates the optional-extra modules — `manifold3d`,
`shapely` (mesh), `yourdfpy` (urdf), `mcp`, `moderngl` (graphics), `cadquery` (cad) —
as WARN, naming the extra to install; everything else missing is a FAIL.

Run the offline test suite to confirm the install:

```bash
cd harness
python -m pytest tests -q                                               # needs node + Blender for the full set (~32 s)
python -m pytest tests -q -m "not live and not blender and not node"    # pure-python subset (~27 s)
# Parallel by default (pytest-xdist, -n auto --dist worksteal).  Serial: add -n0.
python -m pytest tests -q -m live                                       # OPT-IN: real API calls, needs keys
```

Live tests are deselected by default (`addopts = ["-m", "not live"]` in `pyproject.toml`),
so a bare `pytest` never spends money and never hangs waiting on a provider outage.  A
command-line `-m` **replaces** that default rather than adding to it, so any subset you
select must spell `not live` itself — as the line above does.

---

## 5. runtime_js — the node side

**Short answer to "is `runtime_js/node_modules` basically an installation of
node.js?"** — no.  Node.js itself is a *system prerequisite* you install once
(§2).  `runtime_js/` is a small **npm package owned by the harness**, and
`node_modules/` is that package's dependency tree, downloaded by npm into the
checkout.  It is not optional decoration: apart from the `graphics` track, every
render in the harness goes through it (`spatial/render.py` → `render_glb.mjs` →
three.js in headless Chrome), whichever language produced the GLB.

```
harness/runtime_js/
  package.json        committed  — 3 direct deps
  package-lock.json   committed  — exact tree (100 packages), the reproducibility unit
  node_modules/       NOT committed — .gitignore'd, ~97 MB, created by `npm ci`
  *.mjs *.cjs lib/    the harness's own node code (see runtime_js/README.md)
```

Direct dependencies and what they are for:

| package | version here | used for |
|---|---|---|
| `three` | 0.182.0 | GLB export (`export_glb.mjs`), the render rig, the scene host, GLSL preflight |
| `puppeteer` | 24.43.1 | headless Chrome for WebGL rendering (`gpu_launch.cjs`, `browser_daemon.cjs`) |
| `three-mesh-bvh` | 0.9.14 | accelerated raycasts in scene probes |

### Install / refresh

```bash
cd harness/runtime_js
npm ci                                   # exact lockfile install; ~2 s, 100 packages, 95–97 MB
npx puppeteer browsers install chrome    # only if the Chrome cache is missing (see below)
```

Use **`npm ci`**, not `npm install`: `ci` installs exactly what
`package-lock.json` pins and never rewrites it, which is what keeps renders
reproducible.  `npm ci` **deletes `node_modules/` first**, so do not run it while
a bench or render is using this checkout.

You need to re-run it when:

* `node_modules/` does not exist (fresh clone — it is gitignored, so cloning
  never brings it), or
* you pulled a change to `runtime_js/package.json` / `package-lock.json`, or
* `3dcodeverse doctor` reports `three` or `puppeteer` FAIL.

`setup.sh` automates exactly that test: it compares the mtime of
`package-lock.json` against `node_modules/.package-lock.json` and skips the
reinstall when nothing moved (`--force-npm` overrides).

### Chrome

puppeteer downloads its own Chrome build (it does **not** use a system Chrome)
into `~/.cache/puppeteer` — here `chrome/linux-148.0.7778.97` and
`chrome-headless-shell/linux-148.0.7778.97`, 636 MB together.  npm's
`postinstall` normally does this for you; run the explicit command when the
cache was cleared or when `PUPPETEER_SKIP_DOWNLOAD` was set:

```bash
cd harness/runtime_js && npx puppeteer browsers install chrome
# chrome@148.0.7778.97 /home/yipeng/.cache/puppeteer/chrome/linux-148.0.7778.97/chrome-linux64/chrome
```

Re-running it is a no-op that just prints the cached path.  `PUPPETEER_CACHE_DIR`
moves the cache elsewhere (keep it consistent between install and run).

### Why generated code never sees this directory

Agent-written Three.js code is **raw** — it does `import * as THREE from 'three'`
and nothing else; it never imports a harness module and never gets a
`node_modules/` of its own inside the run workspace.  The harness resolves that
bare specifier for it in two places:

* **node**: `node --import runtime_js/lib/resolve_three.mjs …` installs a
  `registerHooks` resolver that redirects `three`, `three/addons/*` and
  `three-mesh-bvh` into `runtime_js/node_modules`, for a module living anywhere
  on disk (`NODE_PATH` cannot do this — it is CommonJS-only).  Python side:
  `spatial.node.run_node(..., three_hook=True)`.
* **browser**: `serve.cjs` mounts `runtime_js/` at `/__runtime/` on the loopback
  server and `importMapHtml()` emits an import map pointing `three` at
  `/__runtime/node_modules/three/build/three.module.js`.  Nothing is ever fetched
  from a CDN.

So `runtime_js/node_modules` is a *harness* dependency, on the same footing as
the harness's python dependencies — not part of the generated artifact, and not
something the agent may install into.

---

## 6. Blender

Optional, but the `static_object` default language and the entire
`articulated_object` track need it.  Any headless-capable 4.2+ / 5.x build works.

Option A — distro package (simplest, often older):

```bash
sudo apt-get install blender     # or: brew install --cask blender
```

Option B — official tarball (what this box uses, no sudo needed); check
<https://www.blender.org/download/> for the current file name:

```bash
mkdir -p ~/tools && cd ~/tools
curl -LO https://download.blender.org/release/Blender5.0/blender-5.0.1-linux-x64.tar.xz
tar xf blender-5.0.1-linux-x64.tar.xz
~/tools/blender-5.0.1-linux-x64/blender --version
```

### The `LD_LIBRARY_PATH` wrapper trick

The stock Linux build links `libSM.so.6` / `libICE.so.6` even in `--background`
mode, and those are not installed system-wide here (no sudo).  They were
extracted from the Ubuntu debs into `~/.local/xlibs`, and a one-line wrapper on
`PATH` prepends that directory to the loader path so no caller has to remember —
`~/.local/bin/blender-5.0`:

```bash
#!/bin/bash
BLENDER_HOME="${BLENDER_HOME:-/home/yipeng/3dcodeverse_data/tools/blender-5.0.1-linux-x64}"
export LD_LIBRARY_PATH="/home/yipeng/.local/xlibs/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH}"
exec "$BLENDER_HOME/blender" "$@"
```

Make it executable (`chmod +x ~/.local/bin/blender-5.0`) and keep the name — it
is the first candidate the harness looks for.  If your distro does have the X11
libs, point the wrapper at the tarball and drop the `LD_LIBRARY_PATH` line, or
skip the wrapper entirely.

### How the harness finds it

`Settings.resolve_blender()` (`codeverse/config.py`) returns the first hit of:

1. `settings.binaries.blender` if that path exists — set it in the settings YAML
   or with `CV3D_BINARIES__BLENDER=/path/to/blender`;
2. `blender-5.0`, `blender`, `blender-5.1`, `blender-4.2` on `PATH`, in that order.

Nothing found → `3dcodeverse doctor` reports `blender FAIL` and builds raise
`BlenderNotFoundError`.  Blender is always invoked `-b --factory-startup` with
`PYTHONPATH`/`PYTHONHOME` stripped from the child env, so your conda env cannot
leak into bpy.

---

## 7. GPU / headless rendering

Nothing to install beyond the `graphics` extra (§4) and `runtime_js` (§5) — the
harness picks the backend itself and both paths have a software fallback.

* **Three.js / scenes** → headless Chrome via `runtime_js/gpu_launch.cjs`, which
  tries `--use-angle=gl-egl` plus the Mesa d3d12 env and probes
  `UNMASKED_RENDERER`; a negative verdict is cached 20 minutes in
  `~/.cache/codeverse/gpu_probe.json`, and it falls back to SwiftShader.
  Force with `CV3D_RENDER_GPU=on|off|auto` (node side) or `render.gpu` in the
  settings YAML / `CV3D_RENDER__GPU`.
* **graphics track** → moderngl in a fresh subprocess per render
  (`spatial/gl_render.py`), which sets `GALLIUM_DRIVER=d3d12` +
  `MESA_LOADER_DRIVER_OVERRIDE=d3d12` first and retries with
  `LIBGL_ALWAYS_SOFTWARE=1` + llvmpipe if the context cannot be created.  You do
  not set these yourself.

On a headless Linux server without a GPU, both fallbacks work out of the box;
expect renders to take a few times longer.  On WSL2 you need the vendor's WSL
driver installed on the Windows side for the d3d12 path — check with
`3dcodeverse doctor`'s `chrome webgl` row.

---

## 8. Keys, CLIs and settings

### 8.1 Model API keys

| variable | needed for | notes |
|---|---|---|
| `GEMINI_API_KEYS` | all `gemini:*` models (planner, judge, captioner, generators) | comma-separated list; the whole list becomes one `KeyPool` (per-key rate limits, 30 s cooldown on 429, dead keys benched 1 h) |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | same | single-key fallback when `GEMINI_API_KEYS` is unset |
| `ANTHROPIC_API_KEY` | `anthropic:*` chat models | optional; not set here → those backends are unavailable (doctor WARN) |
| `OPENAI_API_KEY` | `openai:*` chat models | optional; `CV3D_OPENAI_BASE_URL` points at a compatible gateway |

Resolution order for the Gemini keys (`codeverse/config.py`), first non-empty wins:
settings YAML `gemini_api_keys:` → `GEMINI_API_KEYS` (csv) → `GEMINI_API_KEY` /
`GOOGLE_API_KEY` → the legacy compatibility file
`~/.config/astra3d/gemini_keys.env`, from which a line
`GEMINI_API_KEYS="key1,key2,…"` is read (this is where the 22 keys on this box
come from).  Duplicates are removed, order preserved.  Keys are never written
into run records.

```bash
export GEMINI_API_KEYS="key1,key2,key3"        # e.g. in ~/.bashrc
3dcodeverse doctor --live                      # one ~$0.00001 "pong" call
```

### 8.2 The coding-agent CLIs (`gemini` required, the rest optional)

The generator is always a vendor CLI: `--generator gemini-cli:… | claude-code:… | codex:… | agy:…`.
Since 2026-08-28 the default is `gemini-cli:gemini-3.7-flash`, so **`gemini` is the one CLI a default
run needs**; it authenticates with the same api key as the harness's own calls.  The other three are
needed only when you name them.

```bash
npm i -g @google/gemini-cli        # gemini   (0.53.0 here)
npm i -g @anthropic-ai/claude-code # claude   (2.1.241 here)
npm i -g @openai/codex             # codex    (0.149.0 here)
# agy (Antigravity, 1.1.19 here) ships with the Antigravity IDE
```

`claude`, `codex` and `agy` authenticate against your local subscription (their
own `login` flows) — test lightly.  The harness handles the per-CLI quirks for
you; the ones worth knowing:

* **gemini-cli** — the harness writes a *system settings* file per session into that
  session's `trajectories/<label>_rNN/` dir and passes it via
  `GEMINI_CLI_SYSTEM_SETTINGS_PATH`, forcing four things (do not undo them):
  `security.auth.selectedType = "gemini-api-key"` (a pool key is injected as
  `GEMINI_API_KEY`, other credential env is stripped),
  `experimental.dynamicModelConfiguration = true` — without it an unknown model
  id is *silently substituted* and the run is flagged
  `exit_reason=model_substituted` — and `security.folderTrust.enabled = false`,
  without which the workspace MCP servers are silently ignored even with
  `--skip-trust`, so the agent loses every spatial tool.  It also carries the `3dcv`
  MCP server itself plus `mcp.allowed = ["3dcv"]`: this file is merged LAST and
  `mcp.allowed` replaces, so a server the agent plants in the workspace's own
  `.gemini/settings.json` is Blocked.  A pre-existing OAuth
  login of your own is overridden, not consumed.
* **codex** — MCP tools need `default_tools_approval_mode="approve"`, which the
  harness passes on the command line.
* **agy** — no per-workspace MCP; spatial tools are reached through
  `3dcv tools <name> --json … --workspace .` instead.

The spatial MCP server itself is the `mcp` extra (§4); check it with
`python -m codeverse.spatial.mcp_server --workspace <ws> --list`.

### 8.3 Settings file and `CV3D_` overrides

Loaded in this order, later wins: built-in defaults →
`~/.config/codeverse/config.yaml` → `./codeverse.yaml` (current working
directory) → `CV3D_*` environment variables.  Neither YAML file is required —
this box runs on defaults plus env.

```yaml
# ~/.config/codeverse/config.yaml
runs_dir: /data/runs
cache_dir: /data/cache/codeverse
binaries:
  blender: /home/yipeng/.local/bin/blender-5.0
render:
  gpu: auto          # auto | on | off
  width: 768
limits:
  agent_timeout_s: 1800
  max_parallel_agents: 6
default_judge: gemini:gemini-3.1-pro-preview
default_candidates: 1
```

Env equivalents use the `CV3D_` prefix and `__` for nesting:

```bash
export CV3D_RUNS_DIR=/data/runs
export CV3D_BINARIES__BLENDER=/opt/blender/blender
export CV3D_RENDER__GPU=off
export CV3D_LIMITS__AGENT_TIMEOUT_S=900
export CV3D_DEFAULT_CANDIDATES=2
```

Three more are read directly by the runtime (not via `Settings`):
`CV3D_RENDER_GPU` (`auto|on|off`, seen by the node/moderngl renderers),
`CV3D_CACHE_DIR` (where `gpu_launch.cjs` puts the shared-browser endpoint file)
and `CV3D_BROWSER_REUSE=off` (disable the shared headless-Chrome daemon).

---

## 9. Verify — `3dcodeverse doctor`

```bash
cd harness && 3dcodeverse doctor        # add --live for one tiny Gemini call, --no-gpu to skip the Chrome probe, --json for machine output
```

Real output on this box (exit code 0; any FAIL row makes it exit 1):

```
                                  3dcv doctor
┏━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ check         ┃ status ┃ detail                                              ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ python        │ OK     │ 3.13.9 (/home/yipeng/miniconda3/bin/python3.13)     │
│ python deps   │ OK     │ 23/23 importable                                    │
│ blender       │ OK     │ Blender 5.0.1 @ /home/yipeng/.local/bin/blender-5.0 │
│ node          │ OK     │ v24.14.0                                            │
│ three         │ OK     │ 0.182.0 (…/harness/runtime_js/node_modules)         │
│ puppeteer     │ OK     │ v24.43.1; chrome cache found (~/.cache/puppeteer)   │
│ chrome webgl  │ OK     │ GPU: ANGLE (Microsoft Corporation, D3D12 (NVIDIA    │
│               │        │ GeForce RTX 5090 Laptop GPU), OpenGL ES 3.1)        │
│ gemini keys   │ OK     │ 22 key(s)                                           │
│ anthropic key │ WARN   │ not set (anthropic:* backends unavailable)          │
│ openai key    │ WARN   │ not set (openai:* backends unavailable)             │
│ gemini-cli    │ OK     │ 0.53.0                                              │
│ claude        │ OK     │ 2.1.241 (Claude Code)                               │
│ codex         │ OK     │ codex-cli 0.149.0                                   │
│ agy           │ OK     │ 1.1.19                                              │
│ git           │ OK     │ git version 2.53.0                                  │
│ mcp           │ OK     │ mcp + codeverse.spatial.mcp_server importable       │
└───────────────┴────────┴─────────────────────────────────────────────────────┘
```

`--live` adds one more row (real output here, and the cheapest possible proof
that the keys work end to end):

```
│ gemini live call │ OK     │ 'pong' cost=$0.00001                             │
```

### Troubleshooting table

| row | status | meaning | fix |
|---|---|---|---|
| `python` | — | interpreter running the CLI | if it is not the interpreter you installed into, your shell is picking up another `3dcodeverse` — `which -a 3dcodeverse`, reinstall with `python -m pip install -e harness` |
| `python deps` | FAIL | a core import is missing/broken | `pip install -e 'harness[all]'`; a broken native lib (`fcl`, `manifold3d`) shows up here too — reinstall that wheel (`pip install --force-reinstall python-fcl`) |
| `python deps` | WARN | only optional-extra modules missing (`manifold3d` / `shapely` / `yourdfpy` / `mcp` / `moderngl` / `cadquery`) | install the extra the row names, e.g. `pip install -e 'harness[mesh,urdf,mcp,graphics,cad]'` (§4) |
| `blender` | FAIL | no binary found, or `--version` failed | §6 — install Blender, or `export CV3D_BINARIES__BLENDER=/path/to/blender`. If it is found but fails, run it by hand: a `libSM.so.6`/`libICE.so.6` error means you need the `LD_LIBRARY_PATH` wrapper |
| `node` | FAIL | node missing or older than 20.6.0 (§2.1) | install a newer node (`nvm install --lts`) or point `binaries.node` / `CV3D_BINARIES__NODE` at one.  The same check fires from every node workload (`codeverse.spatial.node.run_node`), so a too-old node cannot fail obscurely mid-render |
| `runtime_js` | FAIL | the node runtime directory cannot be resolved | use a full checkout, or set `CV3D_RUNTIME_JS=/path/to/harness/runtime_js`; then run `npm ci` there (§5) |
| `three` | FAIL | `runtime_js/node_modules/three` missing | `cd harness/runtime_js && npm ci` (§5) |
| `puppeteer` | FAIL | not installed in `runtime_js` | `cd harness/runtime_js && npm ci` |
| `puppeteer` | WARN | installed, but no Chrome in `~/.cache/puppeteer` | `cd harness/runtime_js && npx puppeteer browsers install chrome` (or unset `PUPPETEER_SKIP_DOWNLOAD` / fix `PUPPETEER_CACHE_DIR`) |
| `chrome webgl` | WARN | Chrome launched but on SwiftShader | fine, just slower; on WSL2 install the vendor WSL GPU driver, on a server install Mesa EGL. `CV3D_RENDER_GPU=off` to stop probing |
| `chrome webgl` | FAIL | the browser could not launch at all | usually missing shared libs for Chrome (`ldd ~/.cache/puppeteer/chrome/*/chrome-linux64/chrome \| grep -i "not found"`) or a stale endpoint file — `rm -f ~/.cache/codeverse/browser_*.lock` and retry; `--no-gpu` skips this probe |
| `chrome webgl` | SKIP | `runtime_js/gpu_launch.cjs` not present | you are not in a full checkout |
| `gemini keys` | FAIL | no keys resolved | `export GEMINI_API_KEYS="k1,k2"` (§8.1) — everything model-driven needs this |
| `anthropic key` / `openai key` | WARN | not set | expected unless you use `anthropic:*` / `openai:*`; export the key to clear it |
| `gemini-cli` / `claude` / `codex` / `agy` | WARN | CLI not on `PATH` | optional (§8.2); only that `--generator` is unavailable |
| `git` | FAIL | git missing | install git — run workspaces are git repos |
| `mcp` | WARN | `mcp` package or the server module missing | `pip install -e 'harness[mcp]'`; only affects the agentic CLI backends |
| `gemini live call` (`--live`) | FAIL | key rejected / no network | check the key value and outbound access; a `503 … high demand` is transient, not an install problem |
| `gemini quota` | OK | always informational | the per-key RPM/TPM the pool schedules against x the number of keys (`Settings.rate`, docs/COST.md Part III); tune with `CV3D_RATE__TPM_PER_KEY` / `CV3D_RATE__MAX_IN_FLIGHT` |
| `pool sharing` | WARN | this process's configured concurrency does not fit beside sibling harness processes | wait for the siblings or set `CV3D_MAX_IN_FLIGHT` to the headroom printed in the row |
| `gemini pool` (`--live`) | WARN | a key is benched as dead | that key 401/403'd; rotate or remove it — the pool re-probes it after an hour.  The row also shows in-flight, RPM/TPM headroom used and this process's 429/5xx counts |
| `storm gate` (`--live`) | WARN | a capacity storm is running | provider-side (`503 high demand`), not an install problem; the gate is off by default (docs/COST.md §21) and the row only appears when something enabled it |
| `skills switch` | WARN | skills are disabled | expected by default; set `CV3D_SKILLS=on` only when you want skill routing |
| `skills library` | WARN / FAIL | no bundles were found, or one or more bundles are invalid | use a full checkout and validate the named bundle under `codeverse/skills/library/` |
| `skills routing` | WARN | a routed skill has no installed bundle | restore the missing bundle from the checkout or update the stale route |
| `skills discovery` | OK | always informational | shows the agent-native directories where bundles are materialised |
| `claude-code Skill tool` | FAIL | the Claude backend would deny native skill activation | update/reinstall the harness so `Skill` is present in Claude Code's allowed tools |

---

## 10. 5-minute smoke

The cheapest end-to-end run is a single-shot shader on the `graphics` track
(~$0.05, ~2 minutes, no Blender, no node).  Point it at a throwaway runs
directory so you do not mix it into real data:

```bash
cd harness
3dcv make "a slow rotating neon hex grid" \
    --track graphics --language glsl_shader \
    --generator single-shot:gemini:gemini-3.7-flash \
    --rounds 0 --max-minutes 20 \
    --runs-dir /tmp/cv3d_smoke
3dcv status <slug> --runs-dir /tmp/cv3d_smoke
```

If node and Blender are installed, the equivalent object-track smoke is
`3dcv make "a simple wooden stool" --track static_object --language blender
--generator single-shot:gemini:gemini-3.7-flash --rounds 0` (~$0.1, a few
minutes).

Artifacts land in `<runs-dir>/<slug>/`:

```
spec.json  plan.json  record.json  run_state.json  events.jsonl
src/                      the generated code (a git repo — one commit per round)
artifacts/                build.json, census.json, measurement.json,
                          object.glb (object tracks) / frames/ + frames_sheet.png +
                          preview.gif + metrics.json (graphics)
artifacts/renders/rNN/    per-round view PNGs + sheet.png
artifacts/gates/rNN/      deterministic gate output
artifacts/judge/rNN.json  the VLM verdict and the computed score
trajectories/             per-call transcripts
```

`3dcv status <slug>` prints the rounds table, cost and last events; the full
operating guide is `docs/RUNBOOK.md`.

> **`capacity storm N/10 … waiting Ns`** means the Gemini endpoint is busy, not
> that your install is broken: the harness backs off (up to 10 waits, ~10 min in
> total) and rotates keys.  While the bench batteries on this box were saturating
> `gemini-3.7-flash`, a smoke with `--max-minutes 8` gave up in the planner with
> `status=budget … elapsed 11.2 min exceeds max_minutes 8.0` after spending
> $0.0091 — hence the roomier `--max-minutes 20` above.  `3dcv resume <slug>
> --runs-dir …` continues from the last completed stage.  To prove the key path
> alone, `3dcodeverse doctor --live` is the one-call version
> (`gemini live call OK 'pong' cost=$0.00001`).

---

## 11. Uninstall / cleanup

```bash
pip uninstall 3dcodeverse                     # removes the editable install + 3dcodeverse/3dcv
rm -rf harness/runtime_js/node_modules        # ~97 MB, re-creatable with `npm ci`
rm -rf ~/.cache/puppeteer                     # ~636 MB of downloaded Chrome builds
rm -rf ~/.cache/codeverse                     # harness cache (619 MB here) — see below
rm -rf harness/3dcodeverse.egg-info harness/.pytest_cache harness/.ruff_cache
```

`~/.cache/codeverse` (or `cache_dir` / `CV3D_CACHE_DIR`) holds only regenerable
things: `renders/` and `judge_images/` (render + montage cache), `textures/` and
`texture_plans/` (texture-pass cache — deleting these costs real money to
regenerate), `gpu_probe.json`, `browser_*.lock` (the shared
headless-Chrome endpoint file — do not delete it while renders are running).

**Your data is not in either cache.**  `runs/`, `bench/out/` and any exported
dataset are the flywheel output — delete them deliberately, never as part of a
cleanup.  On this machine `harness/bench/out/` is read-only reference material
(the early `e2e_*` reference runs were archived off-repo on 2026-08-29).

Uninstalling does not touch `~/.config/codeverse/config.yaml`,
`~/.config/astra3d/gemini_keys.env` or the globally installed CLIs — remove
those by hand (`npm rm -g @google/gemini-cli @anthropic-ai/claude-code @openai/codex`).

---

## 12. Appendix: verified versions on this box

| component | version | where |
|---|---|---|
| python | 3.13.9 (the one supported version — §2.1) | `/home/yipeng/miniconda3/bin/python` |
| pip packages | pydantic 2.13.2 · trimesh 4.12.2 · python-fcl 0.7.0.11 · moderngl 5.12.0 · shapely 2.1.2 · networkx 3.6.1 · manifold3d 3.5.2 · pyarrow 24.0.0 · mcp 2.0.0 · scipy 1.18.0 · yourdfpy 0.0.60 · cadquery 2.8.0 · google-genai 2.10.0 · pytest 9.1.1 · ruff 0.15.20 | editable install of `harness/` |
| node / npm | v24.14.0 / 11.9.0 (floor 20.6.0 — §2.1, verified against node 20.19.5) | `/home/yipeng/miniconda3/bin/node` |
| runtime_js deps | three 0.182.0 · puppeteer 24.43.1 · three-mesh-bvh 0.9.14 (100 packages, 97 MB) | `harness/runtime_js/node_modules` |
| Chrome (puppeteer) | 148.0.7778.97 (+ headless-shell) | `~/.cache/puppeteer` |
| Blender | 5.0.1 (2025-12-16) | `~/.local/bin/blender-5.0` → `~/3dcodeverse_data/tools/blender-5.0.1-linux-x64` |
| git | 2.53.0 | `/usr/bin/git` |
| gemini-cli / claude / codex / agy | 0.53.0 / 2.1.241 / 0.149.0 / 1.1.19 | npm global / `~/.local/bin` |
| GPU (Chrome WebGL) | ANGLE · D3D12 · NVIDIA GeForce RTX 5090 Laptop GPU · OpenGL ES 3.1 | via `--use-angle=gl-egl` + Mesa d3d12 |
