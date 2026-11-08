# 3dcodeverse

**Version 0.0.8** · plugin for Claude Code, Codex and Antigravity

A plugin for **Claude Code, Codex and Antigravity (`agy`)** that makes the host coding agent
good at real 3D: it writes **raw, executable 3D code** — Blender `bpy`, CadQuery, Three.js,
URDF, GLSL, moderngl — and the plugin builds, measures, renders and gates what it wrote, then
has a fresh-context judge score it.

* **Tracks:** `static_object` · `articulated_object` · `scene` · `graphics`
* **Languages:** `blender` · `cadquery` · `threejs` · `urdf_blender` · `scene_threejs` ·
  `scene_blender` · `glsl_shader` · `opengl_python`
* **Skills** (`skills/`): `3dcodeverse` (the controller: what 3D coding here is, the tracks
  and languages, every instrument, the workflow init → plan → code → check → judge → refine
  with its stop rules, and the quality rules every track shares), `3d-lang-<language>` × 8
  (each language's files, frame, sandbox and every threshold the instruments measure) and the
  `c3d-*` craft bundles (parts that touch, bbox contract, repeats, scene lighting / placement /
  materials / motion, shader traps …).
* **Instruments** (`3dcode`, a command-line tool): `init`, `validate-plan`, `build`, `check`
  (every gate of the track), `judge` (commit + tag + `judge-pack` + the judge's instruction),
  `score`, and the per-track probes — `measure`,
  `check-contract`, `check-connectivity`, `cross-section`, `render-views`, `render-sheet`,
  `isolate`, `compare-reference`, `joint-sweep`, `scene-probe`, `check-placement`,
  `scene-views`, `shader-probe`, `gl-probe`, `gl-frames`.  Each prints a verdict, numbers,
  fix hints and the paths of the PNGs it wrote.
* **Judge** (`agents/3d-judge.md` for Antigravity, `claude-agents/3d-judge.md` for Claude Code —
  the same body, each host's tool names): a subagent started with the one instruction `3dcode judge`
  prints; it reads only `judge/rNN/brief.md` + the images and writes `judgment.json`; `3dcode
  score` does the arithmetic from the rubric's weights, penalties, floors and gate caps.

Everything model-side — writing code, looking at renders, judging — is the host agent's own
model and billing.  There is no pipeline, no vendor backend, no API key and no cost ledger in
this package.

## Install

```bash
git clone <this repo> 3dcodeverse && cd 3dcodeverse
bash harness/setup.sh                       # python package (console script `3dcode`) + runtime_js + headless Chrome + doctor
python -m codeverse3d.doctor                # deps, Blender, node, GPU

claude plugin marketplace add .             # Claude Code: the repo is a marketplace
claude plugin install 3dcodeverse@3dcodeverse
codex plugin marketplace add .              # Codex
codex plugin add 3dcodeverse@3dcodeverse
agy plugin install harness                  # Antigravity
```

Prerequisites and troubleshooting: `docs/INSTALL.md`.  Python 3.13, node ≥ 20.6; Blender
5.2 LTS for the `blender` languages; CadQuery and moderngl as extras.

## Use

Ask the host agent for a 3D thing and name the language if you care:

> Make a mid-century wooden dining chair in Blender bpy.
> Make a kitchen cabinet with one door and a drawer as a URDF mechanism.
> Make a small Japanese garden at dusk with a koi pond as a three.js scene.
> Make neon rain on a window as a GLSL shader.

The `3dcodeverse` skill drives the rest: a workspace directory with `spec.json`, `plan.json`,
`src/` (the code), `artifacts/` (object.glb, renders, gates) and `judge/rNN/` (brief, montages,
judgment, score).  By default one round is generated and checked; a score and refine rounds
happen when you ask for them ("judge it", "refine it twice"), and then the best-scoring round is
the deliverable.

Docs: `docs/INSTALL.md` (install / prerequisites / doctor) · `docs/ARCHITECTURE.md` (design,
the workspace, every instrument and gate, judging) · `CLAUDE.md` (working rules for changing
the plugin).
