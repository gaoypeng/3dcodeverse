# 3dcodeverse — standing context

`harness/` IS the plugin (python package `codeverse3d`, console script `3dcode`, skills under
`skills/`, the judge under `agents/` (agy) and `claude-agents/` (Claude Code), manifests
`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `plugin.json`; the repo root's
`.claude-plugin/marketplace.json` lists it).  The two judge files differ ONLY in their
frontmatter — agy needs `tools: [read_file, write_file]` in its own format (no `tools` key gives
a read-only default; an unknown name like `Read` fails the spawn), Claude Code needs
`tools: Read, Write` and reads agents only from the manifest's `"agents"` list; neither host can
include the other's file, so the body below the frontmatter is kept byte-identical by hand (edit
one, copy the body to the other).  Codex has no plugin agents: its subagent gets the printed
`JUDGE INSTRUCTION`, whose last clause points it at `agents/3d-judge.md`.  It installs into Claude Code, Codex and Antigravity and boosts the HOST agent's 3D
ability: the host writes raw Blender bpy / CadQuery / Three.js / URDF / GLSL / moderngl code
in a workspace, the `3dcode` instruments build, measure, render and gate it, a fresh-context
judge subagent scores the renders.  No pipeline, no stage graph, no round loop, no vendor-CLI
backends, no model API clients, no API keys, no cost ledger, no run records: flow and wording
live in the skills, each rule in ONE place; code keeps only what a model cannot do or cannot do
reliably — real execution, measurement, deterministic checks, score arithmetic.  Old behaviour
is in releases v0.0.0 / v0.0.1; the flywheel is the separate `flywheel/` package.

Read `docs/ARCHITECTURE.md` (design laws, package map, the workspace, every instrument, the
gates, judging) and `docs/INSTALL.md` before changing anything.

## Laws (do not break)
1. Generated code is raw language — never an SDK/helper import; the plugin owns
   wrappers/exporters (`codeverse3d/languages/wrappers/`, `languages/opengl_python/wrappers/`,
   `runtime_js/`).  The one exception the skills name: `scene_threejs`'s harness-owned
   `src/lib/*.js`, copied into the workspace.
2. `codeverse3d/contracts/` is data-only and shared; `conventions.py` is the only place that
   states frames/units/naming in code.  Import, never restate.  A skill may state a frame to
   the agent, who cannot import it — the number must agree with `conventions.py`.
3. Deterministic gates/measurements run in code; a model only perceives and judges.  The score
   is computed in code from rubric weights; caps/floors are explicit.
4. Typed everything; no regex-on-id control flow; cap **2 000 lines** per file, **3 000**
   absolute.  A merge must DELETE code, not move it.
5. Cheap first: lint → build → gates → montaged views → the judge.
6. Requirements, never templates: a skill keeps interface, sandbox rules and measured
   thresholds, ONE executable interface example per language under `references/`, no code
   fence over ~12 lines, no budget or cost wording, no recipes the brief did not ask for.
7. No wrapper re-centres or grounds the object: export **as authored**, warn in the build, let
   `check-contract` / `check-connectivity` gate it.
8. The judge is a FRESH context that sees `judge/rNN/brief.md` and the images, never the
   builder's code or reasoning; `3dcode score` is the only thing that turns a judgment into a
   number.

## Supported versions
python **3.13** (one fixed version) · node **20.6+** · Blender **5.2 LTS** only · Linux x86_64.
There is no CI: run `ruff check codeverse3d` and the smoke scripts under
`/home/yipeng/3dcodeverse_tests/plugin_smoke/` (outside the repo) before every push, with
`set -o pipefail`.

## Environment (this machine)
- Blender 5.2.2 LTS headless: `~/.local/bin/blender-5.2` (always `--factory-startup`).  The
  judged-frame fast path is Cycles CUDA + GPU OIDN + persistent data; OptiX is unavailable under
  WSL2 and probing it crashes Blender — never enumerate it.
- Node 24 (floor 20.6) + `runtime_js/node_modules` (three@0.182, puppeteer; chrome cached).
  Headless Chrome WebGL uses the GPU on WSL2 with `--use-angle=gl-egl` + Mesa d3d12 env
  (`runtime_js/gpu_launch.cjs`; `C3D_RENDER_GPU=on|off|auto`); SwiftShader fallback.  Bare ESM
  `import 'three'` needs `--import runtime_js/lib/resolve_three.mjs`
  (`spatial.node.run_node(three_hook=True)`).  The `graphics` track renders with moderngl
  (d3d12 GPU context, llvmpipe fallback).
- Settings: `~/.config/3dcodeverse/config.yaml` or `./3dcodeverse.yaml`, env prefix `C3D_`;
  every switch is a `Settings` field read through `get_settings()` — never read the
  environment by hand.
- Hosts: `agy`, `claude`, `codex` are installed; test plugin loading only in a TEMP home
  (`HOME=/tmp/x`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`) — never touch the real `~/.claude`,
  `~/.codex`, `~/.gemini`.

## Commands
```
bash setup.sh                                   # python package + runtime_js + chrome + doctor
python -m codeverse3d.doctor [--json]
3dcode init --workspace W --track static_object --language blender --prompt "a dining chair" [--must-have ...] [--dims height=0.45] [--references img.png]
3dcode validate-plan --workspace W
3dcode build | check | judge-pack --round N | score --round N      (always --workspace W; --json for JSON)
3dcode measure | check-contract | check-connectivity | cross-section | render-views | render-sheet | isolate | compare-reference | joint-sweep
3dcode scene-probe | check-placement | scene-views | shader-probe | gl-probe | gl-frames
agy plugin validate . --strict --json           # the plugin layout (skills, agents, manifest)
claude plugin marketplace add <repo>; claude plugin install 3dcodeverse@3dcodeverse   # in a temp CLAUDE_CONFIG_DIR
codex plugin marketplace add <repo>; codex plugin add 3dcodeverse@3dcodeverse         # in a temp CODEX_HOME
ruff check codeverse3d
```
