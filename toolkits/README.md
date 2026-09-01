# toolkits/ — the tools that turn raw 3D projects into trainable data

Everything here operates on **data**, not on models. The pipeline they cover, end to end:

```
contributor's project ──▶ curation ──▶ 3DCodeVerse on the Hub ──▶ LLaMA-Factory datasets ──▶ finetune/
   code + renders          validate,      per-source subdirs         per-subdir ShareGPT,      training runs
   + meta.json             dedupe,        (metadata.parquet + tars)  verified & flagged        and evaluation
                           render, pack
```

| folder | stage | what it does |
|---|---|---|
| [`3dcode_cli/`](3dcode_cli/) | contributor → corpus | the installable `3dcode` CLI: validate, fingerprint/dedupe, execute, render and push a project to the staging bucket. Per-dialect modules for Blender-Python, CadQuery, build123d, FreeCAD and OpenSCAD |
| [`curation/`](curation/) | raw → corpus | per-source curation: convert a source's native format to runnable projects, execute/render them, compute ground-truth meshes, dedupe, pack to tar+parquet, upload to the Hub |
| [`llamafactory/`](llamafactory/) | corpus → training data | turn every corpus subdirectory into a LLaMA-Factory dataset, verify it with the real compilers/runtimes, flatten multi-file samples, build multimodal (render + instruction) variants, and sample training mixes |

`3dcode_cli/` is a complete copy of **[gaoypeng/3dcode_toolkit](https://github.com/gaoypeng/3dcode_toolkit)**
(74 files, byte-for-byte apart from `.git`), kept here so the whole data toolchain lives in one place. It is still
`pipx install`-able from either location:

```bash
pipx install "git+https://github.com/gaoypeng/3dcode_toolkit"      # upstream
pipx install ./toolkits/3dcode_cli                                  # this copy
```

If both are edited they will drift — pick one as canonical and mirror the other.

---

## curation/ — per source

| source | scripts | notes |
|---|---|---|
| `deepcad/` | `convert_vec_to_cq.py`, `batch_convert.py`, `cq_check{,_batch}.py`, `gen_gt_stl.py`, `iou_compute.py`, `render_all.py`, `dedup_check.py`, `attach_text.py`, `pack_tar_parquet.py`, `finalize.py`, `upload_to_hf.py` | DeepCAD vector sequences → CadQuery programs, executed and checked, GT meshes + IoU, captions attached, packed and uploaded |
| `articraft/` | `articraft_cq.py`, `assemble_shim.py`, … | articulated assets → CadQuery / URDF projects |

The shape of a curation pass, which any new source should follow: **convert → execute → render → compute a
ground truth → dedupe → attach text → pack → upload**. Every step writes files, so a failed step can be rerun
without redoing the pipeline.

## llamafactory/ — corpus → trainable datasets

| script | what it does |
|---|---|
| `convert_subdirs.py` | one `<subdir>_llamafactory/` per corpus subdirectory: `train.parquet` (ShareGPT) + `dataset_info.json` + `qc.json` + README. Runs the per-sample quality checks (empty/short/mojibake code, in-subdir duplicates, missing captions, **benchmark overlap**, syntax, over-length, literal media placeholders) |
| `flatten_multifile.py` | makes multi-file samples single-file: merges a Shadertoy shader's **Common tab** into each pass exactly as Shadertoy does, and recovers `code.urdf` for the articraft URDF subdirs (whose `code` column is empty in the metadata) |
| `qc_repair.py` | validates with the **real** compiler (glslangValidator for GLSL, `ast.parse` for Python dialects), attempts minimal semantics-preserving repairs, re-validates each one, and reports exact-duplicate groups within and across subdirs |
| `glsl_render_batch.py`, `glsl_render.py`, `render_verify_glsl.py` | compiling is not drawing: render every shader in a real headless WebGL2 context, sample two frames, and record whether the image is non-uniform and whether it animates |
| `finalize_glsl.py` | consolidates: prefer the author's untouched program, fall back to a repair only if it compiles, otherwise mark `compiles=False`. Writes `compiles` / `repair` columns |
| `build_mm_dataset.py` | the multimodal datasets — `mm_img` (renders only), `mm_img_text` (renders + instruction), `mm_text` (control), from the same samples with the same split so they can be compared |
| `build_image_dataset.py` | earlier single-variant image builder (kept for reproducing the first pilot) |
| `build_md_max.py`, `sample_mix.py` | build the full corpus (every source, every caption variant) and sample training mixes from it with a per-dialect token budget and a captions-per-sample cap |
| `to_llamafactory.py` | jsonl → ShareGPT JSON + a `dataset_info.json` entry |
| `build_hf_dataset.py` | package corpus + preference sets + held-out tests as a Hub dataset |
| `cache_ntok.py`, `ntok_cache.py` | cache per-sample token counts so re-mixing is seconds, not half an hour |
| `extract.py` | dialect-aware code extraction from free-form model output (used by evaluation, included here because dataset QC uses the same rules) |
| `analyze_3dcodeverse.py`, `fetch_hf_files.py` | corpus inventory; selective file download from the Hub |

### Two rules these tools encode

1. **Suspect the checker before the data.** Of 4,192 shaders that "failed", only 588 actually needed a code
   change; the rest exposed defects in the validation harness (guessing sampler types from syntax, a missing
   `iFrameRate` uniform, a regex that deleted real code from minified lines, the wrong GLSL ES profile).
   Every checker in here has a known-good control that must pass.
2. **Mark, don't silently drop.** Rows that cannot be used for a particular purpose — `benchmark_factory`
   (3DCodeBench contamination), `compiles=False`, over-length — stay in the dataset with a column that lets a
   training mix filter them out. The one exception is exact duplicates, which are removed and listed in
   `duplicates_<source>.json` so the upstream corpus can be cleaned too.

### Typical use

```bash
# 1. corpus -> one dataset per subdirectory, with QC
python convert_subdirs.py --out /path/to/out --workers 16

# 2. make multi-file samples single-file, recover code that lives in tars
python flatten_multifile.py --shadertoy --out /path/to/out
python flatten_multifile.py --urdf_tars /path/to/tars --out /path/to/out

# 3. verify with the real toolchain, repair minimally, consolidate
python qc_repair.py --pass all --root /path/to/out
python render_verify_glsl.py --workers 10 --write
python finalize_glsl.py

# 4. build a training mix (per-dialect token budget, one caption per sample)
python sample_mix.py --out mix/ --budget "blender=all,cadquery=25M,glsl=25M" --captions_per_sample 1

# 5. multimodal variants (renders + instruction)
python build_mm_dataset.py --views 4 --out mm/
```

Results produced with these tools: [`finetune/llm_finetune_exps.md`](../finetune/llm_finetune_exps.md) and
[`finetune/docs/REPORT.md`](../finetune/docs/REPORT.md).

---

## Layout note

Nothing outside this folder hardcodes the name `toolkits/`: the scripts take their input and output paths as
arguments, and the only references are the links in this file, `../README.md` and `../finetune/README.md`.
Renaming the folder is therefore a `git mv` plus those three links.
