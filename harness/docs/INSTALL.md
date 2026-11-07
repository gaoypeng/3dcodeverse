# Installing the 3dcodeverse harness

Paths are relative to the harness root (`harness/` in the 3dcodeverse checkout) unless absolute.
Once installed, read `docs/RUNBOOK.md` (how to run) and `docs/ARCHITECTURE.md` (what a run
does).

1. [TL;DR](#1-tldr) · 2. [Prerequisites](#2-prerequisites)
(incl. [supported versions](#21-supported-versions)) ·
3. [Get the code](#3-get-the-code) · 4. [Python package + extras](#4-python-package--extras) ·
5. [runtime_js — the node side](#5-runtime_js--the-node-side) · 6. [Blender](#6-blender) ·
7. [GPU / headless rendering](#7-gpu--headless-rendering) ·
8. [Keys, CLIs and settings](#8-keys-clis-and-settings) ·
9. [Verify — `3dcodeverse doctor`](#9-verify--3dcodeverse-doctor) ·
10. [5-minute smoke](#10-5-minute-smoke) · 11. [Uninstall / cleanup](#11-uninstall--cleanup)

---

## 1. TL;DR

```bash
git clone <repo> 3dcodeverse && cd 3dcodeverse
python3 -m venv .venv && source .venv/bin/activate    # see §3 — a distro python3 is
                                                      # PEP 668 and pip will refuse it
bash harness/setup.sh            # python + node deps + chrome + the agy / claude / codex CLIs + doctor
```

`setup.sh` is idempotent — it checks the interpreter versions, runs
`pip install -e 'harness[all,dev]'`, runs `npm ci` in `runtime_js/` **only when the lockfile
actually moved**, makes sure puppeteer's Chrome is downloaded, brings the three coding-agent
CLIs to their latest release (§8.2), and finishes by printing `3dcodeverse doctor`.  Flags:
`--extras cad,urdf`, `--python /path/to/python`, `--no-python`, `--no-node`, `--no-chrome`,
`--no-clis`, `--no-doctor`, `--no-gpu`, `--force-npm`, `--help`.

The manual equivalent:

```bash
pip install -e 'harness[all,dev]'
cd harness/runtime_js && npm ci && npx puppeteer browsers install chrome && cd ..
curl -fsSL https://antigravity.google/cli/install.sh | bash       # agy (later: `agy update`)
npm i -g @anthropic-ai/claude-code@latest @openai/codex@latest    # the optional generators
3dcodeverse doctor
```

> **Running installs on a busy box.**  `npm ci` deletes `node_modules/` before re-creating it,
> and `pip install -e` rewrites the entry points.  If benches or renders are running against
> this checkout, use `setup.sh --no-clis` (it skips `npm ci` when the lockfile is unchanged) or
> wait for them to finish.

---

## 2. Prerequisites

### 2.1 Supported versions

One fixed Python version — **3.13** — no floor, no matrix, no compatibility shims, and no CI: the
offline suite and `ruff` are run locally before every push (see `CLAUDE.md`).

| component | version | how it is enforced | notes |
|---|---|---|---|
| **python** | **3.13** | `requires-python = ">=3.13"`; ruff `target-version = "py313"`; `PY_FLOOR` in `tests/core/test_portability.py`; `MIN_PY_MINOR` in `setup.sh` | the four places are pinned together by the portability test |
| **node** | **20.6.0+** (developed on 24 LTS) | `runtime_js/package.json` `engines.node`; `codeverse3d.spatial.node.NODE_MIN` fails every node workload with an actionable message | 20.6 is the `--import` module-hook floor |
| **Blender** | **5.2 LTS** only | `BLENDER_MIN = (5, 2)` in `codeverse3d/config.py`; `3dcodeverse doctor` fails anything older; `Settings.resolve_blender()` | `blender`, `urdf_blender` and `scene_blender` are the users; the prompts and lints state 5.2 API facts |
| **OS** | Linux x86_64 (WSL2 works) | — | macOS should work (nothing is Linux-specific except `resource.setrlimit` guards) but is not tested |

Moving to another Python later is the same four-line change (`requires-python`, ruff
`target-version`, `PY_FLOOR`, `MIN_PY_MINOR`).

### 2.2 What you need installed

| what | required? | how it degrades without it |
|---|---|---|
| **Python 3.13** | yes | nothing runs; `requires-python = ">=3.13"` (§2.1) |
| **pip + venv** | yes | `setup.sh` cannot install the package.  A stock Debian/Ubuntu `/usr/bin/python3` ships **without** pip and is PEP 668 `EXTERNALLY-MANAGED`, so `pip install -e harness` refuses even once pip is present: `sudo apt install python3-venv python3-pip`, then use a virtualenv |
| **git** | yes | run workspaces are git repos (one commit per round); `Workspace.create()` and the flywheel pair miners fail |
| **node ≥ 20.6** | yes, except for the `graphics` track | the `threejs` / `scene_threejs` languages disappear **and no object renders happen at all**: `spatial/render.py` renders *every* GLB (Blender-built and CadQuery-built included) with three.js in headless Chrome.  Only `graphics` (moderngl) is node-free |
| **Blender 5.2 LTS** | optional | `blender`, `urdf_blender` and `scene_blender` languages unavailable → the `static_object` default language and the whole `articulated_object` track cannot build (`BlenderNotFoundError`); scenes lose planner-chosen bpy GLB assets |
| **EGL-capable GPU stack** | optional | headless Chrome falls back to **SwiftShader** and moderngl to **llvmpipe** — everything still renders, just several times slower; no correctness change |
| C toolchain | usually no | only if pip has to build a wheel from source (`python-fcl` ships wheels for cp310–cp313 x86_64) |

Disk: ~100 MB for `runtime_js/node_modules`, ~640 MB for puppeteer's Chrome, ~1 GB for a
Blender tarball, plus whatever `runs/` and `~/.cache/codeverse3d` grow to.

---

## 3. Get the code

```bash
git clone <repo> 3dcodeverse
cd 3dcodeverse
```

Layout: the harness is the `harness/` subdirectory (python dist `3dcodeverse`, import package
`codeverse3d`, CLI `3dcodeverse`).  See the repo `README.md` for the other
top-level components.

Use a virtualenv or a conda env.  It is only optional when your interpreter already owns its
site-packages: a distro `/usr/bin/python3` is PEP 668 `EXTERNALLY-MANAGED` and pip will refuse
to install into it.  `setup.sh` checks for pip and for that marker up front and tells you which
of the two you hit.

```bash
python -m venv .venv && source .venv/bin/activate     # or: conda create -n c3d python=3.13
```

---

## 4. Python package + extras

```bash
pip install -e harness            # core only
pip install -e 'harness[all,dev]' # everything (what setup.sh does)
```

`-e` (editable) is the intended mode: the harness is developed in place and the console script
`3dcodeverse` points back at the checkout.

Core dependencies (always installed): pydantic + pydantic-settings (contracts, settings), typer +
rich (CLI), jinja2 (prompt templates), pyyaml, numpy, trimesh (mesh measurement), python-fcl
(collision), pillow, google-genai, anthropic, openai.

| extra | pulls | unlocks |
|---|---|---|
| `cad` | cadquery | the `cadquery` language (`--language cadquery`) |
| `urdf` | yourdfpy, scipy | the `articulated_object` track: URDF parse/validate + joint math |
| `graphics` | moderngl | the `graphics` track (`glsl_shader`, `opengl_python`) — headless GL rendering in `spatial/gl_render.py` |
| `mesh` | shapely, networkx, matplotlib | `cross_section` (filled area / hollow ratio from shapely, the image drawn in matplotlib), mesh split / connectivity (networkx), judge cross-section slices on gate-ERROR rounds (without the extra the judge logs a warning and sends the payload without slices) |
| `mcp` | mcp | `3dcodeverse mcp` / `codeverse3d.spatial.mcp_server`, i.e. spatial tools for the `agy`, `claude-code` and `codex` backends |
| `flywheel` | pyarrow | `3dcodeverse flywheel export --pack` (parquet shards).  A plain `flywheel export` needs nothing: without pyarrow it writes `metadata.jsonl` and skips `metadata.parquet` with a warning |
| `all` | all of the above | — |
| `dev` | pytest, pytest-timeout, ruff | the test suite and the linter |

`graphics`, `mesh`, `mcp` and `flywheel` are lazily imported: without them the harness installs
and starts fine and only the named feature raises `ImportError` at the moment you use it.
`3dcodeverse doctor` lists every one of these modules under `python deps` and only tolerates the
optional-extra modules — `shapely` (mesh), `yourdfpy` (urdf), `mcp`, `moderngl` (graphics),
`cadquery` (cad) — as WARN, naming the extra to install; everything else missing is a FAIL.

Run the offline test suite to confirm the install:

```bash
cd harness
python -m pytest tests -q                                               # needs node + Blender for the full set
python -m pytest tests -q -m "not live and not blender and not node"    # pure-python subset
# Parallel by default (pytest-xdist, -n auto --dist worksteal).  Serial: add -n0.
python -m pytest tests -q -m live                                       # OPT-IN: real API calls, needs keys
```

Live tests are deselected by default (`addopts = ["-m", "not live"]` in `pyproject.toml`), so a
bare `pytest` never spends money and never hangs waiting on a provider outage.  A command-line
`-m` **replaces** that default rather than adding to it, so any subset you select must spell
`not live` itself — as the line above does.

---

## 5. runtime_js — the node side

Node.js itself is a *system prerequisite* you install once (§2).  `runtime_js/` is a small **npm
package owned by the harness**, and `node_modules/` is that package's dependency tree, downloaded
by npm into the checkout.  Apart from the `graphics` track, every render in the harness goes
through it (`spatial/render.py` → `render_glb.mjs` → three.js in headless Chrome), whichever
language produced the GLB.

```
harness/runtime_js/
  package.json        committed  — 2 direct deps
  package-lock.json   committed  — exact tree, the reproducibility unit
  node_modules/       NOT committed — .gitignore'd, ~100 MB, created by `npm ci`
  *.mjs *.cjs lib/    the harness's own node code (see runtime_js/README.md)
```

Direct dependencies: `three` (GLB export, the render rig, the scene host, GLSL preflight) and
`puppeteer` (headless Chrome for WebGL rendering).

### Install / refresh

```bash
cd harness/runtime_js
npm ci                                   # exact lockfile install
npx puppeteer browsers install chrome    # only if the Chrome cache is missing (see below)
```

Use **`npm ci`**, not `npm install`: `ci` installs exactly what `package-lock.json` pins and
never rewrites it, which is what keeps renders reproducible.  `npm ci` **deletes
`node_modules/` first**, so do not run it while a bench or render is using this checkout.  Re-run
it when `node_modules/` does not exist (fresh clone), when you pulled a change to
`runtime_js/package.json` / `package-lock.json`, or when `3dcodeverse doctor` reports `three` or
`puppeteer` FAIL.  `setup.sh` automates exactly that test (`--force-npm` overrides).

### Chrome

puppeteer downloads its own Chrome build (it does **not** use a system Chrome) into
`~/.cache/puppeteer` (~640 MB).  npm's `postinstall` normally does this for you; run the explicit
command when the cache was cleared or when `PUPPETEER_SKIP_DOWNLOAD` was set:

```bash
cd harness/runtime_js && npx puppeteer browsers install chrome
```

Re-running it is a no-op that just prints the cached path.  `PUPPETEER_CACHE_DIR` moves the
cache elsewhere (keep it consistent between install and run).

### Why generated code never sees this directory

Agent-written Three.js code is **raw** — it does `import * as THREE from 'three'` and nothing
else; it never imports a harness module and never gets a `node_modules/` of its own inside the
run workspace.  The harness resolves that bare specifier for it in two places:

* **node**: `node --import runtime_js/lib/resolve_three.mjs …` installs a `registerHooks`
  resolver that redirects `three` and `three/addons/*` into `runtime_js/node_modules`, for a
  module living anywhere on disk (`NODE_PATH` cannot do this — it is CommonJS-only).  Python
  side: `spatial.node.run_node(..., three_hook=True)`.
* **browser**: `serve.cjs` mounts `runtime_js/` at `/__runtime/` on the loopback server and
  `importMapHtml()` emits an import map pointing `three` at
  `/__runtime/node_modules/three/build/three.module.js`.  Nothing is ever fetched from a CDN.

---

## 6. Blender

Optional, but the `static_object` default language, the entire `articulated_object` track and
the `scene_blender` scene language need it.  **Blender 5.2 LTS is the one supported version**
(`doctor` fails anything older).

Option A — distro package (only if it ships 5.2 LTS):

```bash
sudo apt-get install blender     # or: brew install --cask blender
```

Option B — official tarball (no sudo needed); verify the checksum against
<https://download.blender.org/release/Blender5.2/blender-5.2.2.sha256>:

```bash
mkdir -p ~/tools && cd ~/tools
curl -LO https://download.blender.org/release/Blender5.2/blender-5.2.2-linux-x64.tar.xz
tar xf blender-5.2.2-linux-x64.tar.xz
~/tools/blender-5.2.2-linux-x64/blender --version      # Blender 5.2.2 LTS
```

### Missing X11 libraries (no sudo)

The stock Linux build links `libSM.so.6` / `libICE.so.6` even in `--background` mode.  If they
are not installed system-wide, extract them from the distro packages into a directory of your
own and put a one-line wrapper on `PATH` named `blender-5.2` (the first name the harness looks
for):

```bash
#!/bin/bash
BLENDER_HOME="${BLENDER_HOME:-$HOME/tools/blender-5.2.2-linux-x64}"
export LD_LIBRARY_PATH="$HOME/.local/xlibs/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH}"
exec "$BLENDER_HOME/blender" "$@"
```

### Rendering

`languages/wrappers/render_bpy_scene.py` owns every render setting for `scene_blender`'s judged
frames: Cycles on CUDA with GPU denoising and persistent data when a CUDA device exists, CPU
otherwise; `Settings.render.blender_frame_time_limit_s` (default 20 s) caps a heavy scene's
sampling per frame.  **OptiX is never enumerated** (it is unavailable under WSL2 and probing it
crashes Blender).  `3dcodeverse doctor` times one small frame on exactly this path.

### How the harness finds it

`Settings.resolve_blender()` (`codeverse3d/config.py`) returns the first hit of:

1. `settings.binaries.blender` when it is set — in the settings YAML or with
   `C3D_BINARIES__BLENDER=/path/to/blender`.  A configured binary is never replaced by one from
   `PATH`: if it is missing or not executable, builds raise `BlenderNotFoundError` and `doctor`
   reports `blender FAIL`;
2. only when nothing is configured: `blender-5.2`, then `blender` on `PATH`.

Nothing found → `3dcodeverse doctor` reports `blender FAIL` and builds raise
`BlenderNotFoundError`.  Blender is always invoked `-b --factory-startup` with
`PYTHONPATH`/`PYTHONHOME` stripped from the child env, so your conda env cannot leak into bpy.

---

## 7. GPU / headless rendering

Nothing to install beyond the `graphics` extra (§4) and `runtime_js` (§5) — the harness picks
the backend itself and both paths have a software fallback.

* **Three.js / scenes** → headless Chrome via `runtime_js/gpu_launch.cjs`, which tries
  `--use-angle=gl-egl` plus the Mesa d3d12 env and probes `UNMASKED_RENDERER`; a negative
  verdict is cached 20 minutes in `~/.cache/codeverse3d/gpu_probe.json`, and it falls back to
  SwiftShader.  Force with `C3D_RENDER_GPU=on|off|auto` (node side) or `render.gpu` in the
  settings YAML / `C3D_RENDER__GPU`.
* **graphics track** → moderngl in a fresh subprocess per render (`spatial/gl_render.py`), which
  sets `GALLIUM_DRIVER=d3d12` + `MESA_LOADER_DRIVER_OVERRIDE=d3d12` first and retries with
  `LIBGL_ALWAYS_SOFTWARE=1` + llvmpipe if the context cannot be created.  You do not set these
  yourself.

On a headless Linux server without a GPU, both fallbacks work out of the box; expect renders to
take a few times longer.  On WSL2 you need the vendor's WSL driver installed on the Windows side
for the d3d12 path — check with `3dcodeverse doctor`'s `chrome webgl` row.

---

## 8. Keys, CLIs and settings

### 8.1 Model API keys

| variable | needed for | notes |
|---|---|---|
| `GEMINI_API_KEYS` | all `gemini:*` models (planner, judge, captioner, generators) and `agy` | comma-separated list; the whole list becomes one `KeyPool` (a 429 rotates to another key, dead keys are benched 1 h) |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | same | single-key fallback when `GEMINI_API_KEYS` is unset |
| `ANTHROPIC_API_KEY` | `anthropic:*` chat models | optional; unset → those backends are unavailable (doctor WARN) |
| `OPENAI_API_KEY` | `openai:*` chat models | optional; `C3D_OPENAI_BASE_URL` points at a compatible gateway |

Resolution order for the Gemini keys (`codeverse3d/config.py`), first non-empty wins: settings
YAML `gemini_api_keys:` → `GEMINI_API_KEYS` (csv) → `GEMINI_API_KEY` / `GOOGLE_API_KEY` → a
line `GEMINI_API_KEYS="key1,key2,…"` in `~/.config/astra3d/gemini_keys.env`.  Duplicates are
removed, order preserved.  Keys are never written into run records.

```bash
export GEMINI_API_KEYS="key1,key2,key3"        # e.g. in ~/.bashrc
3dcodeverse doctor --live                      # one ~$0.00001 "pong" call
```

### 8.2 The coding-agent CLIs (`agy` required, the rest optional)

The generator is always a vendor CLI: `--generator agy:… | claude-code:… | codex:…`.  The
default is `agy:gemini-4-argon`, so **`agy` is the one CLI a default run needs**.  The harness
runs agy **only in Gemini-API mode**, so it needs Gemini keys (§8.1): one key from the harness's
own pool per session, the 3dcode MCP tools, priced at Gemini list price — no sign-in, no
non-Gemini model.  Without keys an agy session fails at once (doctor row `gemini keys`).
The other two are needed only when you name them.

`setup.sh` brings all three to their latest release on every run (`--no-clis` skips it): agy
through its own `agy update` — or, when there is no agy at all, Google's checksum-verified
installer into `~/.local/bin` — and the two npm globals whenever the installed version differs
from the registry's `latest`.  A claude or codex installed another way (a native installer,
Homebrew) is left to its own updater.  Nothing pins a version; the closing `doctor` prints the
three it found.  By hand:

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash      # agy; later: `agy update`
npm i -g @anthropic-ai/claude-code@latest @openai/codex@latest   # claude, codex
agy --version && claude --version && codex --version
```

`claude` and `codex` authenticate against your local subscription (their own `login` flows).
The harness handles the per-CLI quirks for you (agy's per-session API-mode HOME, codex's MCP
approval mode): `docs/RUNBOOK.md` §3.  The spatial MCP server itself is the `mcp` extra (§4);
check it with `python -m codeverse3d.spatial.mcp_server --workspace <ws> --list`.

### 8.3 Settings file and `C3D_` overrides

Loaded in this order, later wins: built-in defaults → `~/.config/3dcodeverse/config.yaml` →
`./3dcodeverse.yaml` (current working directory) → `C3D_*` environment variables.  Neither YAML
file is required.

```yaml
# ~/.config/3dcodeverse/config.yaml
runs_dir: /data/runs
cache_dir: /data/cache/codeverse3d
binaries:
  blender: ~/.local/bin/blender-5.2
render:
  gpu: auto          # auto | on | off
  width: 768
limits:
  agent_timeout_s: 1800
  max_parallel_agents: 6
default_judge: gemini:gemini-4-argon
judge:
  thinking: medium
```

Env equivalents use the `C3D_` prefix and `__` for nesting:

```bash
export C3D_RUNS_DIR=/data/runs
export C3D_BINARIES__BLENDER=/opt/blender/blender
export C3D_RENDER__GPU=off
export C3D_LIMITS__AGENT_TIMEOUT_S=900
export C3D_DEFAULT_JUDGE=gemini:gemini-4-argon
export C3D_JUDGE__THINKING=medium
```

Every knob the harness reads is a `Settings` field (`codeverse3d/config.py`, one description
each): `C3D_<FIELD>` at the top level, `C3D_<SECTION>__<FIELD>` nested, plus two flat
spellings of nested knobs — `C3D_MAX_IN_FLIGHT`, `C3D_RENDER_GPU`.  An
on/off switch (`C3D_SKILLS`, `C3D_POST`, `C3D_PLAN_BRIEF`, … — every `Flag` field) takes
on/off, 1/0, true/false or yes/no in any case; a typo warns and keeps the default, and an empty
value is unset.  The node side also reads `C3D_RENDER_GPU` and `C3D_CACHE_DIR` itself, and one
variable only it reads: `C3D_BROWSER_REUSE=off` disables the shared headless-Chrome daemon.

---

## 9. Verify — `3dcodeverse doctor`

```bash
cd harness && 3dcodeverse doctor        # add --live for one tiny Gemini call, --no-gpu to skip the Chrome probe, --json for machine output
```

One row per check (`python`, `python deps`, `blender`, `blender cycles`,
`blender gpu slots`, `node`, `three`, `puppeteer`, `chrome webgl`, `gemini keys`, `in-flight
slots`, `anthropic key`, `openai key`, `agy`, `claude`, `codex`, `git`,
`mcp`; `--live` adds `gemini live call`); any FAIL row makes it exit 1, WARN
rows name what is optional or busy.

### Troubleshooting table

| row | status | meaning | fix |
|---|---|---|---|
| `python` | — | interpreter running the CLI | if it is not the interpreter you installed into, your shell is picking up another `3dcodeverse` — `which -a 3dcodeverse`, reinstall with `python -m pip install -e harness` |
| `python deps` | FAIL | a core import is missing/broken | `pip install -e 'harness[all]'`; a broken native lib (`fcl`) shows up here too — reinstall that wheel (`pip install --force-reinstall python-fcl`) |
| `python deps` | WARN | only optional-extra modules missing (`shapely` / `yourdfpy` / `mcp` / `moderngl` / `cadquery`) | install the extra the row names, e.g. `pip install -e 'harness[mesh,urdf,mcp,graphics,cad]'` (§4) |
| `blender` | FAIL | no binary found, or `--version` failed | §6 — install Blender, or `export C3D_BINARIES__BLENDER=/path/to/blender`. If it is found but fails, run it by hand: a `libSM.so.6`/`libICE.so.6` error means you need the `LD_LIBRARY_PATH` wrapper |
| `blender cycles` | WARN | Cycles sees no CUDA device | scene_blender's judged frames render on the CPU (a few times slower) — fine without an NVIDIA GPU; on WSL2 install the vendor WSL GPU driver.  The row also names the judged-frame samples / device (`C3D_RENDER__BLENDER_*`) |
| `blender render` | FAIL / SKIP | the render probe could not run Blender (or there is none) | see the `blender` row |
| `blender gpu slots` | WARN | every machine-wide GPU Blender render slot is held right now | other renders are on the GPU; a render waits `C3D_RENDER__BLENDER_GPU_WAIT_S` for a slot and then renders on the CPU — nothing to fix unless it stays full.  `C3D_RENDER__BLENDER_GPU_SLOTS` sizes it |
| `node` | FAIL | node missing or older than 20.6.0 (§2.1) | install a newer node (`nvm install --lts`) or point `binaries.node` / `C3D_BINARIES__NODE` at one.  The same check fires from every node workload (`codeverse3d.spatial.node.run_node`), so a too-old node cannot fail obscurely mid-render |
| `runtime_js` | FAIL | the node runtime directory cannot be resolved | use a full checkout, or set `C3D_RUNTIME_JS=/path/to/harness/runtime_js`; then run `npm ci` there (§5) |
| `three` | FAIL | `runtime_js/node_modules/three` missing | `cd harness/runtime_js && npm ci` (§5) |
| `puppeteer` | FAIL | not installed in `runtime_js` | `cd harness/runtime_js && npm ci` |
| `puppeteer` | WARN | installed, but no Chrome in `~/.cache/puppeteer` | `cd harness/runtime_js && npx puppeteer browsers install chrome` (or unset `PUPPETEER_SKIP_DOWNLOAD` / fix `PUPPETEER_CACHE_DIR`) |
| `chrome webgl` | WARN | Chrome launched but on SwiftShader | fine, just slower; on WSL2 install the vendor WSL GPU driver, on a server install Mesa EGL. `C3D_RENDER_GPU=off` to stop probing |
| `chrome webgl` | FAIL | the browser could not launch at all | usually missing shared libs for Chrome (`ldd ~/.cache/puppeteer/chrome/*/chrome-linux64/chrome \| grep -i "not found"`) or a stale endpoint file — `rm -f ~/.cache/codeverse3d/browser_*.lock` and retry; `--no-gpu` skips this probe |
| `chrome webgl` | SKIP | `runtime_js/gpu_launch.cjs` not present | you are not in a full checkout |
| `gemini keys` | FAIL | no keys resolved | `export GEMINI_API_KEYS="k1,k2"` (§8.1) — everything model-driven needs this |
| `anthropic key` / `openai key` | WARN | not set | expected unless you use `anthropic:*` / `openai:*`; export the key to clear it |
| `agy` / `claude` / `codex` | WARN | CLI not on `PATH` | only that `--generator` is unavailable — but `agy` serves the default generator (§8.2) |
| `git` | FAIL | git missing | install git — run workspaces are git repos |
| `mcp` | WARN | `mcp` package or the server module missing | `pip install -e 'harness[mcp]'`; only affects the agentic CLI backends |
| `gemini live call` (`--live`) | FAIL | key rejected / no network | check the key value and outbound access; a `503 … high demand` is transient, not an install problem |
| `in-flight slots` | WARN | every machine-wide slot is held right now (`busy n/n`), or `max_in_flight=0` switched the cap off | busy: other harness processes are mid-call and new calls wait for a slot — nothing to fix unless it stays full; a slot is held only while a call is out and a killed process frees its slots at once.  Off: unset `C3D_MAX_IN_FLIGHT` |

---

## 10. 5-minute smoke

The cheapest end-to-end run is a single-shot shader on the `graphics` track (~$0.05, ~2
minutes, no Blender, no node).  Point it at a throwaway runs directory so you do not mix it into
real data:

```bash
cd harness
3dcodeverse make "a slow rotating neon hex grid" \
    --track graphics --language glsl_shader \
    --generator single-shot:gemini:gemini-3.8-flash \
    --rounds 0 --max-minutes 20 \
    --runs-dir /tmp/c3d_smoke
3dcodeverse status <slug> --runs-dir /tmp/c3d_smoke
```

If node and Blender are installed, the equivalent object-track smoke is
`3dcodeverse make "a simple wooden stool" --track static_object --language blender
--generator single-shot:gemini:gemini-3.8-flash --rounds 0` (~$0.1, a few minutes).

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

`3dcodeverse status <slug>` prints the rounds table, cost and last events; the full operating guide is
`docs/RUNBOOK.md`.

> **`capacity storm N/10 … waiting Ns`** means the Gemini endpoint is busy, not that your
> install is broken: the harness backs off (up to 10 waits, ~10 min in total) and rotates keys,
> which is why the smoke above gets `--max-minutes 20`.  `3dcodeverse resume <slug> --runs-dir …`
> continues from the last completed stage.  To prove the key path alone, `3dcodeverse doctor
> --live` is the one-call version.

---

## 11. Uninstall / cleanup

```bash
pip uninstall 3dcodeverse                     # removes the editable install + the 3dcodeverse command
rm -rf harness/runtime_js/node_modules        # re-creatable with `npm ci`
rm -rf ~/.cache/puppeteer                     # downloaded Chrome builds
rm -rf ~/.cache/codeverse3d                   # harness cache — see below
rm -rf harness/3dcodeverse.egg-info harness/.pytest_cache harness/.ruff_cache
```

`~/.cache/codeverse3d` (or `cache_dir` / `C3D_CACHE_DIR`) holds only regenerable things:
`renders/` and `judge_images/` (render + montage cache), `gpu_probe.json`, `browser_*.lock` (the
shared headless-Chrome endpoint file — do not delete it while renders are running).

**Your data is not in either cache.**  `runs/`, `eval/bench/out/` and any exported dataset are
the flywheel output — delete them deliberately, never as part of a cleanup.

Uninstalling does not touch `~/.config/3dcodeverse/config.yaml`,
`~/.config/astra3d/gemini_keys.env` or the globally installed CLIs — remove those by hand
(`npm rm -g @anthropic-ai/claude-code @openai/codex`; agy: `rm ~/.local/bin/agy` and the
`# Added by Antigravity CLI installer` PATH lines in `~/.bashrc` / `~/.profile`).
