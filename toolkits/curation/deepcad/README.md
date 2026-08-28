# DeepCAD → CadQuery curation tools

Tools used to curate the **DeepCAD** subset of [`ilabai/3dcodeverse`](https://huggingface.co/datasets/ilabai/3dcodeverse) (under `deepcad/v1/`).

[DeepCAD](https://github.com/ChrisWu1997/DeepCAD) (MIT) is a dataset of ~178k CAD
construction sequences (sketch + extrude, stored as quantized command vectors). We
convert each sequence into a **self-contained, executable CadQuery (B-rep) program**
— one `code.py` that only needs `import cadquery` to run — then validate, score
geometric fidelity, render, attach text, dedup, and upload.

**Result: 174,981 / 178,238 converted (98.2%); 174,177 produce a valid solid
(97.7%); 128,201 unique after dedup. Conversion fidelity vs DeepCAD's own
geometry: mean IoU 0.985, median 1.0, 96.9% > 0.9.**

## Approach

1. **Convert** the vectorized sequence → CadQuery, reusing the *validated*
   numeric logic from [GenCAD-Code](https://github.com/anniedoris/GenCAD-Code)
   (sketch plane from `CoordSystem`, two-sided/symmetric extrudes, arc geometry,
   the DeepCAD normalization scale). `convert_vec_to_cq.py` + `batch_convert.py`.
2. **Validate** by execution: run each `code.py`, compute exact volume +
   topological validity natively via OpenCASCADE (`GProp` + `BRepCheck_Analyzer`),
   export STEP. Batched worker imports cadquery once (`validate_fast.py` +
   `cq_check_batch.py`) — ~7× faster than per-model subprocess.
3. **Geometric fidelity (IoU)**: generate DeepCAD's *official* ground-truth mesh
   from the same vec via `cadlib.visualize.vec2CADsolid` (pythonocc-core, separate
   env), then voxel-IoU + Chamfer vs our CadQuery geometry. `gen_gt_stl.py` +
   `iou_compute.py`. This proves the conversion is geometrically faithful, not
   merely compilable.
4. **Render** 4 views (iso/front/right/top) + `object.glb` with headless pyrender
   (OSMesa). `render_all.py`.
5. **Text**: map [Text2CAD](https://huggingface.co/datasets/SadilKhan/Text2CAD)
   (CC-BY-NC-SA) 4-level descriptions by uid. `attach_text.py`. (Kept in
   `curation.json`; the official caption is filled by the maintainer.)
6. **Dedup** on the *source vec hash* (deterministic conversion ⇒ identical
   code+geometry). `dedup_vec.py`. (`dedup_fast.py` does a geometry-fingerprint
   variant — kept for reference; it over-merges, see Gotchas.)
7. **Assemble + finalize**: merge into a JSONL, mark dedup + leakage-free splits +
   IoU fidelity tiers. `assemble_corpus.py` → `mark_dedup.py` → `finalize.py`.
8. **Format + pack**: emit the official per-sample dir layout (`to_3dcodeverse.py`),
   then pack into tar shards + `metadata.parquet` 1:1 with `shadertoy/3X`
   (`pack_tar_parquet.py`) — turns 1.15M small files into ~30, sidestepping HF's
   request-rate limit and enabling HTTP-Range single-sample reads for the viewer.
9. **Upload** the ~30 packed files (`upload_to_hf.py` / `HfApi.upload_folder`).

## Layout

```
convert:
  convert_vec_to_cq.py   vec(.h5) -> standalone CadQuery code (GenCAD-Code logic)
  batch_convert.py       parallel conversion over the official split

validate:
  cq_check.py            single-model worker: exec -> OCC volume+validity -> STEP
  cq_check_batch.py      batched worker (import cadquery once per chunk)
  validate_fast.py       fast driver (chunked subprocess, crash-isolating retry)
  validate_all.py        simple per-model driver (slower; kept for reference)

fidelity (IoU):
  gen_gt_stl.py          DeepCAD official vec2CADsolid -> ground-truth STL (pythonocc env)
  iou_compute.py         voxel IoU + Chamfer: our STEP vs GT STL

render:
  render_all.py          STEP -> tessellate -> pyrender 4 views (OSMesa)

text:
  attach_text.py         Text2CAD CSV -> {uid: {abstract,beginner,intermediate,expert}}

assemble / dedup:
  assemble_corpus.py     merge code+step+renders+text+iou -> JSONL
  mark_dedup.py          annotate is_canonical/duplicate_of + cross-split leakage
  finalize.py            fidelity tiers (IoU) + leakage-free split_dedup
  dedup_vec.py           TRUE dedup on source vec hash
  dedup_fast.py          geometry-fingerprint dedup (reference; over-merges)

format / pack / upload:
  to_3dcodeverse.py      official per-sample dir layout (meta.json + curation.json + renders/)
  pack_tar_parquet.py    pack -> <sf>-NNNNN.tar shards + metadata.parquet (3X layout)
  upload_to_hf.py        HF upload (upload_large_folder / folder)

viz:
  viz_compare.py         side-by-side ours-vs-ground-truth render (IoU-labeled)
```

## Configuration

Runs inside a Docker container (CadQuery needs conda-forge; pip OCP is fragile):
- main env: `cadquery=2.7`, `h5py`, `trimesh`, `scipy`, `pyrender`, `PyOpenGL==3.1.7`
- `gt` env (ground-truth): `pythonocc-core=7.7`, `matplotlib-base`, `numpy<1.24`
- `GENCAD_SCRIPTS` → cloned GenCAD-Code `scripts/` (pure geom helpers)
- `~/.hf_token` / token file → HF token (read for download, write for upload)
- `PYOPENGL_PLATFORM=osmesa` for headless rendering

## Typical pipeline

```bash
python batch_convert.py  --vec_dir cad_vec --split split.json --out_dir converted_cq
python validate_fast.py  --cq_dir converted_cq --step_dir step_files --out val.json
# ground-truth IoU (in the pythonocc 'gt' env, then back in cadquery env):
python gen_gt_stl.py     --vec_dir cad_vec --ids ids.txt --out_dir gt_stl --deepcad DeepCAD
python iou_compute.py    --step_dir step_files --gt_dir gt_stl --ids ids.txt --out iou.json
python render_all.py     --step_dir step_files --out_dir renders
python attach_text.py    --csv text2cad.csv --out text.json
python assemble_corpus.py ... | python mark_dedup.py ... | python finalize.py ...
python dedup_vec.py      --vec_dir cad_vec --ids ids.txt --out dups.json
python to_3dcodeverse.py --jsonl final.jsonl --canonical_only --out_root corpus
python pack_tar_parquet.py --corpus corpus/deepcad --out packed --subfolder v1
python upload_to_hf.py   --folder packed --repo ilabai/3dcodeverse --token_file ~/.hf_token
```

## Gotchas learned (baked into the tools)

- **vec→CadQuery, not JSON→CadQuery**: a first JSON→CQ attempt reconstructed the
  sketch plane via Euler `ZYX` angles — wrong. The vec path reuses GenCAD-Code's
  validated `CoordSystem` (origin/x_axis/normal directly) and handles two-sided
  extrudes + arc sweep + the `NORM_FACTOR=0.75` scale correctly.
- **trimesh can't load STEP** (needs `cascadio`). Validate with OCC natively
  (`GProp` volume + `BRepCheck_Analyzer`) instead of meshing.
- **Validation speed**: per-model subprocess pays the ~1.5s cadquery import every
  time. Batching one import per chunk is ~7× faster (2.7h → ~8min for 175k).
- **Dedup must key on vec/code, not geometry**: a volume/area/inertia fingerprint
  over-merges badly (one "dup group" had 1396 distinct vecs that merely share a
  bounding box). `dedup_vec.py` (source-vec hash) is correct: 128,201 unique.
- **DeepCAD's official split leaks**: 3,230 vec-duplicate groups span train/test
  (32,737 models). `finalize.py` emits a leakage-free `split_dedup`.
- **IoU is harsh on thin/tiny geometry**: voxelizing a hair-thin part at 48³ can
  read 0 overlap even when the shapes match. The <0.5-IoU tail (0.7%) is a mix of
  this metric artifact, pose diffs, and a few real local diffs (holes). Gate on
  IoU directly for fidelity-sensitive use; don't trust the quality flag alone.
- **pyrender + OSMesa**: needs `PyOpenGL==3.1.7` (3.1.0 lacks
  `OSMesaCreateContextAttribs`).
- **HF rate limit + many small files**: 1.15M-file `upload_large_folder` trips
  HF's 2500-req/5min limit and stalls on commits (8h → 14/100 buckets, ~890k 429s).
  Pack into tar shards + `metadata.parquet` (per-sample contiguous byte range,
  `<key>/...` naming) → ~30 files, uploads in minutes; viewer reads a single
  sample via HTTP Range on the shard.
