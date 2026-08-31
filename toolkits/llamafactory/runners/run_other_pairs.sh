#!/bin/bash
# The remaining parquet-backed sources. deepcad and bioinspired3d have renders, so they get all three pair types;
# articraft (URDF) and thingiverse have none, so only text->code is produced and the img/imgtext folders are
# simply not written -- an empty image dataset would be worse than an absent one.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate llmft
OUT=/wekafs/ict/hx_624/hf_pairs_other
mkdir -p $OUT
build () {
  local SUB=$1 DIA=$2
  local Q="$OUT/$(basename $SUB)_llamafactory_text/qc.json"
  [ -f "$Q" ] && { echo "[oth] $SUB already built"; return; }
  python scripts/build_pairs_3dcodebench.py --subdir "$SUB" --dialect "$DIA" --out $OUT \
    --workers 12 --views 4 --img_root /wekafs/ict/hx_624/data/pair_images > logs/pairs_oth_$(basename $SUB).log 2>&1
  echo "[oth] $(date +%H:%M) $SUB $(grep -E '^\[pairs\] (text|img|imgtext)' logs/pairs_oth_$(basename $SUB).log | tr '\n' ' ' | tr -s ' ' | cut -c1-110)"
}
export -f build; export OUT
LIST=""
for d in /wekafs/ict/hx_624/data/3dcodeverse_meta/deepcad/*/metadata.parquet; do LIST="$LIST deepcad/$(basename $(dirname $d)):cadquery"; done
for d in /wekafs/ict/hx_624/data/3dcodeverse_meta/bioinspired3d/*/metadata.parquet; do LIST="$LIST bioinspired3d/$(basename $(dirname $d)):blender"; done
for d in /wekafs/ict/hx_624/data/3dcodeverse_meta/articraft/*/metadata.parquet; do LIST="$LIST articraft/$(basename $(dirname $d)):cadquery"; done
for d in /wekafs/ict/hx_624/data/3dcodeverse_meta/thingiverse/*/metadata.parquet; do LIST="$LIST thingiverse/$(basename $(dirname $d)):openscad"; done
echo "[oth] $(echo $LIST | wc -w) subdirs"
echo $LIST | tr ' ' '\n' | xargs -P ${PAR:-8} -I{} bash -c 'IFS=: read -r s d <<< "$1"; build "$s" "$d"' _ {}
echo "[oth] ALL DONE"
