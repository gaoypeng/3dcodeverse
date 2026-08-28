# Articraft → CadQuery curation tools

Tools used to curate the **Articraft** subset of [`ilabai/3dcodeverse`](https://huggingface.co/datasets/ilabai/3dcodeverse).

[Articraft](https://github.com/mattzh72/articraft) (Apache-2.0) is a **mesh-based**
SDK: its models build triangle meshes. We convert each model into a
**self-contained, parametric CadQuery (B-rep) program** — one `code.py` per sample
that only needs `import cadquery` to run — then verify, render, caption, and upload.

**Result: 6878 / 6882 samples converted (99.94%).**

## Approach

1. **Clean-room CadQuery shim** reimplementing the Articraft SDK public API, producing
   exact B-rep instead of triangle meshes. `shim/` = a core (`articraft_cq.py`) +
   one module per geometry family (`shim_parts/`).
2. **Tree-shaking generator** (`gen_code.py`): for each model it inlines *only* the
   shim symbols the model actually uses (symbol-level dependency closure over core +
   families + the model body), giving a minimal self-contained `code.py`
   (median ~900 lines vs ~1600 for whole-shim inlining).
3. **Verify** each output against the SDK's ground-truth mesh via normalized
   **chamfer distance** (pass = chamfer < 0.02 and bbox ratio ∈ [0.9, 1.1]).
4. **Render** 8 azimuth views + a colored GLB with Blender (Cycles, GPU).
5. **Caption** a natural "user instruction" per sample with Gemini (image-primary +
   a structured prior of parts/joints).
6. **Upload** to Hugging Face in small low-concurrency chunks.

## Layout

```
shim:
  articraft_cq.py        core shim: model API (ArticulatedObject + rest-pose FK ->
                         cq.Assembly), primitives, transforms, MeshGeometry base,
                         profile/builder helpers, booleans
  shim_parts/*.py        10 geometry families (primitives, lathe_extrude_loft, sweeps,
                         section_loft, knobs, wheels_tires, fans, panels_vents,
                         brackets_hinges_bezels, gears)
  assemble_shim.py       combine core + families; name -> family map

convert:
  gen_code.py            tree-shaking self-contained code.py generator

verify:
  verify_cadquery.py     exec code.py -> chamfer vs HF ground-truth mesh (+ --glb export)
  geom_chamfer.py        isolated per-geometry chamfer (real sdk vs candidate)

render:
  cq_to_glb.py           exec code.py -> renders/object.glb (colored cq.Assembly)
  blender_glb.py         Blender: load GLB -> 8 views (material / clay)
  blender_urdf.py        Blender: render a URDF -> 8 views
  render_glb_only.py     GPU consumer: render every pending GLB in parallel
  driver_urdf_render.py  URDF render driver

caption:
  caption_cadquery.py    Gemini "generate this as CadQuery code" user-instruction
  caption_urdf.py        Gemini "generate this as URDF" user-instruction

batch / pipeline:
  batch_verify.py        parallel gen + verify + meta update (resumable)
  regen_glb.py           regenerate code.py + export GLB (no chamfer), parallel
  exec_validate.py       regen + exec-validate ALL samples (catch regressions)
  integrate_recovered.py re-run failed samples through fixes and fold the passes back in

upload:
  upload_articraft.py    chunked HF upload (low-concurrency create_commit to respect
                         rate limits; hf_transfer bursts trip 429s)
```

## Configuration (env vars)

| var | meaning | default |
|-----|---------|---------|
| `ARTICRAFT_ROOT` | dataset root containing `data/articraft_with_mesh/` etc. | repo-relative guess |
| `ARTICRAFT_FORK` | path to the real Articraft sdk (ground truth, for `geom_chamfer`) | — |
| `BLENDER` | Blender executable | `blender` |
| `CONDA_PREFIX` | used to set `LD_LIBRARY_PATH` so Blender finds conda libs | — |
| `GEMINI_API_KEY` or `~/.gemini_api_key` | Gemini key (captioning) | — |
| `~/.hf_token` | Hugging Face write token (verify download + upload) | — |

Credentials are **always read from env / dotfiles — never hard-coded or committed.**

## Typical pipeline

```bash
export ARTICRAFT_ROOT=/path/to/dataset
python assemble_shim.py                 # sanity: combine core + all families
python batch_verify.py all --workers 12 # gen self-contained code.py + chamfer verify
python regen_glb.py --workers 16        # export renders/object.glb for passing samples
python render_glb_only.py --workers 12  # render 8 views per GLB
python caption_cadquery.py --workers 256 --rpm 900   # Gemini captions
python upload_articraft.py mesh         # chunked, low-concurrency HF upload
```

## Gotchas learned (baked into the tools)

- **mesh vs B-rep**: some models read `.vertices/.faces` off a geometry and rebuild it
  (axis swaps) — the shim tessellates the B-rep on demand for those (`MeshGeometry`
  `.vertices/.faces` are read/write properties).
- **`unit_scale`**: models authored in mm pass `unit_scale=0.001`; the shim honors it
  (otherwise output is 1000× too large).
- **`cq.Workplane.gear` plugin**: cq_gears-style `wp.gear(SpurGear(...))` is registered
  when the gears family is inlined.
- **name collisions**: inlining puts shim helpers and model helpers in one namespace;
  private shim helpers that clash with a model's are auto-renamed (`_shim*`).
- **OCC robustness**: `merge()` falls back to a Compound when a boolean fuse returns a
  null shape (e.g. many thin wire loops).
- **HF rate limits**: upload uses `create_commit(num_threads=5)` + backoff, not
  `hf_transfer` (whose parallel bursts trip HF's request-rate limit and then hang).
