#!/usr/bin/env bash
# 3dcodeverse harness — idempotent installer / updater.
#
#   bash harness/scripts/setup.sh [options]
#
# Does, in order: version checks (python >= 3.11, node >= 20) -> `pip install -e
# harness[<extras>]` -> `npm ci` in runtime_js *only when the lockfile moved* ->
# puppeteer chrome download (no-op when cached) -> `3dcodeverse doctor`.
# Re-running is safe: every step is a no-op when it is already satisfied.
# Full guide: docs/INSTALL.md
set -euo pipefail

MIN_PY_MINOR=11      # python 3.11+
MIN_NODE_MAJOR=20    # node 20+

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="$(cd "$HERE/.." && pwd)"
RUNTIME_JS="$HARNESS/runtime_js"

PY="${PYTHON:-python3}"
EXTRAS="all,dev"
DO_PYTHON=1
DO_NODE=1
DO_CHROME=1
DO_DOCTOR=1
FORCE_NPM=0
DOCTOR_ARGS=()

usage() {
  cat <<'USAGE'
usage: setup.sh [options]

  --extras LIST     pip extras to install (default: all,dev; "" = none)
  --python BIN      python interpreter to install into (default: $PYTHON or python3)
  --no-python       skip the pip install step
  --no-node         skip `npm ci` in runtime_js
  --no-chrome       skip the puppeteer chrome download
  --no-doctor       skip the final `3dcodeverse doctor`
  --force-npm       run `npm ci` even when node_modules looks up to date
                    (WARNING: npm ci deletes node_modules first — do not use
                    while renders/benches are running against this tree)
  --no-gpu          pass --no-gpu to doctor (skips the headless-Chrome probe)
  -h, --help        this message
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --extras) EXTRAS="${2-}"; shift 2 ;;
    --extras=*) EXTRAS="${1#*=}"; shift ;;
    --python) PY="${2-}"; shift 2 ;;
    --python=*) PY="${1#*=}"; shift ;;
    --no-python) DO_PYTHON=0; shift ;;
    --no-node) DO_NODE=0; shift ;;
    --no-chrome) DO_CHROME=0; shift ;;
    --no-doctor) DO_DOCTOR=0; shift ;;
    --force-npm) FORCE_NPM=1; shift ;;
    --no-gpu) DOCTOR_ARGS+=(--no-gpu); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "setup.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------- prerequisites
step "prerequisites"

command -v "$PY" >/dev/null 2>&1 || die "python interpreter not found: $PY (install python 3.11+ or pass --python)"
PY_VER="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
"$PY" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, $MIN_PY_MINOR) else 1)" \
  || die "python 3.$MIN_PY_MINOR+ required, found $PY_VER ($PY)"
info "python   $PY_VER  ($("$PY" -c 'import sys; print(sys.executable)'))"

command -v git >/dev/null 2>&1 || die "git not found (every round is a git commit in the run workspace)"
info "git      $(git --version | awk '{print $3}')"

NODE_OK=1
if command -v node >/dev/null 2>&1; then
  NODE_VER="$(node --version)"            # vNN.NN.NN
  NODE_MAJOR="${NODE_VER#v}"; NODE_MAJOR="${NODE_MAJOR%%.*}"
  if [ "$NODE_MAJOR" -lt "$MIN_NODE_MAJOR" ]; then
    NODE_OK=0
    info "node     $NODE_VER  (need >= $MIN_NODE_MAJOR — three.js / scene / GLB tracks will not run)"
  else
    info "node     $NODE_VER"
  fi
else
  NODE_OK=0
  info "node     not found (three.js / scene / GLB export tracks will not run)"
fi

if command -v blender-5.0 >/dev/null 2>&1 || command -v blender >/dev/null 2>&1 || [ -n "${CV3D_BINARIES__BLENDER:-}" ]; then
  info "blender  found"
else
  info "blender  not found (optional: blender / urdf_blender tracks unavailable — docs/INSTALL.md §6)"
fi
command -v ffmpeg >/dev/null 2>&1 && info "ffmpeg   found" || info "ffmpeg   not found (optional: turntables fall back to animated GIF)"

# ------------------------------------------------------------------ python pkg
if [ "$DO_PYTHON" -eq 1 ]; then
  step "pip install -e $HARNESS${EXTRAS:+[$EXTRAS]}"
  if [ -n "$EXTRAS" ]; then
    "$PY" -m pip install -e "$HARNESS[$EXTRAS]"
  else
    "$PY" -m pip install -e "$HARNESS"
  fi
else
  step "pip install (skipped: --no-python)"
fi

# -------------------------------------------------------------------- runtime_js
if [ "$DO_NODE" -eq 1 ] && [ "$NODE_OK" -eq 1 ]; then
  step "runtime_js node_modules (three + puppeteer)"
  command -v npm >/dev/null 2>&1 || die "npm not found (it ships with node)"
  [ -f "$RUNTIME_JS/package-lock.json" ] || die "missing $RUNTIME_JS/package-lock.json"
  STAMP="$RUNTIME_JS/node_modules/.package-lock.json"
  if [ "$FORCE_NPM" -eq 1 ] || [ ! -d "$RUNTIME_JS/node_modules" ] || [ ! -f "$STAMP" ] \
     || [ "$RUNTIME_JS/package-lock.json" -nt "$STAMP" ]; then
    info "running npm ci (this deletes and re-creates node_modules)"
    ( cd "$RUNTIME_JS" && npm ci )
  else
    info "up to date — node_modules matches package-lock.json (use --force-npm to reinstall)"
  fi

  if [ "$DO_CHROME" -eq 1 ]; then
    step "puppeteer chrome (~/.cache/puppeteer)"
    ( cd "$RUNTIME_JS" && npx --no puppeteer browsers install chrome )
  fi
else
  step "runtime_js (skipped: no usable node, or --no-node)"
fi

# ----------------------------------------------------------------------- doctor
if [ "$DO_DOCTOR" -eq 1 ]; then
  step "3dcodeverse doctor"
  if command -v 3dcodeverse >/dev/null 2>&1; then
    3dcodeverse doctor ${DOCTOR_ARGS[@]+"${DOCTOR_ARGS[@]}"} || true
  else
    info "3dcodeverse not on PATH — falling back to python -m codeverse.cli.main"
    "$PY" -m codeverse.cli.main doctor ${DOCTOR_ARGS[@]+"${DOCTOR_ARGS[@]}"} || true
  fi
fi

printf '\n\033[1mdone.\033[0m  Next: docs/INSTALL.md §10 (5-minute smoke).\n'
