# Installing 3dcodeverse

## 1. TL;DR

```bash
git clone <repo> 3dcodeverse && cd 3dcodeverse
bash harness/setup.sh                 # python package (`3dcode`) + runtime_js + headless Chrome + doctor
python -m codeverse3d.doctor          # what was found: python, node, Chrome/WebGL, Blender, cadquery, moderngl, GPU
```
`setup.sh` is idempotent: `pip install -e 'harness[all]'`, `npm ci` in `harness/runtime_js`
only when the lockfile moved, puppeteer's Chrome when its cache is missing, then doctor.
Options: `--extras LIST`, `--python PATH`, `--no-python`, `--no-node`, `--no-chrome`,
`--no-doctor`, `--no-gpu`, `--force-npm` (`npm ci` deletes `node_modules/` first — never while
a build is running).  By hand:
```bash
python -m venv .venv && source .venv/bin/activate      # python 3.13
pip install -e 'harness[all]'
(cd harness/runtime_js && npm ci && npx puppeteer browsers install chrome)
```

Then install the plugin into each host you use (§4).

## 2. Prerequisites

| need | required? | without it |
|---|---|---|
| **Python 3.13** + pip + venv | yes | nothing runs (`requires-python = ">=3.13"`).  Debian/Ubuntu: `sudo apt install python3-venv python3-pip`, then a virtualenv (PEP 668) |
| **node ≥ 20.6** | yes, except for the `graphics` track | the `threejs` / `scene_threejs` languages disappear and NO object renders happen: every GLB (Blender- and CadQuery-built too) is rendered with three.js in headless Chrome |
| **Blender 5.2 LTS** | for `blender`, `urdf_blender`, `scene_blender` | those languages raise `BlenderNotFoundError`; `articulated_object` cannot build |
| **cadquery** (extra `cad`) | for `cadquery` | that language is unavailable |
| **moderngl** (extra `graphics`) | for `glsl_shader`, `opengl_python` | the `graphics` track is unavailable |
| **EGL-capable GPU stack** | optional | headless Chrome falls back to SwiftShader and moderngl to llvmpipe — same results, several times slower |

Disk: ~100 MB `runtime_js/node_modules`, ~640 MB puppeteer Chrome, ~1 GB a Blender tarball.
Extras: `cad` (cadquery), `urdf` (yourdfpy, scipy), `graphics` (moderngl), `mesh` (shapely,
networkx, matplotlib — cross-section area, mesh split, judge slices); `all` is every one.
`doctor` reports a missing extra's module as WARN naming the extra; everything else missing is
FAIL.

### Blender
Blender 5.2 LTS is the one supported version (`BLENDER_MIN = (5, 2)` in `codeverse3d/config.py`).
```bash
curl -LO https://download.blender.org/release/Blender5.2/blender-5.2.2-linux-x64.tar.xz
tar xf blender-5.2.2-linux-x64.tar.xz && ~/tools/blender-5.2.2-linux-x64/blender --version
```
`Settings.resolve_blender()` takes `binaries.blender` from the settings file or
`C3D_BINARIES__BLENDER=/path/to/blender` first (a configured binary that is missing is a
FAIL, never replaced by PATH), else `blender-5.2`, then `blender` on PATH.  Headless Blender
without X11 libraries (no sudo): download the X11 `.so`s the tarball needs and put a one-line
wrapper named `blender-5.2` on PATH that sets `LD_LIBRARY_PATH` and execs the binary.  Every
Blender build runs `--factory-startup` in an emptied scene; `scene_blender`'s judged frames are
Cycles (32 samples + OpenImageDenoise, AgX, exposure 0), GPU when the doctor finds one.

### runtime_js
`harness/runtime_js/` is a small npm package the plugin owns (three@0.182, puppeteer); its
`node_modules/` is not committed.  Generated code never sees it: node builds get
`--import runtime_js/lib/resolve_three.mjs` (a `registerHooks` resolver for `three` and
`three/addons/*`), and the browser gets `/__runtime/node_modules/three/...` over the local
server.  Nothing is fetched from a CDN.  Use `npm ci`, not `npm install`.

### Headless Chrome and the GPU
On WSL2 WebGL uses the GPU with `--use-angle=gl-egl` plus the Mesa d3d12 environment
(`runtime_js/gpu_launch.cjs`); `C3D_RENDER_GPU=on|off|auto` forces it.  moderngl uses a d3d12
or EGL context, llvmpipe otherwise.  `doctor --no-gpu` skips the probe.

## 3. Settings
`~/.config/3dcodeverse/config.yaml` or `./3dcodeverse.yaml`; every field is also an
environment variable with the `C3D_` prefix and `__` for nesting (`C3D_BINARIES__BLENDER`,
`C3D_LIMITS__BUILD_TIMEOUT_S=300`).  `python -m codeverse3d.doctor --json` prints what is in
effect.  Nothing in the plugin needs an API key.

## 4. Installing the plugin into a host
The repository root is a marketplace (`.claude-plugin/marketplace.json`) whose one plugin is
`harness/`.
```bash
# Claude Code
claude plugin marketplace add /path/to/3dcodeverse        # or the GitHub owner/repo
claude plugin install 3dcodeverse@3dcodeverse
claude plugin details 3dcodeverse                         # skills, the judge agent, token cost
# Codex
codex plugin marketplace add /path/to/3dcodeverse
codex plugin add 3dcodeverse@3dcodeverse
# Antigravity
agy plugin install /path/to/3dcodeverse/harness
agy plugin validate /path/to/3dcodeverse/harness --strict
```
The host then sees the skills (`3dcodeverse`, `3d-lang-*`, `c3d-*`) and the `3d-judge`
subagent.  The skills run `3dcode …` in the host's terminal: the console script from
`pip install -e harness`, or `harness/bin/3dcode` (`$C3D_PYTHON`, else `harness/.venv/bin/python`,
else `python3`, with `PYTHONPATH=harness`).  To try a host without touching your real config,
point it at a temporary home (`HOME=/tmp/x`, `CODEX_HOME=/tmp/x/.codex`,
`CLAUDE_CONFIG_DIR=/tmp/x/.claude`).

## 5. Doctor troubleshooting
* `python FAIL` — not 3.13: make a venv from a 3.13 interpreter and rerun `setup.sh --python`.
* `node FAIL` — below 20.6, or `runtime_js/node_modules` missing: `npm ci` in `runtime_js/`.
* `chrome FAIL` — `npx puppeteer browsers install chrome` in `runtime_js/`.
* `blender FAIL` — nothing on PATH named `blender-5.2` / `blender`, or an older version, or a
  configured binary that is not executable (§2).
* `gpu WARN` — SwiftShader / llvmpipe in use: slower, not wrong.
