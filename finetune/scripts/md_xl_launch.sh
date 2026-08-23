#!/bin/bash
cd /wekafs/ict/hx_624/llm-ft; source env.sh
echo "[md_xl] $(date) build data"
python scripts/build_md_xl.py 2>&1 | grep -v Warning | tail -1 | tee logs/build_md_xl.stats
python scripts/to_llamafactory.py --data_dir /wekafs/ict/hx_624/llm-ft/data/md_xl --name md_xl 2>&1 | tail -3 | cut -c1-200
# ~10 checkpoints over the run: steps = tokens / (4 GPUs x 2 accum x 8192)
python3 - <<'PY'
import re, yaml
s=open("logs/build_md_xl.stats").read(); tok=float(re.search(r"tokens/epoch ([0-9.]+)M", s).group(1))*1e6
steps=tok/(8*8192); save=max(50, int(round(steps/10/50))*50)
p="configs/lf/lora_md_xl.yaml"; c=yaml.safe_load(open(p)); c["save_steps"]=save; c["eval_steps"]=save; c["save_total_limit"]=14
yaml.safe_dump(c, open(p,"w"), sort_keys=False); print(f"[md_xl] est. steps {steps:.0f}, save/eval every {save} steps")
PY
echo "BUILD_MD_XL_DONE"
G=0,1,2,3; echo "[md_xl] using GPUs $G"
scripts/run_md_exp.sh $G md_xl 2>&1 | tee -a logs/exp_md_xl.log
grep -q "\[md md_xl\] DONE" logs/exp_md_xl.log || { echo "[md_xl] pipeline failed, not sweeping"; exit 1; }
echo "[md_xl] $(date) starting checkpoint sweep on GPU 1"
GPUS=1 scripts/md_xl_ckpt_sweep.sh > logs/md_xl_ckpt_sweep.log 2>&1
echo "[md_xl] ALL DONE"
