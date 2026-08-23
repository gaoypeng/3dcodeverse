#!/bin/bash
set -x
source /wekafs/ict/hx_624/llm-ft/env.sh; cd /wekafs/ict/hx_624/llm-ft
python scripts/prep_data.py --out data/sft_v2_indist --include_test_factories            > logs/prep_v2.log 2>&1 && python scripts/to_llamafactory.py --data_dir data/sft_v2_indist --name v2_indist
python scripts/prep_data.py --out data/sft_v3_bio --sources bio3d                         > logs/prep_v3.log 2>&1 && python scripts/to_llamafactory.py --data_dir data/sft_v3_bio --name v3_bio
python scripts/prep_data.py --out data/sft_v4_cq --sources bio3d,fac,distill,thingi50,deepcad --deepcad_n 6000 > logs/prep_v4.log 2>&1 && python scripts/to_llamafactory.py --data_dir data/sft_v4_cq --name v4_cq
python scripts/prep_data.py --out data/sft_v5_instr --prompt_fields instruction:1        > logs/prep_v5.log 2>&1 && python scripts/to_llamafactory.py --data_dir data/sft_v5_instr --name v5_instr
python scripts/prep_data.py --out data/sft_v6_detail --prompt_fields detailed:1          > logs/prep_v6.log 2>&1 && python scripts/to_llamafactory.py --data_dir data/sft_v6_detail --name v6_detail
for v in v2_indist v3_bio v4_cq v5_instr v6_detail; do python scripts/make_lora_cfg.py $v $v; done
for v in v2_indist v3_bio v4_cq v5_instr v6_detail; do echo "== $v"; grep -E '"n_train"|"n_val"' data/sft_$v/stats.json | tr -d '\n'; echo; done
echo BUILD_VARIANTS_DONE
