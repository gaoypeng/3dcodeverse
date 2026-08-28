# TEASER — the showcase page, what is on it, and how to rebuild it

`docs/teaser.html` is a self-contained showcase page for fifteen artefacts produced by the
harness in the `teaser_v1` wave.  It is **not a measurement**: the judge score is a filter
("was this good enough to be worth looking at"), and every card was then looked at by a
human and captioned with what is still wrong with it.  Two cards ship at a *lower* judged
score than the run they replace, because the higher-scoring run did not contain the thing
the prompt was chosen for.

> Defect ids below (`D1`, `D5`, `D9`, `D10`, `S-OBJ-3`, `S-OBJ-4` …) refer to the
> `teaser_v1` wave's defect log, kept with the wave scratchpad rather than in the repo:
> the wave was a generation task, so friction was recorded, not fixed.

## 1. Serve it

The page references stills and clips under `bench/out/teaser/site/media/` with the relative
path `../bench/out/teaser/site/media/…`, so it needs a server rooted at the harness
directory (opening the file directly works too, as long as `bench/out/teaser/` is present):

```
cd /home/yipeng/3dcodeverse/harness
python -m http.server 8931 --bind 127.0.0.1 -d .
# → http://localhost:8931/docs/teaser.html
```

`bench/out/` is git-ignored, so **only the page, the prompt sheets and this document are
committed** — the ~31 MB of stills and GIFs are not.  On a fresh checkout the page renders
with empty image frames until the runs are regenerated (§4) and the media directory is
rebuilt (§5).

## 2. What is on the page

| section | artefact | languages | generator | judge | verdict |
|---|---|---|---|---|---|
| lead | Penny-farthing | Blender bpy | codex:gpt-5.6-sol | 0.863 | reads |
| 01 multi-language | StoneLantern · BronzeCenser · PotbellyStove · ClinkerSkiff | bpy → GLB → three.js + GLSL | both | — | see §3 |
| 02 static | Arcade cabinet | three.js | flash | 0.948 | reads |
| 02 static | Coastal lighthouse | Blender bpy + text-to-image texture | flash | 0.728 | reads |
| 02 static | Wind-up gramophone | Blender bpy | flash | 0.750 | reads |
| 02 static | Radial aero engine | CadQuery | codex:gpt-5.6-sol | 0.600 | usable |
| 02 static | Art-deco chandelier | three.js | flash | 0.600 | usable |
| 03 articulated | Rolling tool chest | URDF + bpy | flash | 0.957 | reads |
| 03 articulated | Longcase clock | URDF + bpy | flash | 0.947 | reads |
| 04 scenes | Neon alley | three.js + GLSL | flash | 0.827 | reads |
| 04 scenes | Boatbuilder's workshop | three.js + GLSL + bpy | flash | 0.747 | reads |
| 04 scenes | Temple courtyard at night | three.js + GLSL + bpy | codex:gpt-5.6-sol | 0.286 | usable |
| 05 graphics | Aurora over a ridge | GLSL | flash | 0.940 | reads |
| 05 graphics | Starling murmuration | Python OpenGL + GLSL | flash | 0.865 | reads |
| 05 graphics | Accretion disc | GLSL | flash | 0.753 | reads |
| 05 graphics | Rain on a window | GLSL | flash | 0.909 | usable |

`flash` = `gemini-cli:gemini-3.6-flash`.  Judge = `gemini-3.1-pro-preview`
(flash × 3 fallback).  Per card the page shows: the prompt, the language(s), the generator,
the judged score, the round count, the wall-clock, the run slug, and an honest note.

Each object card offers three views — a chosen hero view (**not** always `front_right_34`),
a turntable GIF, and the 8-view contact sheet.  Articulated cards add the articulation
sheet and a joint-motion loop.  Scene cards show the authored cameras at **t = 0 s and
t = 1.5 s** side by side so motion reads, plus detail cameras and the full sheet.  Graphics
cards show a hero frame, an animated preview and the 0/1/2.5/4/6 s frame strip.

### Left out, and why

* **Espresso portafilter (CadQuery)** — rated *no*.  Two attempts; the second has better
  geometry but the body is still flat white on stubby legs.  The CadQuery track's only
  material channel is `cq.Color(r,g,b)` with no metalness, so "polished stainless" is
  unreachable by construction and the texture pass produced zero textures
  (`DEFECTS.md` S-OBJ-4).  A third attempt would fail the same way.
* **Ten superseded attempts** — the first radial engine, chandelier, clock, temple,
  workshop and alley; two extra rain windows; two extra auroras; a second accretion disc.
  All are on disk under `bench/out/teaser/runs/`; the winner's caption says why each lost.
* **mp4 turntables** — ffmpeg is not installed on this machine, so `assemble_turntable`
  falls back to animated GIF (`DEFECTS.md` D3).  Nothing in the harness requires that.
* **Scene textures** — `3dcv texture scene-pack` produced 8 tileable maps for the workshop
  (seam ≈ 0.0007) and 7 for the alley, and neither could be wired in: a passed run refuses
  `resume` without `--force`, and `--force` re-plans and overwrites the passing artefact
  (`DEFECTS.md` D9).

## 3. The multi-language panel (the centrepiece)

Two scene prompts force the pipeline no single-language generator can show:

```
_assets/<name>/src/model.py      raw Blender bpy, its own plan + agent session + 8-view review
      ↓  blender -b --factory-startup, census, glTF export
artifacts/object.glb  →  public/assets/<name>.glb
      ↓  loaders.gltf.loadAsync in src/scene.js (or a per-asset loader module)
src/zones/*.js                   three.js composition beside procedural geometry
src/shaders/*.js                 raw GLSL vertex/fragment pairs with a live uTime uniform
```

Nothing in the harness enforces asset kind (`DEFECTS.md` D2/D3), so this was verified by
hand rather than trusted, and the page says exactly what held:

| asset | scene | bpy → GLB | GLB used in the shipped frames |
|---|---|---|---|
| StoneLantern | temple | yes, 443 KB | **yes** — `src/assets/stone_lantern.js` loads it and *throws* if missing; cloned into 3 zones |
| BronzeCenser | temple | yes, 367 KB | **yes** — `src/scene.js:12` → `src/zones/courtyard_veranda.js:32` |
| PotbellyStove | workshop | yes, 131 KB | **yes** — `src/scene.js:12` → `src/zones/stove_corner.js:130` |
| ClinkerSkiff | workshop | yes, 400 KB | **no** — loaded but unused; `src/zones/central_bay.js:59` calls a *procedural* three.js rebuild a refine round wrote over the asset slot |

The ClinkerSkiff case is filed as `DEFECTS.md` D10 and is captioned honestly on the page:
the standalone skiff card is the real bpy asset, the hull in the workshop frames is not.
**The temple is the clean end-to-end demonstration.**

GLSL in the shipped frames: the temple pond (`src/shaders/water.js`, a `ShaderMaterial`
named `Water` on `ReflectiveKoiPond` with `uTime/uDeep/uShallow/uSunDir`) and the workshop's
`VolumetricSunbeamShader` + `SawdustMotesShader`, all with live time uniforms the render rig
samples at two times.

## 4. Regenerate the runs

Prompt sheets live in `bench/prompts/teaser_v1_{static,articulated,scene,graphics}.yaml`
— one file per track, because a `Battery` binds exactly one track (`DEFECTS.md` D1).  Each
sheet holds the original curated prompt **and** the sharpened re-run that actually shipped,
suffixed `_v2` / `_v3`, with the CLI flags that are not expressible in `BenchPrompt`
(`--style`, `--must-not`, `--candidates`, `--profile`) recorded as comments above it.

```
3dcv bench run bench/prompts/teaser_v1_static.yaml      --generator gemini-cli:gemini-3.6-flash
3dcv bench run bench/prompts/teaser_v1_articulated.yaml --generator gemini-cli:gemini-3.6-flash
3dcv bench run bench/prompts/teaser_v1_scene.yaml       --generator gemini-cli:gemini-3.6-flash
3dcv bench run bench/prompts/teaser_v1_graphics.yaml    --generator gemini-cli:gemini-3.6-flash
```

The three hero pieces used `--generator codex:gpt-5.6-sol` instead (penny-farthing, radial
engine, temple courtyard).  Note that `--profile quality` prices codex subscription usage as
API dollars and self-throttles the scene track (`DEFECTS.md` D5) — raise `--max-usd` well
above the profile default for a codex scene run.

Per-artefact extras used here:

```
3dcv render <slug>                 # 8 canonical views (--mode shaded|wire|normals|clay)
3dcv texture pass <slug>           # text-to-image material pass; SHIPPED only on the lighthouse
3dcv texture scene-pack <slug>     # tileable pack for a scene (see the D9 caveat above)
```

Turntables and the articulated loops were rendered with
`codeverse.spatial.render.render_turntable(glb, out, n=36, elevation_deg=18, width=640,
height=640, fps=15)` — resolve the GLB through `artifacts/textures/texturing.json`
→ `shipped`, never by filename, or you render the *rejected* textured build
(`DEFECTS.md` S-OBJ-3).

## 5. Rebuild the page

`docs/teaser.html` is generated, not hand-edited.  Everything needed to rebuild it is
committed under `bench/teaser/`:

| file | what it does |
|---|---|
| `bench/teaser/content.py` | the curated content table — one entry per shipped artefact: title, languages, generator, score, verdict, prompt, honest note, chosen media |
| `bench/teaser/build_page.py` | renders `docs/teaser.html` from it (markup + CSS + the small JS) |
| `bench/teaser/build_media.py` | copies and downscales the chosen stills/clips into `bench/out/teaser/site/media/` |
| `bench/teaser/render_turntables.py` | renders every turntable, resolving the *shipped* GLB through `texturing.json` |

```
python bench/teaser/render_turntables.py    # bench/out/teaser/turntables/*.gif (+ the 4 Blender heroes)
python bench/teaser/build_media.py          # bench/out/teaser/site/media/ (~31 MB)
python bench/teaser/build_page.py           # rewrites docs/teaser.html
```

`render_turntables.py` writes the four Blender-hero clips to
`$TEASER_ASSET_TURNTABLES` (default `bench/out/teaser/turntables/assets/`); point
`build_media.py` at the same directory with that variable.

The page is one file with no external dependencies: inline CSS, ~30 lines of vanilla JS for
the view tabs, a lightbox and deferred GIF loading.  JPEGs (4.3 MB total) load normally;
the GIFs (26 MB) are fetched only when their tab is opened or they scroll into view.
