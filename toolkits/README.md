# toolkits/ — general data CLI and corpus curation

These tools turn contributed or source projects into the 3DCodeVerse corpus: validate, execute,
render, deduplicate, attach metadata, and package the results for publication.

| folder | stage | what it does |
|---|---|---|
| [`3dcode_cli/`](3dcode_cli/) | contributor → corpus | the installable `3dcode-data` CLI: validate, fingerprint/dedupe, execute, render and push a project to the staging bucket. Per-dialect modules for Blender-Python, CadQuery, build123d, FreeCAD and OpenSCAD |
| [`curation/`](curation/) | raw → corpus | per-source curation: convert a source's native format to runnable projects, execute/render them, compute ground-truth meshes, dedupe, pack to tar+parquet, upload to the Hub |

LLaMA-Factory conversion, training-data QC, multimodal pairs, and training mixes live in
[`../finetune/llamafactory/`](../finetune/llamafactory/). Its README contains the training-data script
catalog and usage examples. Training configs, environments, and launchers remain under `finetune/`.

## 3dcode_cli/

`3dcode_cli/` is a repository copy of
**[gaoypeng/3dcode_toolkit](https://github.com/gaoypeng/3dcode_toolkit)**. It can be installed from
either location:

```bash
pipx install "git+https://github.com/gaoypeng/3dcode_toolkit"   # upstream
pipx install ./toolkits/3dcode_cli                          # from this repository's root
```

If both copies are edited they can drift; coordinate changes with the upstream repository.

## curation/ — per source

| source | scripts | notes |
|---|---|---|
| `deepcad/` | `convert_vec_to_cq.py`, `batch_convert.py`, `cq_check{,_batch}.py`, `gen_gt_stl.py`, `iou_compute.py`, `render_all.py`, `dedup_vec.py`, `attach_text.py`, `pack_tar_parquet.py`, `finalize.py`, `upload_to_hf.py` | DeepCAD vector sequences → CadQuery programs, executed and checked, GT meshes + IoU, captions attached, packed and uploaded |
| `articraft/` | `articraft_cq.py`, `assemble_shim.py`, … | articulated assets → CadQuery / URDF projects |

The shape of a curation pass is **convert → execute → render → compute ground truth → dedupe →
attach text → pack → upload**. Scripts retain intermediate files; inspect their completion checks
before rerunning a partially completed pass. Some scripts contain paths from the original workstation;
adapt their input, output, and tool paths before running them elsewhere.
