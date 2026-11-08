---
name: 3dcodeverse
description: "Use for ANY request to make, measure, judge or improve a 3D thing in code: a model, object or prop, a mechanism or robot with joints, a scene or environment, a shader or OpenGL effect — in Blender bpy, CadQuery, Three.js, URDF, GLSL or moderngl. This is 3D coding with the 3dcodeverse plugin: raw executable 3D code that the 3dcode instruments build, measure, render, gate and score. First step, all in ONE read: this file, skills/3d-lang-<language, hyphenated>/SKILL.md and skills/3dcodeverse/references/plan-<static|articulated|scene|graphics>.md (urdf_blender: also skills/3d-lang-blender/SKILL.md and plan-static.md, which it builds on); no c3d-* skill until a 3dcode finding or score item names its topic."
license: Apache-2.0
metadata:
  verified: "2026-11-07"
---

# 3D coding with 3dcodeverse

This is 3D CODING.  You write raw, executable 3D code — bpy / CadQuery / three.js / URDF / GLSL /
moderngl — in a workspace; the `3dcode` instruments build, export, measure, render and gate it;
a judge who never sees your code scores the renders; `3dcode score` turns the verdict into a
number and an improvement list.  Nothing is drawn by hand and nothing comes from an asset
store: the object, mechanism, place or effect has to come out of the code at real scale, and it
is measured.  Your job is the loop below, honestly.

**Read three files together, in ONE step, before anything else** — all under the plugin root, beside
this file: this skill's `## The loop` (the rest of this file is reference), the language skill
`skills/3d-lang-<language>/SKILL.md` (hyphens: `scene_threejs` → `3d-lang-scene-threejs`; the
table below names each) and `skills/3dcodeverse/references/plan-<track>.md`.  Open no other
reference (`c3d-*` skills, a language skill's `references/`) unless a finding needs it.

## Tracks and languages

| track | what it is | languages (default first) | read before writing code | plan reference |
|---|---|---|---|---|
| `static_object` | one thing that does not move — furniture, tools, props, a vehicle at rest | `blender`, `cadquery`, `threejs` | `3d-lang-blender` / `3d-lang-cadquery` / `3d-lang-threejs` | `references/plan-static.md` |
| `articulated_object` | a mechanism judged while it moves — doors, drawers, lids, wheels, linkages, robots | `urdf_blender` | `3d-lang-urdf-blender` | `references/plan-articulated.md` |
| `scene` | a place — terrain or room, sky and light, many placed things, authored cameras, something moving | `scene_threejs`, `scene_blender` | `3d-lang-scene-threejs` / `3d-lang-scene-blender` | `references/plan-scene.md` |
| `graphics` | an animated image — a fragment shader or a moderngl program, judged on frames | `glsl_shader`, `opengl_python` | `3d-lang-glsl-shader` / `3d-lang-opengl-python` | `references/plan-graphics.md` |

Pick the track from what the user wants and the language from their words, else the row's
first.  The language skill states the files, the frame, the sandbox and every threshold the
gates measure; the `c3d-*` skills hold optional craft for one defect class each (parts that
touch, the bbox contract, repeats, scene lighting / placement / water, shader traps …) — read
one when its description matches your plan or a finding.

## The instruments

Every call is `3dcode <subcommand> --workspace <abs dir> [--flag value …]`.  Always pass
`--workspace` (the absolute path).  The first line of the output is the verdict — `PASS`,
`FAIL` or `ERROR` — then the numbers and fix hints, then the absolute paths of any PNGs it
wrote (open one when the loop below says to look).  Exit 0 means the tool
ran (a FAIL is exit 0); non-zero means it could not run.  `--json` gives the same result as
JSON.  A list flag is repeated (`--views front --views top`); `3dcode <subcommand> --help`
lists a tool's flags.  If `3dcode` is not on PATH, run `<plugin root>/bin/3dcode`.

| subcommand | what it answers | when |
|---|---|---|
| `init` | writes `spec.json` (track, language, prompt, must-haves, dims, references, style) and the workspace layout; prints the entry files | only to create a project before its plan exists — the first `validate-plan` does it otherwise |
| `validate-plan` | is `plan.json` a valid plan for the track — schema, plan checks, the acceptance items derived from `spec.json`; writes the normalised plan back (`scene_blender`: also `src/scene.py`) | after every plan edit, until PASS |
| `build` | does `src/` lint, build and export; measures the result | after every code edit |
| `check` | build + EVERY gate of the track → `artifacts/gates.json`; PASS/FAIL with each ERROR and its fix — the round's verdict (it subsumes `build`, `check-contract` and `check-connectivity`) | before judging; every ERROR caps the score |
| `judge --round N` | the round's hand-over in one call: commits `src/` + `plan.json` tagged `rNN` (re-running `check` when the gates are stale), runs `judge-pack`, prints the folder and the `JUDGE INSTRUCTION:` line for the judge subagent | after a clean `check`, once per round |
| `judge-pack --round N` | renders the judged views, composes the montages and crops, writes `judge/rNN/brief.md` + images for a fresh-context judge | inside `judge`; alone only to re-pack |
| `score --round N` | the rubric arithmetic on `judgment.json`: overall, passed, caps, the improvement list → `score.json`; repairs a trivial deviation in the judgment and says what it repaired | after the judge replied |
| `measure` | overall bbox, ground gap, per-part size / triangles / islands in Y-up metres (`--parts`) | when a number, not a picture, answers it |
| `check-contract` | the built object vs `plan.json`: missing parts, bbox delta per part, overall size, ground contact | object tracks, after every build |
| `check-connectivity` | the contact graph: floating parts with the gap vector that closes them, interpenetration, stray islands | object tracks, after every build |
| `cross-section` | a labelled slice (`--axis x|y|z --at 0..1 --parts`) with loops, area and hollow ratio | when `check` or a score item says a cavity, wall or part position is wrong — not as a routine look |
| `render-views` | named views (`--views`, `--mode shaded|wire|normals|silhouette|clay`, `--isolate`, `--explode`) | to see one aspect |
| `render-sheet` | the labelled 14-view contact sheet (`--mode`) | the refine round's one look, to confirm a listed item |
| `isolate --part X` | one part alone + its measurement row | where did that fragment come from; where is this part |
| `compare-reference` | a reference image beside a render with the outline diff and IoU (`--reference-index`, `--view`) | after the first clean build and after every shape change when `spec.json` has references |
| `joint-sweep` | every joint through its range: overlap, unattached, buried per link pair with the fix, plus pose renders (`--joints` renders fewer) | articulated, after every build |
| `scene-probe` | the scene census: objects, materials, lights, draw calls, triangles, console errors, world and atmosphere | scene, for the budget and the world |
| `check-placement` | per placed thing: gap under its feet, burial, support, interpenetration with the metres to move it; bounds, scale, zone contents (`--rebuild` probes again) | scene, after every build — read its rows (gap + tolerance), never `artifacts/scene_probe.json` (the raw dump) |
| `scene-views` | the authored cameras and/or the orbit rig (`--cameras authored|orbit|all`, `--times`): sheet + frame table + MEASURED MOTION per camera | scene exposure, cameras, motion — those numbers, not your impression; the refine round's one look |
| `shader-probe` | compiles every ShaderMaterial headlessly; errors with line numbers | scene_threejs, before a material renders black |
| `gl-probe` | compile / import and ONE frame (`--t`, default 1 s): errors with source lines, or the frame + its stats | graphics, after every edit |
| `gl-frames` | the judged frames (`--times`, `--width`, `--height`) as a sheet + per-frame metrics + the `gl_frames` gate | graphics; `check` runs it — alone only as the refine round's one look |
| `list` · `doctor` | every tool with its arguments · the machine's deps (Blender, node, GPU) | when a tool or a build is missing |

`spec.json` is re-read on every call; `plan.json` is what the gates measure against.

## The workspace

```
spec.json     the request (track, language, prompt, must_have, must_not, dims, references)
plan.json     your plan — exact numbers; validated before any code
src/          your raw code (scene track: also public/ for compiled assets)
artifacts/    instrument output: object.glb, renders/, gates.json, measurement.json — READ-ONLY
judge/rNN/    brief.md + montages (judge-pack), judgment.json (the judge), score.json (score)
```
* Only code goes in `src/` — no notes, READMEs or reports; install nothing, fetch nothing.
* Never write under `artifacts/` or `judge/` except the judge's `judgment.json`; never edit
  `spec.json` after init; never edit `plan.json` after it validated (a re-plan starts the
  rounds over).  The one exception is a camera finding: prefer moving the subject (a zone's
  placement, the ground under the eye); when the camera itself must move, edit `cameras[]`
  only and re-run `3dcode validate-plan` — that is not a re-plan.

## The loop

**Editing rule.**  After a file's first write, change it only with the host's edit tool, scoped
to the lines the change needs; a large change is several edits, never a whole-file rewrite.
Keep files small and modular so edits stay local — one part / zone / asset / layer per file, as
the language skill's `## Files` allows.  Do not re-read a file you just wrote or edited.

**Commands run in the foreground; wait for them.**  Every `3dcode` call finishes on its own (a
scene `check` or `judge` can take a minute or two): give each `3dcode` command a wait of up to
five minutes (300 000 ms) so it finishes in the same step instead of moving to the background.
Never poll a running command or subagent; its result comes back to you.

**How many rounds is the user's call.**  By default there is ONE round: plan, write the code,
`3dcode check` until it lists no ERROR, report — no judge, no render-look-edit cycles.  Judge
(steps 4–5) only when the user asks for a score or for refine rounds, and run exactly the
number of refine rounds the user asked for.  **No self-polishing:** the judge decides what to
polish and `score` lists it; you do not iterate on looks between judges.  A refine round fixes exactly what the last `score` listed (gate
ERRORs first, then the judge's items), runs `3dcode check` until no ERROR, then `3dcode judge`
— at most ONE look at renders (`render-sheet` / `scene-views` / `gl-frames`) per refine round,
to confirm the listed items.  A WARN never caps the score: fix it once if cheap, otherwise
leave it.

1. **Plan.**  (There is no `spec.json` yet; step 2 creates it.)  Write `W/plan.json` by
   `references/plan-<track>.md` — exact numbers, an acceptance
   checklist, and SHORT: one sentence per part, one line of summary, the reference's example is
   the length (an object plan under 5 KB).
2. **Validate (and create the project).**  `3dcode validate-plan --workspace W --track T
   --language L --prompt "..."` with `--must-have "..."` / `--must-not "..."` per hard
   requirement, `--dims height=0.45` per stated dimension, `--references img.png` per reference
   image: the first call also creates the project (spec.json, layout, the language's own files)
   and prints its entry files.  Then `3dcode validate-plan --workspace W` until PASS; FAIL lists
   each error with its JSON path — fix the plan, never argue with the validator.  It says what it
   normalised; there is nothing to re-read.
3. **Baseline round (r00).**  Write every file the language skill describes from the plan in at
   most THREE write steps — several files per step (all the part / zone / layer files together),
   the entry file last — then `3dcode check --workspace W` and fix each ERROR it lists (a compile or runtime error at the
   `file:line` it names, see Repairs; a gate ERROR by its fix hint) until none is left — every
   ERROR caps the score.  When the user asked for a score, run `3dcode judge --workspace W --round 0`
   instead of each `check` after the code is written: it runs the check first and stops on any ERROR
   with the same output, so a clean round goes straight to step 4 without a separate `check`.  Object tracks: the whole object first — every plan part at its box and
   attached to its parent before any part gets detail (a part still a ≤ 12-triangle box within
   1 mm of its plan box is a `placeholder_part` ERROR); then detail.  No ERROR → the round is
   done: without a judge request, go to step 6.
4. **Judge (only when the user asked for a score or for refine rounds).**  `3dcode judge --workspace W --round 0`: it commits and tags `r00`, writes
   `judge/r00/` and prints a line starting `JUDGE INSTRUCTION:`.  The judge is a context that
   has not seen your work: start a FRESH subagent with the `3d-judge` agent definition and hand
   it that instruction VERBATIM — nothing else: not what you built, not what you think of it.
   It reads `brief.md` and the images, writes `judgment.json` and replies `wrote <path>`; wait
   for that reply.  Only when the host cannot start any fresh context, judge it yourself as a
   DEGRADED verdict — a clearly separated step after writing nothing else into context: open
   `brief.md`, look at every image, answer exactly its JSON — and mark the round "self-judged"
   in your report.  Then `3dcode score --workspace W --round 0`: it prints overall, passed,
   the caps applied, what it repaired in the judgment and the improvement list — each item names
   its target, its tile and the evidence, which is all a fix needs.  Do NOT open the judge's
   montages after `score`; the refine round's one look is the `render-sheet` (or the track's
   `scene-views` / `gl-frames`) after your edits.  Never list or guess names in `judge/`, never
   re-read `brief.md`.
5. **Refine rounds (r01, r02 …; exactly as many as the user asked for).**  Each round
   makes exactly the score's improvement list, gate ERRORs first (`[gate/...]` items), as
   edits under the editing rule, keeping every other part, name and dimension.
   A placement task changes that part's placement numbers to the plan's centre; a material task
   changes that part's colour / roughness / metalness only; a shape task rebuilds that part
   alone; on graphics a colour/light task touches palette, exposure and grade only, a motion
   task the time terms only — never rewrite unrelated layers or passes.  A measured finding
   that repeats needs a different change of the thing it measures, not the same edit again; a
   judge note that repeats needs a visible change in the named view.  Then `judge --round N`
   (it checks first and stops on any ERROR) until it packs, or `check` first when an item needs
   the round's one look at the renders → the judge → `score --round N`.
6. **Stop** when the rounds the user asked for are done, when `score` says passed with an empty
   improvement list, or when the overall fell two rounds running.  Without a judge the
   deliverable is r00; with one it is the BEST-scoring round: if the last round is not it, `git -C W checkout rNN -- src` (and `public`) and run
   `3dcode check` once more so `artifacts/` matches.  Report in AT MOST ten lines: each round's
   overall and caps (if judged), the entry files, the deliverable round and the paths of its montages —
   nothing more, no file-by-file list.

## Quality rules on every track

* **The plan's numbers are what the gates measure.**  Build them; if one looks wrong for the
  object, keep the plan's value in the code and say why in a comment — never drift silently.
* **Parts meet at their surfaces.**  Where one part goes INTO another (an axle in a hub, a fork
  in a head tube, a switch in its base, a leg in a seat) end it at the receiving surface or make
  the receiver hollow: more than 2 mm of overlap is measured, more than 10 mm is an ERROR.
* **Detail goes INSIDE the parts the plan names** — edges, profiles, seams, counted repeats —
  never into new parts: detail inside a part is neutral-to-positive for the score, an unplanned
  part is strongly negative (the contract gate reports it, the judge counts it as clutter).
* **Model only what a viewer sees from outside.**  Internals — springs in a housing, wiring,
  the underside of a lid that never opens — are never parts.
* **A material per part with the plan's colour**; a grey default anywhere is a judged defect.
* **Hollow things are hollow** when the real object is (a mug, bowl, box, drawer): cut the
  cavity in the code; open `3dcode cross-section` only when `check` or a score item says it is missing.
* **Never trim part names, counts, the overall size or the planned motion to "simplify"**;
  no placeholders, no unit-cube stand-ins, no static image where the plan says motion.
* **A render is evidence; "it should be fine" is not.**  The judge's montages are that evidence;
  on your one look per refine round check only the listed items — and that the silhouette still
  reads as the requested thing, nothing missing, exploded, lying on its side or left over from a
  primitive (a stray `Cube`), the materials the plan's.

## References

When `spec.json` lists `references`, open them before planning.  On the object tracks the
plan's proportions, part set and visible details match the FIRST image, with dimensions in
metres consistent with what it shows; the pictures outrank the text where they disagree; that
first reference is the silhouette target — its outline IoU against the best-matching render is
measured (`check` WARNs below 0.6) and the judge scores `silhouette_match` from it (rubric
`reference`).  Run `3dcode compare-reference` after the first clean build and after every
shape change.  On the scene and graphics tracks the photos are not a composition to copy: they
show what the real thing looks like — its dominant and secondary colours, how its structure
folds, layers and thins, where brightness concentrates and how much stays dark, its texture at
fine scale — and they outrank the brief's adjectives; the judge sees your frames beside the
same photos.  Keep the brief's foreground, framing and landscape, and open the photos again
on the refine round's one look.

## Repairs

A build failure is fixed with the smallest correct change at the `file:line` the output names:
a runtime error → the call or import (the language skill's allow-list is the only set of
modules); a lint error → the construct the fix hint names; a syntax error → that line; a
timeout or memory blow-up → fewer segments or loop counts.  Keep every part, name and
dimension.  The same error twice means the edit missed the cause: change a different call,
not different arguments — check the name and signature against the language skill's API facts
first.  Never finish a round on a broken build.

## Honesty

* One code path: no detection of headless mode, user agent or the instruments.
* Never special-case a check to make it pass — no hidden parts, zero-scaled parts, invisible
  filler geometry, connector sticks bridging a gap; fix the geometry the finding names.
* No fake success, no swallowed exceptions around geometry (the one exception is a cosmetic
  fillet/chamfer falling back to the unrounded solid when the kernel refuses it).
* Never import a helper SDK, the plugin's own package or `runtime_js/`; harness-owned modules a
  language skill names inside `src/` (scene_threejs `src/lib/*.js`) are the one exception.
* `judgment.json` is the judge's: never edit it, never give the judge anything but the printed
  `JUDGE INSTRUCTION`, and report a self-judged round as such.
