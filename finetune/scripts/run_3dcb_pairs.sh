#!/bin/bash
# Build text->code, image->code and image+text->code for every 3DCodeBench subdirectory.
# Renders come from a sequential scan of each tar, NOT from metadata's byte offsets: for instances_geo only 577
# of 1,953 offsets point at a real tar header (the archives were repacked after the metadata was written), and
# trusting them silently cost 70% of the image pairs.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
OUT=/wekafs/ict/hx_624/hf_pairs
for SUB in instances_tex instances_geo_full instances_tex_full; do
  # "the folder exists" is not the same as "it was built correctly": instances_tex was skipped by that test
  # while holding the 578-of-1,951 image pairs the broken byte offsets produced. Require the img rows to match
  # the source count before treating a subdir as done.
  Q="$OUT/${SUB}_llamafactory_img/qc.json"
  if [ -f "$Q" ] && python3 -c "import json,sys; q=json.load(open('$Q')); sys.exit(0 if q['rows'] >= 0.95*q['source_samples'] else 1)"; then
    echo "[3dcb] $SUB already built with full render coverage — skipping"; continue
  fi
  M=/wekafs/ict/hx_624/data/3dcodeverse_meta/3dcodebench/$SUB/metadata.parquet
  if [ ! -f "$M" ]; then
    echo "[3dcb] fetching metadata for $SUB"
    python - "$SUB" <<'PY'
import os, sys, shutil
from huggingface_hub import hf_hub_download
sub = sys.argv[1]
p = hf_hub_download("ilabai/3dcodeverse", f"3dcodebench/{sub}/metadata.parquet", repo_type="dataset", token=os.environ["HF_TOKEN"])
d = f"/wekafs/ict/hx_624/data/3dcodeverse_meta/3dcodebench/{sub}"
os.makedirs(d, exist_ok=True); shutil.copy(p, f"{d}/metadata.parquet")
PY
  fi
  echo "[3dcb] $(date) building $SUB"
  python scripts/build_pairs_3dcodebench.py --subdir 3dcodebench/$SUB --out $OUT --workers 16 --views 4 2>&1 | tail -6
done
echo "[3dcb] ALL DONE"
