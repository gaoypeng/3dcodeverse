# llamafactory/ — 3DCodeVerse training-data tools

This is the canonical home for the project's LLaMA-Factory data preparation: corpus conversion,
quality checks, multimodal pairs, mix sampling, and publication. It contains project tooling, not
upstream LLaMA-Factory source. General corpus curation stays in [`../../toolkits/`](../../toolkits/);
training configs, environments, and launchers stay in the parent [`finetune/`](../README.md) component.

The six former data scripts in `../scripts/` forward to the implementation here for CLI compatibility.
For new workflows, call `python llamafactory/<script>.py ...` from `finetune/`. Per-source shell drivers
live in [`runners/`](runners/).

## Script catalog

| script | what it does |
|---|---|
| `convert_subdirs.py` | one `<subdir>_llamafactory/` per corpus subdirectory: ShareGPT `train.json`, registry, `qc.json`, and README. Checks empty/short/mojibake code, duplicates, captions, benchmark overlap, syntax, length, and literal media placeholders |
| `flatten_multifile.py` | merges a Shadertoy shader's Common tab into its passes, and recovers `code.urdf` for Articraft subdirectories whose metadata has no code; writes parquet datasets |
| `qc_repair.py` | checks parquet datasets with glslangValidator or Python `ast.parse`, attempts repairs, re-validates them, and reports duplicate groups within/across subdirectories |
| `glsl_render_batch.py`, `glsl_render.py`, `render_verify_glsl.py` | render shaders in headless WebGL2 and check non-uniform images and animation |
| `finalize_glsl.py` | prefer the original program, use a repair if it compiles, otherwise record `compiles=False`; writes `compiles` / `repair` columns |
| `verify_unflattened_glsl.py` | compile-check the Shadertoy shards without a flattened variant and record the same `compiles` / `repair` fields |
| `build_mm_dataset.py` | build `mm_img`, `mm_img_text`, and `mm_text` variants from the same source samples and split |
| `build_image_dataset.py` | earlier single-variant image builder, retained for reproducing the first pilot |
| `build_pairs_3dcodebench.py` | build text→code, image→code, and image+text→code pairs for one source subdirectory. Text variants retain usable captions; image-only pairs use one row per sample. Reads renders by scanning tars rather than trusting stale byte offsets |
| `shrink_pair_images.py` | re-encode extracted PNG renders as JPEGs at a configurable maximum size (default 448 px); source tars retain the originals |
| `audit_pairs.py` | check pair datasets for absolute/missing/non-JPEG media paths, placeholder counts across all turns, turn shape, fenced code, empty systems, and row-count/QC mismatches; exits nonzero on findings |
| `upload_pairs.py` | publish pair datasets under `<source>/<subdir>_llamafactory_{text,img,imgtext}` and optionally renders under `<source>/pair_renders/`, then update the Hub registry |
| `build_md_max.py`, `sample_mix.py` | build the combined corpus and sample mixes with per-dialect token budgets and captions-per-sample caps |
| `build_refine_sft.py` | turn improved harness refinement transitions into message JSONL for SFT |
| `to_llamafactory.py` | convert message JSONL to ShareGPT JSON and registry entries |
| `build_hf_dataset.py` | package prepared corpus data, historical preference sets, held-out tests, and mix descriptions for the Hub |
| `cache_ntok.py`, `ntok_cache.py` | calculate/cache token counts for data builders |
| `analyze_3dcodeverse.py`, `fetch_hf_files.py` | corpus inventory and selective Hub downloads |
| `runners/` | per-source drivers for the pair-building workflows |

## Formats and stage boundaries

`convert_subdirs.py` writes JSON, while the flattening/pair builders and downstream QC tools use
parquet. These are separate workflows with prepared inputs; the directory move does not provide a
missing JSON→parquet conversion stage.

```text
<subdir>_llamafactory/             corpus conversion
  train.json                       ShareGPT system + conversations[human,gpt]
  dataset_info.json                 local registry entry
  qc.json                          counts and quality findings
  README.md                        provenance and filtering notes
<subdir>_llamafactory_flat/        flattened shader variants
  train.parquet                    code and verification fields where produced
<subdir>_llamafactory_{text,img,imgtext}/
  train.parquet                    pairs, plus images for multimodal variants
  dataset_info.json
  qc.json
```

For multimodal data, the total number of `<image>` placeholders across **all conversation turns**
must equal `len(images)`. Literal placeholders inside assistant code also count; the pair builder
rejects those samples and the audit checks both turns. Use `packing: false` and `neat_packing: false`
for image-containing rows.

The audit validates prepared outputs; it does not rewrite image paths or convert formats. Prepare
relative JPEG media paths and the corresponding files before expecting it to pass.

## Requirements and paths

The original data environment uses Python 3.12 with `pandas`, `pyarrow`, `transformers` (tokenizer),
Pillow, and `huggingface_hub` for publication. Particular scripts also need `requests`, Playwright
and Chromium, glslangValidator, Blender, or OpenSCAD. See [`../env/`](../env/) for environment snapshots
and [`../docs/SETUP.md`](../docs/SETUP.md) for the original setup.

Many input, model, output, and binary paths still refer to `/wekafs/ict/hx_624/...` in script constants
or defaults. Inspect and adapt them before running on another machine. Not every path has a CLI
option, and setting `env.sh` alone does not change those constants. Hub operations require access
to the relevant datasets; upload and deletion scripts change remote data.

| dialect | verification used by the workflows |
|---|---|
| Blender-Python | Python syntax checks; execution workflows use headless Blender, with scipy needed by some reference scripts |
| CadQuery | Python syntax checks; execution workflows use CadQuery |
| OpenSCAD | OpenSCAD CLI |
| GLSL | glslangValidator and headless WebGL2; compilation and visible rendering are distinct checks |
| three.js / web | headless Chromium through Playwright |
| URDF | recovery from source tars and XML checks |

## Examples

Run from `finetune/` after preparing inputs and adapting the script-specific paths. These are examples
for separate stages, not a single ready-to-run pipeline:

```bash
# Corpus metadata -> ShareGPT JSON datasets
python llamafactory/convert_subdirs.py --out /path/to/json-datasets --workers 16

# Flatten shader passes / recover URDF from the prepared source corpus
python llamafactory/flatten_multifile.py --shadertoy --out /path/to/flat-datasets
python llamafactory/flatten_multifile.py --urdf_tars /path/to/tars --out /path/to/flat-datasets

# QC expects prepared parquet datasets
python llamafactory/qc_repair.py --pass all --root /path/to/parquet-datasets

# Mix sampling expects message-format train.jsonl and val.jsonl
python llamafactory/sample_mix.py --src /path/to/md_max --out /path/to/mix \
  --budget "blender=all,cadquery=25M,glsl=25M" --captions_per_sample 1

# Multimodal variants use the builder's prepared sources
python llamafactory/build_mm_dataset.py --views 4 --out /path/to/mm

# Audit every pair directory under the supplied root before publication
python llamafactory/audit_pairs.py --roots /path/to/pairs --media_root /path/to/media
```

## Lessons from the data work

1. **Check the checker.** Many shader failures came from validation defects: sampler types, missing
   uniforms, transformations of minified code, or the GLSL ES profile. Use known-good controls.
2. **Keep filtering decisions explicit.** Record QC findings and the reasons for drops. Benchmark
   membership and runtime validity serve different purposes; an experiment must declare its split
   and filtering policy rather than assuming conversion enforces it.
3. **Benchmark overlap belongs to split selection.** `convert_subdirs.py` counts overlaps with the
   212 benchmark factories in `qc.json` and keeps those rows. It previously dropped entire instance
   folders. Counting overlap does not itself make a later training mix leak-free.
4. **Compare before deleting.** Same-named trees previously held different contents; the root copy
   was sometimes newer. Compare contents and verify the published registry before retiring a copy.
5. **Audit global invariants globally.** A full scan once found 36,964 absolute media paths that spot
   checks missed. Also validate the audit itself: parquet sequence values can be numpy arrays,
   which invalidated an earlier list-only check.

Results and historical context remain in [`../llm_finetune_exps.md`](../llm_finetune_exps.md) and
[`../docs/REPORT.md`](../docs/REPORT.md). Current training recipes are listed in
[`../configs/lf/README.md`](../configs/lf/README.md).
