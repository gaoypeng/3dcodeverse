#!/bin/bash
# Cross prompt-style eval: 3DCodeBench prompt_description.txt (appearance descriptions) instead of prompt_instruction.txt, for base / v1 / v6_detail
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft; export PROMPTS=/wekafs/ict/hx_624/llm-ft/data/sft_v1/bench_prompts_desc.jsonl
until grep -q "\[dpo\] DONE\|FAILED" logs/exp_dpo_exec_v1.log 2>/dev/null; do sleep 30; done
until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 2,3 | sort -n | tail -1)" -lt 10000 ]; do sleep 20; done
echo "[desc] $(date) start"
[ -d runs/lf_qwen35_9b_lora_v1/merged ] || scripts/lf_export.sh configs/lf/export_v1.yaml > logs/export_v1_desc.log 2>&1
GPUS=2 eval/run_eval_vllm.sh runs/lf_qwen35_9b_lora_v1/merged eval/out/desc_qwen35_9b_lora_v1 --no_think --max_new_tokens 6144 > logs/eval_desc_v1.log 2>&1 &
GPUS=3 eval/run_eval_vllm.sh /wekafs/ict/hx_624/models/Qwen3.5-9B eval/out/desc_qwen35_9b_base --no_think --max_new_tokens 6144 > logs/eval_desc_base.log 2>&1 &
wait; echo "[desc] v1/base done"; cat eval/out/desc_qwen35_9b_lora_v1/summary.json | tr -d '\n'; echo; cat eval/out/desc_qwen35_9b_base/summary.json | tr -d '\n'; echo
scripts/lf_export.sh configs/lf/export_v6_detail.yaml > logs/export_v6_desc.log 2>&1 && GPUS=2 eval/run_eval_vllm.sh runs/lf_qwen35_9b_lora_v6_detail/merged eval/out/desc_qwen35_9b_lora_v6_detail --no_think --max_new_tokens 6144 > logs/eval_desc_v6.log 2>&1; echo "[desc] v6 done"; cat eval/out/desc_qwen35_9b_lora_v6_detail/summary.json | tr -d '\n'; echo
rm -rf runs/lf_qwen35_9b_lora_v6_detail/merged runs/lf_qwen35_9b_lora_v1/merged; echo "[desc] DONE"
