# `configs/` — what is in here and which files matter

Every training run in this project is one YAML consumed by unmodified
[LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) (upstream `c4e09c7`, 0.9.6.dev0).
There is no custom trainer. If you want to reproduce a number from
[`docs/REPORT.md`](../docs/REPORT.md) or the results table in
[`llm_finetune_exps.md`](../llm_finetune_exps.md), you need exactly one file from here.

**Start with the eight configs in [§1](#1-the-configs-that-produced-the-headline-models).** Everything
else is an ablation, a failed branch, or a disposable merge recipe.

---

## 0. How one experiment flows

```
configs/lf/lora_<name>.yaml   ──lf_train.sh──▶  runs/lf_<model>_<name>/        (adapter + train_results.json)
configs/lf/export_<name>.yaml ──lf_export.sh─▶  runs/lf_<model>_<name>/merged/ (adapter merged into the base)
                                    │
                              eval/eval_all_run2.sh  ──▶  eval/out/<tag>/summary.json
```

* `scripts/lf_train.sh <cfg>` — train. `GPUS=0,1,2,3` selects devices; extra `key=value` args override the YAML.
* `scripts/lf_export.sh <cfg>` — merge a LoRA adapter into its base model so vLLM can load it.
* `eval/eval_all_run2.sh <merged_dir> <tag>` — run all six dialect suites. **This is the current evaluator**;
  `eval/eval_all.sh` is the older one, kept only because `scripts/run_27b.sh` still calls it.

## Naming scheme

```
lora_[<base>_]<experiment>.yaml     SFT.  No base tag  = Qwen3.5-9B.  `27b_` = Qwen3.8-27B.  `9b_` is used
                                    on the later 9B runs once 27B runs existed and the prefix stopped being implicit.
dpo_<experiment>.yaml               Preference tuning on top of an already-merged SFT model.
export_<experiment>.yaml            Merge recipe: which adapter, into which base, to which directory.
full_*.yaml / qwen35_9b_full_*.yaml Full-parameter SFT (no LoRA).
```

The `<experiment>` suffix is stable across the three families: `lora_md_xl.yaml` trains it,
`export_md_xl.yaml` merges it, `dpo_geo_md_xl.yaml` tunes it further. The run directory is
`runs/lf_qwen35_9b_<experiment>` (9B) or `runs/lf_qwen38_27b_<experiment>` (27B).

---

## 1. The configs that produced the headline models

Numbers are 3DCodeBench exec-rate / F@0.05(all) unless noted; full table in
[`llm_finetune_exps.md`](../llm_finetune_exps.md) §0.

| config | base | what it is | result |
|---|---|---|---|
| **`lora_27b_v2.yaml`** | 27B | 6-dialect LoRA, 104k pairs, Blender raised to 40% + 15.7k execution-verified bootstrapped samples | **93.4% / 0.380** — best geometric fidelity anywhere (Blender F@0.05 0.890, Chamfer 0.049) |
| **`dpo_27b.yaml`** | ← that, merged | execution-feedback DPO, 756 pairs | three.js 92.5% → **100%**, OpenSCAD +2, GLSL +2, 3DCodeBench −1.9 |
| **`lora_md_xl.yaml`** | 9B | 6-dialect LoRA, 270k pairs — the largest 9B mix | 90.1% / 0.346; **CadQuery 97.5%**, the best of any model |
| **`dpo_geo_md_xl.yaml`** | ← that, merged | geometry-feedback DPO (pairs ranked by F-score) | first lever that moved *fidelity*: Blender F@0.05(ok) 0.387 → **0.800**, Chamfer −12% |
| **`lora_v1.yaml`** | 9B | the first run: LoRA on 5.2k Blender samples | 0% → 75–82%. The single biggest jump in the project |
| **`dpo_exec_v1.yaml`** → **`dpo_exec_v2.yaml`** | ← that, merged | two rounds of execution-feedback DPO | 75% → 93.9% → **96.2%**, the highest executability reached |
| **`lora_md_big.yaml`** + `dpo_md_big.yaml` | 9B | 102k-pair mix, then DPO | 84.9%; the mid-size point on the data-scaling curve |
| **`lora_27b_v3.yaml`** | 27B | CadQuery doubled (36k→65k), rank 32, cutoff 3072 | **negative result worth keeping**: CadQuery flat (86.5→85.0) and every other suite got worse |

Two configs record the results that stopped a line of work, and are worth reading before repeating it:
`lora_md_max_9b.yaml` (caption augmentation — 90.1% → 34.4% at equal token budget) and
`qwen35_9b_full_sft.yaml` / `full_md_bal.yaml` (full-parameter FT — equal to LoRA at 3× the memory).

## 2. The families

### `lora_*.yaml` — 52 files, supervised finetuning

One per experiment, each with a `runs/` directory. They fall into groups:

* **`lora_v1` … `lora_v13_boot`** — the 9B Blender-only ladder: repeats (`v1_rep`), in-distribution data
  (`v2_indist`, `v2b_indist16k`), other sources (`v3_bio`, `v4_cq`), prompt style (`v5_instr`, `v6_detail`),
  hyperparameters (`v7_ep4`, `v8_r16`, `v9_lr2e4`), other bases (`v10_4b`, `v12_qwen3_8b`), and
  self-training (`v11_selftrain`, `v11b_selfprompts`, `v13_boot`).
* **`lora_md_*`** — the six-dialect mixes, in size order `md_mixed` (26k) → `md_big` (102k) → `md_xl` (270k),
  plus single-dialect probes (`md_blender`, `md_cadquery`, `md_glsl`, `md_openscad`, `md_threejs`) and
  rebalancing attempts (`md_bal`, `md_max_9b`, `md_v3`).
* **`lora_9b_*`** — the later 9B work: leakage control (`noleak`, `clean`), learning-rate sweep
  (`bal_lr1e4`, `bal_lr3e5`), multimodal (`mm_img`, `mm_text`, `mm_img_text`, `mm1_img_text`, `mmvis`,
  `mmmix2`, `img`, `img4`, `imgmix`), and full-parameter (`full`, `full_resume`).
* **`lora_27b_*`** — the 27B line: `md_z3` / `ddp` (ZeRO-3 vs DDP — DDP is 7.8× faster for LoRA),
  then `v2` … `v5`, `clean`, `mm_img_text`.

### `dpo_*.yaml` — 10 files, preference tuning

Always applied on top of an already-merged SFT model (`model_name_or_path` points at a `runs/*/merged`
directory, not at a base model). Three kinds, distinguished by how the preference pairs were built:

* **execution feedback** — sample K completions, run them, pair (OK, FAIL): `dpo_exec_v1`, `dpo_exec_v2`,
  `dpo_md_mixed`, `dpo_md_big`, `dpo_md_xl`, `dpo_27b`.
* **geometry feedback** — pairs ranked by F-score against the ground-truth mesh: `dpo_geo_md_xl`.
* **stop-token / truncation repair** — `dpo_stop`, `dpo_stop_r2`, `dpo_best_stop`.

### `export_*.yaml` — 34 files, disposable merge recipes

**These are generated artefacts, not authored configs.** Each is `export_lora_template.yaml` with three
lines rewritten by `sed` inside the runner script that needs it, e.g. in `scripts/run_27b_v3.sh`:

```bash
sed -e "s|model_name_or_path:.*|model_name_or_path: /wekafs/.../Qwen3.8-27B|" \
    -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" \
    -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" \
    configs/lf/export_lora_template.yaml > configs/lf/export_27b_v3.yaml
```

They carry no experimental information beyond "which adapter was merged where" — which the run directory
name already tells you. Any of them can be recreated by re-running its script.
The 34 kept here are the ones still named by a script; **`export_lora_template.yaml` must never be
deleted** — 49 scripts derive their merge config from it.

A few export configs are read by a script that does *not* also write them (`export_v1.yaml`,
`export_v6_detail.yaml`, `export_md_xl.yaml`, `export_md_mixed.yaml`, `export_md_big.yaml`,
`export_md_threejs.yaml`, `export_27b_max.yaml`, `export_27b_v2.yaml`, `export_dpo_exec_v1_r2.yaml`,
`export_dpo_exec_v2.yaml`, `export_9b_full.yaml`). Deleting one of those *does* break its caller.

### Other files

| file | purpose |
|---|---|
| `ds_zero2.json`, `ds_zero3.json`, `lf/ds_z3_offload.json` | DeepSpeed stage configs. `ds_z3_offload.json` is the 2-GPU fallback (ZeRO-3 + CPU optimizer offload) used by the full-FT scripts. |
| `qwen35_9b_full_sft{,_3gpu,_4gpu}.yaml`, `full_md_bal.yaml` | full-parameter SFT, 1/3/4-GPU variants |
| `qwen35_9b_lora_sft.yaml` | the original hand-written LoRA config; superseded by `lora_v1.yaml` but referenced by `README.md` and `scripts/after_lora_v2.sh` |
| `qwen35_9b_preprocess.yaml` | tokenise-and-cache only, no training |

### `lf/archive/` — 42 unreferenced generated export configs

Merge recipes for experiments that are finished, whose script no longer names them. Kept rather than
deleted because they are the only record of *which checkpoint step* was merged in the two checkpoint
sweeps (`export_md_xl_ck450` … `ck3600`, `export_md_big_ck2200` … `ck3292`). Nothing reads them; they are
safe to delete outright if the folder ever needs to shrink again.

---

## 3. Known quirks

* **`overwrite_output_dir` is set to a path instead of `true`** in `dpo_27b`, `dpo_exec_v2`, `dpo_md_big`,
  `dpo_md_mixed`, `dpo_md_xl`. LLaMA-Factory reads it as truthy so the runs behaved as intended, but the
  value is meaningless — do not copy it into a new config.
* **Six configs point at `runs/*/merged` directories that no longer exist** (`dpo_md_big`, `dpo_md_mixed`,
  `dpo_md_xl` and their `export_dpo_*` counterparts). The merged models were deleted to reclaim disk. This
  is handled: each caller re-creates the merge first, e.g.
  `[ -d $BASE ] || scripts/lf_export.sh configs/lf/export_md_big.yaml`.
* **`scripts/run_27b_v2.sh` does not train `lora_27b_v2.yaml`.** It trains `lora_27b_ddp.yaml` into
  `runs/lf_qwen38_27b_lora_md_max`. The config called `v2` is trained by **`scripts/run_27b_v2b.sh`**.
* **`export_9b_full.yaml` and `export_mmvis.yaml` are named by scripts but absent** from this directory.
  Both are written by their own script before use, so this is harmless.
* Configs are near-identical by design: every `export_*` differs from its siblings in 2 lines, and
  `lora_v1` / `lora_v1_rep` / `lora_v7_ep4` / `lora_v9_lr2e4` differ in 1–2 hyperparameters. That is the
  point — one variable per experiment.

## 4. Editing rules

1. **Never edit a config while a job is reading it.** Check `pgrep -af llamafactory-cli` first. The same
   applies doubly to shell scripts: bash reads a script incrementally as it executes, so editing a running
   one corrupts it mid-run. This has already broken two evaluations in this project.
2. New experiment = new file. Do not mutate an existing config — it is the record of a run in `runs/`.
3. Do not hand-write an `export_*.yaml`. Copy the `sed` block from a neighbouring runner script.
