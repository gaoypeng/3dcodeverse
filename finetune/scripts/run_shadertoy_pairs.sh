#!/bin/bash
# All three pair types for every shadertoy shard. Renders come from scanning each shard's tars (the byte offsets
# in metadata are not reliable), and the code comes from whichever verified parquet exists for that shard --
# *_llamafactory_flat for the 68 that could be flattened, the separately verified tree for the other 44 -- so the
# pairs carry a compiles column instead of being unverified GLSL.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
OUT=/wekafs/ict/hx_624/hf_pairs_shadertoy
VER="/wekafs/ict/hx_624/hf_subdirs/shadertoy,/wekafs/ict/hx_624/hf_subdirs_verified/shadertoy"
PAR=${PAR:-4}
mkdir -p $OUT
build () {
  local SUB=$1
  local Q="$OUT/${SUB}_llamafactory_img/qc.json"
  if [ -f "$Q" ]; then echo "[st] $SUB already built — skipping"; return; fi
  python scripts/build_pairs_3dcodebench.py --subdir shadertoy/$SUB --dialect glsl \
    --out $OUT --workers 12 --views 4 --verified_from "$VER" > logs/pairs_st_$SUB.log 2>&1
  local line=$(grep -E "^\[pairs\] (text|img|imgtext)" logs/pairs_st_$SUB.log | tr '\n' ' ' | tr -s ' ')
  echo "[st] $(date +%H:%M) $SUB ${line:-FAILED: $(grep -iE 'error|Traceback' logs/pairs_st_$SUB.log | tail -1 | cut -c1-90)}"
}
export -f build; export OUT VER
SHARDS=$(ls -d /wekafs/ict/hx_624/data/3dcodeverse_meta/shadertoy/*/ | xargs -n1 basename)
echo "[st] $(echo "$SHARDS" | wc -l) shards, $PAR at a time"
echo "$SHARDS" | xargs -P $PAR -I{} bash -c 'build "$@"' _ {}
echo "[st] ALL DONE"
