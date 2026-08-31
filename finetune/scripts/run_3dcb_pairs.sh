#!/bin/bash
# Build text->code, image->code and image+text->code for every 3DCodeBench subdirectory, cheapest first:
# the two factories have their tars on local disk, the instances are read from the Hub by byte range.
# The two *_full subdirs are the before-dedup variants (12,720 / 12,708 rows against 1,953 / 1,951 after).
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
OUT=/wekafs/ict/hx_624/hf_pairs
TARS=/wekafs/ict/hx_624/data/3dcodeverse_tars/3dcodebench
for SPEC in "factories_geo:$TARS/factories_geo" "factories_tex:$TARS/factories_tex" \
            "instances_geo:" "instances_tex:" "instances_geo_full:" "instances_tex_full:"; do
  SUB="${SPEC%%:*}"; LOCAL="${SPEC#*:}"
  if [ -d "$OUT/${SUB}_llamafactory_imgtext" ]; then echo "[3dcb] $SUB already built — skipping"; continue; fi
  # the *_full metadata is only on the Hub; fetch it once into the place the builder reads from
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
print("   fetched", sub)
PY
  fi
  echo "[3dcb] $(date) building $SUB"
  ARGS="--subdir 3dcodebench/$SUB --out $OUT --workers 12 --views 4"
  [ -n "$LOCAL" ] && ARGS="$ARGS --local_tars $LOCAL"
  python scripts/build_pairs_3dcodebench.py $ARGS 2>&1 | tail -8
done
echo "[3dcb] ALL DONE"
