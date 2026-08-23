#!/bin/bash
# wait for self-train sampling to finish (GPU0 free), stop the self-train script (its training would collide), then full FT on GPUs 0,1,3 (ZeRO-3, no offload), then eval.
cd /wekafs/ict/hx_624/llm-ft
until grep -q "\[self\] .*sampling done" logs/self_train_round1.log 2>/dev/null; do sleep 20; done
for p in $(pgrep -u hx_624 -f "scripts/self_train_round.s[h]"); do kill $p; done; echo "[full3] self-train script stopped after sampling (exec/build/train to be run manually)"
sleep 20
until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0,1,3 | sort -n | tail -1)" -lt 10000 ]; do sleep 15; done
RUN=/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_full_v2_3gpu
echo "[full3] $(date) start full FT on GPUs 0,1,3 (ZeRO-3 no offload, grad_accum=3)"
GPUS=0,1,3 scripts/lf_train.sh configs/lf/qwen35_9b_full_sft_3gpu.yaml > logs/train_full_v2_3gpu.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[full3] TRAIN FAILED"; grep -E "Error|error" logs/train_full_v2_3gpu.log | tail -5 | cut -c1-200; exit 1; }
echo "[full3] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
for f in preprocessor_config.json video_preprocessor_config.json vocab.json merges.txt; do [ -f $RUN/$f ] || cp /wekafs/ict/hx_624/models/Qwen3.5-9B/$f $RUN/; done
GPUS=1 eval/run_eval_vllm.sh $RUN eval/out/qwen35_9b_full_v2_3gpu --no_think --max_new_tokens 6144 > logs/eval_full_v2_3gpu.log 2>&1
echo "[full3] $(date) eval done"; cat eval/out/qwen35_9b_full_v2_3gpu/summary.json; echo FULL3_DONE
