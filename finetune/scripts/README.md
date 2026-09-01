# `scripts/` — index

88 shell scripts and 32 Python scripts. Most are **one-off runners**: a single experiment's
"train → merge → evaluate" recipe, kept so the run in `runs/` can be reproduced. You almost never need to
read one unless you are reproducing that specific experiment.

The files below are the ones that are actually infrastructure.

## The five you will use

| script | what it does |
|---|---|
| `lf_train.sh <cfg> [k=v ...]` | train. `GPUS=0,1,2,3` picks devices; extra `key=value` args override the YAML. Called by 53 other scripts. |
| `lf_export.sh <cfg>` | merge a LoRA adapter into its base so vLLM can load it. Called by 57 other scripts. |
| `free_gpus.sh [min_free_mb]` | list GPUs with enough free memory. The scheduling primitive everything waits on. |
| `supervisor2.sh` | the job queue. Reads `queue.txt` (`<label>\|<command>` per line), runs one job at a time when the GPUs are free, records completions in `logs/.supervisor_done`, logs to `logs/supervisor.log`. |
| `sync_repo.sh ["msg"]` | mirror `scripts/ configs/ eval/` + docs into `repos/3dcodeverse/finetune/` and push. |

`queue.txt` is the live work list — append a line to schedule a job; `supervisor2.sh` picks it up.

## Runner naming

```
run_<model>_<experiment>.sh   train + merge + evaluate one experiment end to end
run_exp*.sh, run_md_exp.sh    parameterised runners: pass an experiment name, they generate the config
*_dpo_round.sh                build preference pairs by executing samples, then DPO, then evaluate
*_ckpt_sweep.sh               merge and evaluate every checkpoint of one run
eval_*.sh, *_eval.sh          evaluation only, against an already-merged model
build_*.py                    dataset construction (see `docs/REPORT.md` §2)
```

Generation of a merge config is inlined in each runner as a `sed` over
`configs/lf/export_lora_template.yaml` — see [`configs/README.md`](../configs/README.md) §2.

## Two pairs that look like duplicates

* **`supervisor2.sh` supersedes `archive/supervisor.sh`.** Identical except that `supervisor2.sh`
  re-checks the done-list after its GPU wait, which can last hours. Without that check a job whose
  label was completed by another process meanwhile got run a second time (this happened to
  `mm_multisuite`). Use `supervisor2.sh`.
* **`eval/eval_all_run2.sh` supersedes `eval/eval_all.sh`** and is what all 20 current callers use.
  `eval_all.sh` is kept only because `run_27b.sh` still names it, and because `docs/REPORT.md` quotes it
  when reporting the zero-shot baselines. The two differ by one comment line; they are functionally
  identical.

## `archive/`

Nine scripts, referenced by nothing and superseded:

* `supervisor.sh` — see above.
* `after_lora.sh` — superseded by `after_lora_v2.sh` (vLLM merge-and-eval path with an HF fallback).
  `logs/after_lora.log` is 0 bytes: it never produced output.
* `explore_sched.sh` — the first 8-hour scheduling plan, replaced by `explore_sched2.sh` the same day
  when GPU availability changed. `logs/explore_sched.log` is 0 bytes; `explore_sched2.log` is not.
* `run_full_ft2.sh` … `run_full_ft7.sh` — six successive one-off edits of `run_full_ft.sh`, each changing
  only which log line it waits for before starting. They produced no run of their own.

## Do not edit a running script

Bash reads a script incrementally *as it executes it*, so editing one mid-run corrupts the run. This has
already broken two 9B evaluations in this project. Check `pgrep -af 'scripts/'` before touching anything,
and copy to a new filename instead of editing in place.
