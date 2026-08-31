#!/bin/bash
# One command to mirror the working tree into the GitHub repo and push:
#   scripts/sync_repo.sh "commit message"        (default message if omitted)
#
# What is synced (code + docs only):
#   scripts/ configs/ eval/ env.sh   ->  finetune/
#   REPORT.md                        ->  finetune/docs/REPORT.md
#   README.md                        ->  finetune/docs/PROJECT_README.md
#   llm_finetune_exps.md             ->  finetune/llm_finetune_exps.md
#   data/lf/dataset_info.json        ->  finetune/data_specs/dataset_info.json
#   data/md_max/report.json          ->  finetune/data_specs/md_max_report.json
#   pip freezes of the three envs    ->  finetune/env/requirements-*.txt   (with --envs)
# Never synced: data/, runs/, logs/, eval/out/, models/, secrets. Only finetune/ is ever touched.
set -uo pipefail
SRC=/wekafs/ict/hx_624/llm-ft
REPO=/wekafs/ict/hx_624/repos/3dcodeverse
DST=$REPO/finetune
MSG=${1:-"finetune: sync scripts, configs, eval stack and docs"}

[ -d "$DST" ] || { echo "repo clone missing at $REPO"; exit 1; }
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$SRC/scripts/" "$DST/scripts/"
# --delete on configs too: without it a config retired into configs/lf/archive/ was copied to its new
# location but the old copy lingered in the repo forever, so a tidy-up never actually reached GitHub.
rsync -a --delete --exclude '__pycache__' "$SRC/configs/" "$DST/configs/"
rsync -a --delete --exclude '__pycache__' --exclude 'out' --exclude '_test' --exclude 'ref_oracle' --exclude '*.pyc' "$SRC/eval/" "$DST/eval/"
cp "$SRC/env.sh" "$DST/env.sh"
cp "$SRC/REPORT.md" "$DST/docs/REPORT.md"
cp "$SRC/README.md" "$DST/docs/PROJECT_README.md" 2>/dev/null || true
cp "$SRC/llm_finetune_exps.md" "$DST/llm_finetune_exps.md" 2>/dev/null || true
cp "$SRC/data/lf/dataset_info.json" "$DST/data_specs/dataset_info.json" 2>/dev/null || true
cp "$SRC/data/md_max/report.json" "$DST/data_specs/md_max_report.json" 2>/dev/null || true
if [ "${2:-}" = "--envs" ]; then
  source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh
  for e in llmft lf vllm; do conda activate $e && pip freeze > "$DST/env/requirements-$e.txt"; done
  conda deactivate
fi

# refuse to publish anything that looks like a credential
PAT=$(printf '%s_%s|%s_%s|%s_%s' hf '[A-Za-z0-9]{30,}' ghp '[A-Za-z0-9]{30,}' github 'pat_[A-Za-z0-9_]{20,}')  # assembled at runtime so this file cannot match itself
if grep -rIlE "$PAT" "$DST" | head -1 | grep -q .; then
  echo "ABORT: a token-like string is present under finetune/ — not committing"; exit 1
fi

cd "$REPO"
git add -A -- finetune
if git diff --cached --quiet; then echo "[sync] nothing changed"; exit 0; fi
git commit -q -m "$MSG

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EtURHD29bBnAeA3FkJNnci"
export GIT_ASKPASS=/wekafs/ict/hx_624/.secrets/gh_askpass.sh GIT_TERMINAL_PROMPT=0
git fetch -q origin main && git rebase -q origin/main
git push -q origin main && echo "[sync] pushed: $(git log --oneline -1)"
