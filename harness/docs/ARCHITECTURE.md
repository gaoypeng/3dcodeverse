# 3dcodeverse — architecture

`harness/` is a plugin for Claude Code, Codex and Antigravity.  The HOST agent drives
everything — plan, write raw code, build, look, judge, refine — and the plugin gives it three
things: **skills** (the workflow, a measured contract per language, craft bundles), a **judge
subagent**, and the **`3dcode` instruments** (build/export, measurement, renders,
deterministic gates, judge material, score arithmetic).  Python 3.13 (`codeverse3d`), a small
node runtime (`runtime_js/`) for everything Three.js / headless Chrome, moderngl for the
graphics track.

## 0. What it is

LLM-written **raw executable 3D code** — no SDK layered on the language — for four tracks:

| track | languages | deliverable |
|---|---|---|
| `static_object` | `blender` (bpy), `cadquery`, `threejs` | code + `artifacts/object.glb` (+ stl / step) |
| `articulated_object` | `urdf_blender` (bpy link meshes + hand-written URDF) | code + `robot.urdf` + `meshes/*.glb` + `object.glb` |
| `scene` | `scene_threejs` (multi-file three.js + GLSL), `scene_blender` (multi-file bpy) | `src/**` + `public/assets/*.glb` + renders + probes |
| `graphics` | `glsl_shader`, `opengl_python` | source + judged frames + sheet + GIF + frame metrics |

## 1. Design laws

1. **Code is truth; artifacts are derived.**  `src/` is the deliverable; every GLB, PNG and
   URDF is regenerated from it.  Every round is a git commit of `src/`.
2. **Requirements, never templates.**  A skill states what must be true, where, and how it is
   measured — interface, sandbox, thresholds — never a mandated shape or structure the brief
   did not ask for; ONE executable interface example per language; no fence over ~12 lines.
3. **Code answers when code can answer.**  Deterministic gates and measurements run in code;
   the model perceives and judges; the score is computed from rubric weights, penalties, floors
   and caps.
4. **Responsibility out of agent code**: export wrappers, camera rigs, placement validation,
   URDF composition math, scene assembly, GLSL headers are the plugin's.
5. **Evidence-based acceptance.**  Every plan carries an acceptance checklist; the judge
   answers each item from renders or measurements.
6. **Typed everything.**  One contracts package; no regex-on-id control flow; no god files
   (2 000 lines, 3 000 absolute); a merge must delete code.
7. **Cheap first.**  Lint → build → gates → montaged views → the judge.
8. **Generator and judge are separate contexts.**  The judge sees `brief.md` and images only.
9. **Export as authored.**  No wrapper re-centres or grounds; the gates measure where the
   agent put things and print the translation that fixes it.
10. **Each rule in ONE place.**  Flow and wording live in the skills; a number the code
    measures is stated where the agent reads it, in the words of the gate that measures it.

## 2. Package map

```
harness/
  .claude-plugin/plugin.json  .codex-plugin/plugin.json  plugin.json     the three manifests (name 3dcodeverse)
  bin/3dcode                launcher: $C3D_PYTHON | .venv/bin/python | python3, PYTHONPATH=<plugin>, python -m codeverse3d.cli
  skills/3d-director/       the workflow + references/plan-<track>.md (what plan.json holds per track)
  skills/3d-lang-<lang>/    one per language: files, frame, sandbox, thresholds + references/interface.md
  skills/c3d-*/             craft bundles (optional practices for one defect class each)
  agents/3d-judge.md        the judge subagent
  codeverse3d/
    cli.py                  `3dcode`: argparse generated from the tool registry's pydantic arg models
    config.py               Settings (binaries, limits, render) via C3D_*; resolve_blender()
    conventions.py          THE frames / units / naming / view presets / thresholds shared by code
    doctor.py               python -m codeverse3d.doctor
    contracts/              data-only models: Spec, Plan (Static / Articulated / Scene / Graphics), artifacts (BuildResult, GateReport, Measurement)
    languages/<lang>/       runtime per language: lint, build, wrappers (the exporters), starter files
    spatial/                measure, connectivity, contract, sections, sheet, render (three.js / Blender / moderngl), joints_*, scene_placement, probes, registry + tools
    gates.py                the per-track gate assembly and judged-view rig (`check`, `judge-pack`)
    judges/rubrics/*.yaml   the rubrics (criteria, anchors, defects, caps); rubrics.py parses a judgment and scores it
    judges/prompt_builder.py  montage / crop planning and the brief
  runtime_js/               three@0.182 + puppeteer: render_glb.mjs, the scene runner, the post chain, resolve_three.mjs
  setup.sh  pyproject.toml  README.md  CLAUDE.md  docs/
```

## 3. The workspace

A 3D project is a directory.  Every instrument takes `--workspace DIR` (absolute; default
`$C3D_WORKSPACE`, else the cwd), handled once in the CLI; `spec.json` is re-read on every call.

```
spec.json        track, language, prompt, constraints {must_have, must_not, dimensions_m, style}, references, fixed_parts, checks
plan.json        the agent's plan, validated and normalised by `validate-plan`
src/             the agent's raw code (scene: public/ for compiled assets)
artifacts/       object.glb / meshes/, renders/, measurement.json, build.json, gates.json — instrument output, read-only
judge/rNN/       brief.md + montages (judge-pack) · judgment.json (the judge) · score.json (score)
```

## 4. The instruments (`3dcode <subcommand> --workspace DIR [--flag value]`)

Every call prints the verdict first (`PASS` / `FAIL` / `ERROR`), then numbers and fix hints,
then the absolute paths of the PNGs it wrote; `--json` prints the result as JSON.  Exit 0 =
the tool ran (a FAIL is exit 0); non-zero = it could not run.  Tool names and their arguments
come from the `@tool` registry (`spatial/registry.py`, `spatial/tools.py`): one definition →
python function + argparse subcommand (kebab-case; a list argument is a repeated flag).

| subcommand | tracks | does |
|---|---|---|
| `init --track --language --prompt [--must-have]* [--must-not]* [--dims k=v]* [--references path]*` | all | writes `spec.json`, the layout and the language's starter files; prints the entry files and the skill to read |
| `validate-plan` | all | validates `plan.json` against the track's plan model + the deterministic plan checks (placeholder echo, hard quality complaints, acceptance present, fixed-part clash); writes the normalised plan back; FAIL lists each error with its JSON path |
| `build` | all | lint + build with the language runtime, export, measure; `artifacts/build.json` |
| `check` | all | build + measure + every gate of the track → `artifacts/gates.json`; the round's verdict |
| `judge-pack --round N` | all | renders the judged views, post-render gates, composes the montages/crops, writes `judge/rNN/brief.md` + images; needs a prior `check` in the same state |
| `score --round N` | all | parses `judge/rNN/judgment.json` strictly, applies weights, penalties, floors and the gate caps from `gates.json` → `score.json`; prints overall, passed, caps, the improvement list |
| `measure [--parts]*` | object | bbox / extents, ground gap, per-part size / tris / islands |
| `check-contract` | object | built GLB vs `plan.json`: missing parts, bbox deltas, overall size, ground contact |
| `check-connectivity` | object | contact graph: floating parts (with the gap vector), interpenetration, stray islands |
| `cross-section [--axis x|y|z] [--at 0..1] [--parts]*` | object | a labelled section image + loops / area / hollow ratio |
| `render-views [--views]* [--mode shaded|wire|…] [--isolate]* [--explode f] [--size px]` | object | named camera views + a contact sheet |
| `render-sheet [--mode]` | object | the 14-view labelled contact sheet |
| `isolate --part NAME [--views]*` | object | one part alone + its measurement row |
| `compare-reference [--view] [--reference-index i] [--size px]` | object | a render beside a reference image |
| `joint-sweep [--joints]*` | articulated | every joint through its range + random combinations; overlap depths; limit poses rendered |
| `shader-probe` | scene_threejs | compiles every ShaderMaterial headlessly |
| `scene-probe` | scene | census, draw / triangle counts, console errors |
| `scene-views [--cameras authored|orbit|all] [--times]*` | scene | authored cameras and/or the overview rig; the frame table and measured motion |
| `check-placement [--rebuild]` | scene | every placed root: floating / sunken / unsupported / interpenetration with the metres to move it |
| `gl-probe [--t s]` | graphics | compile / import + one frame with stats |
| `gl-frames [--times]* [--width] [--height]` | graphics | the judged frames, per-frame metrics and the gl_frames gate |

## 5. Gates (what `check` runs per track; thresholds are stated verbatim in the `3d-lang-*` skills)

* **object tracks**: lint; `check_contract` (per-part and overall bbox within
  max(10 mm, 10 %) per axis, WARN / 3 × ERROR; ground within 10 mm WARN / 30 mm ERROR;
  footprint centring; `placeholder_part`); `check_connectivity` (contact ≤ 2 mm; penetration
  > 2 mm WARN, > 10 mm ERROR; floating ERROR; islands < 5 % WARN); `detail_budget` (triangle
  floor / ceiling and build time from parts × instances); fixed parts; project checks.
  Articulated adds the FK check, `RestPenetration`, `joint_sweep` and `motion_direction`.
* **scene**: `scene_placement`, `scene_probe` (census, budgets, world / atmosphere, drivers),
  shader preflight (three.js), then after rendering `scene_frames` (exposure, camera, content)
  and `frame_motion`, `hero_unseen`.
* **graphics**: lint; `gl_frames` (NaN, black / blown, static, flicker, low detail).

## 6. Judging

`judge-pack` renders the track's judged views (objects: the 14-view rig + detail and contact
crops; articulated: plus the pose sheet; scenes: every authored camera at t = 0 and 1.5 s plus
the overview rig; graphics: the frame sheet), composes labelled 2 × 2 montages, and writes
`brief.md`: the request, the plan digest, the acceptance items, the measured numbers and gate
facts, the rubric's criteria with anchors, the binary defect checklist with penalties and caps,
and the EXACT JSON the judge returns (`criteria`, `summary`, `strengths`, `issues`,
`improvement_plan`, `acceptance`, `defects`).  The `3d-judge` agent reads only that folder and
writes `judgment.json`.  `score` parses it strictly (`judges/rubrics.py`), fills measured
criteria from code, applies weights, subtracts defect penalties, applies floors, then the caps
from `gates.json` (build error 0; floating 0.60; penetration 0.70; contract violation 0.75;
an unmet `must` acceptance item 0.60 / 0.70 on graphics; the per-track measured caps), and
prints the improvement list with gate ERRORs first.  Rubrics: `static_object`, `articulated`,
`scene`, `shader`, `asset`, `reference` under `judges/rubrics/`.

## 7. Known limits

* The judge is the host's model; its calibration is the host's.  The rubrics were calibrated
  on Gemini-class judges (the historical numbers live in the flywheel's docs).
* `scene_threejs` heroes (`blender_glb` assets) are built in a separate `static_object`
  workspace and copied into `public/assets/`; nothing automates that.
* No run record, cost or telemetry is kept here; `flywheel/` reads old records.
