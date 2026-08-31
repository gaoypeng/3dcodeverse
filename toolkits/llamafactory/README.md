# llamafactory/ — 3DCodeVerse → LLaMA-Factory datasets

Scripts that turn the corpus on the Hub into datasets a training run can consume directly, and that verify the
data with the same runtimes the evaluation uses. See [`../README.md`](../README.md) for the table of every script
and the two rules they encode; the sections below are the details you need to run them.

## Output layout

```
<subdir>_llamafactory/          one per corpus subdirectory, next to the source
  train.parquet                 ShareGPT: system + conversations[human,gpt], plus id / caption_type / n_tokens
  dataset_info.json             drop-in registry entry -> dataset: 3dcv_<subdir>
  qc.json                       per-subdir counters (drops, syntax, token stats)
  README.md                     what it is and what was dropped, with reasons
<subdir>_llamafactory_flat/     flattened multi-file variants (Shadertoy passes with Common merged)
  train.parquet                 + compiles (bool) and repair (original / repaired:<cat> / unfixable)
```

Multimodal datasets (`build_mm_dataset.py`) add an `images` column; the number of `<image>` placeholders in the
user turn **must** equal `len(images)` — LLaMA-Factory's `mm_plugin._validate_input` raises otherwise. Multimodal
rows also cannot be packed: set `packing: false` and `neat_packing: false`.

## Dialects covered

| dialect | runtime used for verification | notes |
|---|---|---|
| Blender-Python | Blender 5.0.1 headless (needs `scipy` in its bundled Python) | 15 of the 212 3DCodeBench reference scripts import scipy |
| CadQuery | CadQuery 2.8 | the runner captures `show_object`/exports and prefers `result`/`solid` variables |
| OpenSCAD | OpenSCAD AppImage (`--export-format binstl`) | decode with sampling: greedy collapses into the Customizer parameter block |
| GLSL | glslangValidator **and** headless WebGL2 | try ES 3.00 and 3.10 × the channel-type assignments a shader actually uses |
| three.js / web | headless Chromium (Playwright) | judge blankness from the composited screenshot, not `gl.readPixels` |
| URDF | XML parse + link/joint counts | code recovered from the source tars |

## Requirements

Python 3.12 with `pandas`, `pyarrow`, `transformers` (tokenizer only), `playwright` (+ `playwright install chromium`),
and `huggingface_hub` for the upload paths; `glslangValidator` on PATH (conda-forge `glslang`); Blender / OpenSCAD
as system binaries. Paths to the binaries are constants at the top of each script — change them there.
| `build_pairs_3dcodebench.py` | the three pair types for one source subdirectory — text→code, image→code, image+text→code — with one caption per sample rather than one row per caption variant. Renders are read by **scanning each tar**, because the byte offsets in `metadata.parquet` cannot be trusted (for `3dcodebench/instances_geo` only 577 of 1,953 point at a real tar header; the archives were repacked after the metadata was written, and following them silently cost 70% of the image pairs) |
| `verify_unflattened_glsl.py` | compile-verifies the 44 Shadertoy shards `flatten_multifile.py` never reached — their metadata carries no `shader_json`, so there was no renderpass structure to merge a Common tab from. Writes the same `compiles` / `repair` columns the flattened shards have, so one filter covers the whole GLSL corpus |
| `runners/` | the per-source drivers that call the above over every subdirectory |

### A third rule these tools now encode

3. **A conversion must never decide what a benchmark forbids.** `convert_subdirs.py` used to drop any sample
   overlapping the 212 3DCodeBench factories, which silently emptied both `3dcodebench/instances_*` folders —
   100% of their rows were overlaps. The overlap is now *counted* in `qc.json` and the rows are kept, so the
   split is a decision the training run makes, not one the converter already made.
