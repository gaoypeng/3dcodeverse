---
name: 3d-director
description: "Use whenever the user wants a 3D thing made with code - an object, a mechanism with joints, a scene, a shader or an OpenGL effect - in Blender bpy, CadQuery, Three.js, URDF, GLSL or moderngl, or wants one measured, judged or improved. The whole 3dcodeverse workflow: init a workspace, write plan.json and validate it, write raw code, run the 3dcode instruments (build, gates, renders) until they pass, pack the judge material, have a FRESH-context judge score it, refine from the score, keep the best round. Read it before the first 3dcode call."
license: Apache-2.0
metadata:
  verified: "2026-11-07"
---

# Directing a 3dcodeverse project

You write RAW code (bpy / CadQuery / three.js / URDF / GLSL / moderngl) in a workspace; the
`3dcode` command-line instruments build, export, measure, render and gate it; a judge who never
sees your code scores the renders; `3dcode score` turns that into a number and an improvement
list.  Your job is the loop below, honestly.

## The instruments

Every call is `3dcode <subcommand> --workspace <abs dir> [--flag value ...]`.  Always pass
`--workspace` (the absolute path).  The first line of the output is the verdict — `PASS`,
`FAIL` or `ERROR` — then the numbers and fix hints, then the absolute paths of any PNGs it
wrote: OPEN those images with your file viewer and look, every time.  Exit 0 means the tool
ran (a FAIL is exit 0); non-zero means it could not run.  `--json` gives the same result as
JSON.  A list flag is repeated (`--views front --views top`); `3dcode <subcommand> --help`
lists a tool's flags.  If `3dcode` is not on PATH, run `<plugin root>/bin/3dcode`.

| subcommand | what it does |
|---|---|
| `init` | writes `spec.json`, the workspace layout and the language's starter files |
| `validate-plan` | validates `plan.json` against the track's schema + plan checks; writes the normalised plan back |
| `build` | lint + build `src/`, export, measure (every language) |
| `check` | build + measure + EVERY gate of the track → `artifacts/gates.json`; the round's verdict |
| `judge-pack --round N` | renders the judged views, composes the montages, writes `judge/rNN/brief.md` + images |
| `score --round N` | scores `judge/rNN/judgment.json` with the rubric's weights, penalties, floors and gate caps → `score.json` |
| object tracks | `measure`, `check-contract`, `check-connectivity`, `cross-section`, `render-views`, `render-sheet`, `isolate`, `compare-reference`; articulated adds `joint-sweep` |
| scene track | `scene-probe`, `check-placement`, `scene-views`, `shader-probe` (three.js) |
| graphics track | `gl-probe`, `gl-frames` |

`spec.json` is re-read on every call; `plan.json` is what the gates measure against.

## The workspace

```
spec.json     the request (track, language, prompt, must_have, must_not, dims, references)
plan.json     your plan — exact numbers; validated before any code
src/          your raw code (scene track: also public/ for compiled assets)
artifacts/    instrument output: object.glb, renders/, gates.json, measurement.json — READ-ONLY
judge/rNN/    brief.md + montages (judge-pack), judgment.json (the judge), score.json (score)
```
Never write under `artifacts/` or `judge/` except the judge's `judgment.json`; never edit
`spec.json` after init or `plan.json` after it validated (a re-plan starts the rounds over).

## Tracks and languages

| track | languages | skill to read before writing code |
|---|---|---|
| `static_object` | `blender`, `cadquery`, `threejs` | `3d-lang-blender` / `3d-lang-cadquery` / `3d-lang-threejs` |
| `articulated_object` | `urdf_blender` | `3d-lang-urdf-blender` |
| `scene` | `scene_threejs`, `scene_blender` | `3d-lang-scene-threejs` / `3d-lang-scene-blender` |
| `graphics` | `glsl_shader`, `opengl_python` | `3d-lang-glsl-shader` / `3d-lang-opengl-python` |

Pick the track from what the user wants (a thing → static, a thing that moves on joints →
articulated, a place → scene, an animated image → graphics) and the language from their
request or the default of the row's first entry.  The language skill states the files, the
frame, the sandbox and every threshold the gates measure; the `c3d-*` skills hold optional
craft for a specific defect (parts that touch, bbox contract, repeats, scene lighting…) —
read one when its description matches your plan or a finding.

## The loop

1. **Init.**  `3dcode init --workspace W --track T --language L --prompt "..."` with
   `--must "..."` / `--must-not "..."` per hard requirement, `--dim height=0.45` per stated
   dimension, `--reference img.png` per reference image.  It prints the entry files and the
   skill to read.  Then `git -C W init` if the workspace is not a repository.
2. **Plan.**  Write `plan.json` by `references/plan-<track>.md` (exact numbers, acceptance
   checklist), then `3dcode validate-plan --workspace W` until it prints PASS; FAIL lists each
   error with its JSON path — fix the plan, never argue with the validator.
3. **Baseline round (r00).**  Write the code the language skill describes.  Run the language's
   loop (`build` → its gates → look at the renders) after every edit, and finish the round with
   `3dcode check --workspace W`: every ERROR it lists caps the score, so fix each before judging.
   Object tracks: the whole object first — every plan part at its box and attached to its
   parent before any part gets detail (a part still a ≤ 12-triangle box within 1 mm of its plan
   box is a `placeholder_part` ERROR); then detail.  Commit: `git -C W add -A src public 2>/dev/null; git -C W commit -qm r00`.
4. **Judge.**  `3dcode judge-pack --workspace W --round 0` writes `judge/r00/` (requires a `check`
   in the current state).  Start a FRESH subagent with the `3d-judge` agent definition and give
   it ONLY the folder path: it reads `brief.md` and the images and writes `judgment.json`.  Tell
   it nothing else — not what you built, not what you think of it.  Without subagents, do the
   judging yourself as a clearly separated step after writing nothing else into context:
   open `brief.md`, look at every image, answer exactly its JSON.  Then
   `3dcode score --workspace W --round 0`: it prints overall, passed, the caps applied and the
   improvement list.
5. **Refine rounds (r01, r02 …; at most 2 unless the user sets another number).**  Each
   round makes exactly the score's improvement list, gate ERRORs first (`[gate/...]` items), as
   targeted edits — change the lines a task needs, keep every other part, name and dimension;
   a measured finding that repeats needs a different change of the thing it measures, not the
   same edit again; a judge note that repeats needs a visible change in the named view.  Then
   `check` → commit `rNN` → `judge-pack --round N` → the judge → `score --round N`.
6. **Stop** when the rounds are used up, when `score` says passed with an empty improvement
   list, or when the overall fell two rounds running.  The deliverable is the BEST-scoring
   round: if the last round is not it, `git -C W checkout rNN -- src` (and `public`) and run
   `3dcode check` once more so `artifacts/` matches.  Report the round, its overall, the caps
   and the paths of its montages.

## Repairs

A build failure is fixed with the smallest correct change at the `file:line` the output names:
a runtime error → the call or import (the language skill's allow-list is the only set of
modules); a lint error → the construct the fix hint names; a syntax error → that line; a
timeout or memory blow-up → fewer segments or loop counts.  Keep every part, name and
dimension.  The same error twice means the edit missed the cause: change a different call,
not different arguments.  Never finish a round on a broken build.

## Honesty

* One code path: no detection of headless mode, user agent or the instruments.
* Never special-case a check to make it pass — no hidden parts, zero-scaled parts, invisible
  filler geometry, connector sticks bridging a gap; fix the geometry the finding names.
* No fake success, no swallowed exceptions around geometry (the one exception is a cosmetic
  fillet/chamfer falling back to the unrounded solid when the kernel refuses it).
* Never import a helper SDK, the plugin's own package or `runtime_js/`; harness-owned modules a
  language skill names inside `src/` (scene_threejs `src/lib/*.js`) are the one exception.
* Never write or edit `judgment.json`, and never judge a round you built in the same context.
