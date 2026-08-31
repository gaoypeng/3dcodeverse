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
| `shrink_pair_images.py` | re-encodes the extracted renders to training resolution (≤448px JPEG). Measured 90.8% smaller — 113.7 GB becomes 10.5 GB — and loses nothing a training run would have seen, because every vision tower downsamples to 448² anyway; the originals stay in the corpus tars |
| `upload_pairs.py` | publishes the pair datasets INTO their source folders (`<source>/<subdir>_llamafactory_{text,img,imgtext}`) and their renders as `<source>/pair_renders/`, then registers everything in the root `dataset_info.json` |
| `drop_old_pair_trees.py` | removes a superseded tree only after verifying the index no longer points into it — it refuses to run otherwise |

### A fourth rule, learned the expensive way

4. **Same name does not mean same content — compare before deleting.** Twice a folder appeared both at the
   repository root and inside its source, and both times the root copy was the NEWER one (1,008 vs 949 samples,
   and earlier 949 vs 318). Deleting the apparent duplicate would have silently discarded 1,239 samples. The
   safe order is always: compare, promote the newer copy, then delete — in a single commit, so the repository is
   never seen with the data in two places or in neither.
| `audit_pairs.py` | checks every pair dataset for the invariants that fail only once training has started: absolute paths, wrong extension, image files that do not exist, `<image>` count vs `len(images)`, turn shape, fenced code, empty system, row count vs `qc.json`. Exits non-zero on any finding, so it can gate a publish |

### A fifth rule

5. **Sampling proves a problem exists; it cannot prove one does not.** Checking four datasets said "every path is
   relative". Scanning all 411 found **36,964 absolute paths** in three of them — introduced by a later rebuild
   that reused already-extracted renders and took a code path which skipped the relativisation. For a global
   invariant, scan globally: `audit_pairs.py` exists for exactly this.

   The same audit script then reported all 1,948,765 rows as malformed, which was a defect in the checker
   (parquet returns numpy arrays, and `isinstance(x, list)` is False for them) — rule 1 applies to your own
   checkers too.
