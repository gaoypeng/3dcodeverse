#!/bin/bash
# Re-execute + re-score every 3DCodeBench run that had failures caused by the missing scipy in Blender's Python.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
for RUN in qwen35_9b_lora_v7_ep4 md_big_T07_s2 qwen35_9b_full_md_bal qwen35_9b_lora_md_bal dpo_geo_md_xl_T07_s3 \
           md_xl_T07_s2 md_xl_T07_s3 qwen35_9b_full_v2_3gpu qwen35_9b_full_v3_4gpu qwen35_9b_lora_md_glsl \
           qwen35_9b_lora_md_xl_ck3150 qwen35_9b_lora_v6_detail; do
  D=eval/out/$RUN; [ -d $D ] || continue
  OLD=$(python3 -c "import json;print(json.load(open('$D/summary.json'))['exec_ok'])" 2>/dev/null || echo "?")
  python eval/run_bench.py --gen_dir $D --workers 20 --timeout 300 > /dev/null 2>&1
  python eval/metrics.py --gen_dir $D > /dev/null 2>&1
  NEW=$(python3 -c "import json;s=json.load(open('$D/summary.json'));print(f\"{s['exec_ok']}/{s['n_tasks']} = {100*s['exec_ok_rate']:.1f}% F@0.05(all) {s['f@0.05_mean_all(fail=0)']:.3f}\")" 2>/dev/null)
  echo "[rescore] $RUN: was exec_ok=$OLD -> now $NEW"
done
echo "[rescore] DONE"
