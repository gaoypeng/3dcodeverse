#!/usr/bin/env bash
# One-shot environment setup for 3dcodeverse_eval on a Linux box (no sudo needed).
#   bash llm/setup.sh            # everything below
#   C3D_TOOLS=/opt/c3d-tools bash setup.sh  # tools elsewhere
# Installs: conda env `cv3d-eval` (vLLM, executors, metrics), Blender 5.0.1, OpenSCAD nightly AppImage
# (+ libglvnd unpacked locally), glslang 16.5, Playwright Chromium, scipy/shapely inside Blender's python.
# Re-runnable: every step skips what already exists.  Afterwards: `python -m llm.config`.
set -euo pipefail

TOOLS="${C3D_TOOLS:-$HOME/3dcodeverse_data/tools}"
ENV_NAME="${C3D_ENV:-cv3d-eval}"
XLIBS="${C3D_XLIBS:-$HOME/.local/xlibs/usr/lib/x86_64-linux-gnu}"
mkdir -p "$TOOLS" "$HOME/.local/bin"

log() { printf '\n\033[1;34m[setup]\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------------------------- conda env
if ! conda env list | grep -qE "^${ENV_NAME}\s"; then
  log "creating conda env $ENV_NAME"
  conda create -y -n "$ENV_NAME" python=3.12
fi
PY="$(conda run -n "$ENV_NAME" python -c 'import sys; print(sys.executable)')"
log "python: $PY"
"$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q vllm trimesh scipy pyarrow pandas datasets huggingface_hub openai anthropic cadquery playwright \
                        pyyaml tqdm pillow "transformers>=4.50" accelerate pytest
"$PY" -m playwright install chromium

# ---------------------------------------------------------------------------------------------- Blender 5.0.1
if [ ! -x "$TOOLS/blender-5.0.1-linux-x64/blender" ]; then
  log "downloading Blender 5.0.1"
  curl -L -o "$TOOLS/blender.tar.xz" https://download.blender.org/release/Blender5.0/blender-5.0.1-linux-x64.tar.xz
  tar -xJf "$TOOLS/blender.tar.xz" -C "$TOOLS" && rm "$TOOLS/blender.tar.xz"
fi
BPY="$(ls -d "$TOOLS"/blender-5.0.1-linux-x64/5.0/python/bin/python3* | head -1)"
"$BPY" -m ensurepip >/dev/null 2>&1 || true
"$BPY" -m pip install -q "scipy>=1.14" shapely      # 15/212 reference scripts import scipy; one needs shapely
# libSM / libICE / libglvnd are often missing on headless boxes; unpack the Ubuntu debs locally (no sudo)
mkdir -p "$HOME/.local/xlibs"
if [ ! -e "$XLIBS/libOpenGL.so.0" ] || [ ! -e "$XLIBS/libSM.so.6" ]; then
  log "unpacking libSM/libICE/libglvnd into ~/.local/xlibs"
  tmp="$(mktemp -d)"; pushd "$tmp" >/dev/null
  apt-get download libsm6 libice6 libglvnd0 libopengl0 libglx0 2>/dev/null || true
  for d in *.deb; do [ -f "$d" ] && dpkg-deb -x "$d" "$HOME/.local/xlibs"; done
  popd >/dev/null; rm -rf "$tmp"
fi
cat > "$HOME/.local/bin/blender-5.0" <<EOF
#!/bin/bash
export LD_LIBRARY_PATH="$XLIBS:\${LD_LIBRARY_PATH:-}"
exec "$TOOLS/blender-5.0.1-linux-x64/blender" "\$@"
EOF
chmod +x "$HOME/.local/bin/blender-5.0"
"$HOME/.local/bin/blender-5.0" --version | head -1

# ---------------------------------------------------------------------------------------------- OpenSCAD (nightly, --export-format binstl)
if [ ! -x "$TOOLS/openscad/AppRun" ]; then
  log "downloading OpenSCAD nightly AppImage"
  latest="$(curl -s https://files.openscad.org/snapshots/ | grep -oE 'OpenSCAD-[0-9.]+-x86_64\.AppImage' | sort -u | tail -1)"
  curl -L -o "$TOOLS/openscad.AppImage" "https://files.openscad.org/snapshots/$latest"
  chmod +x "$TOOLS/openscad.AppImage"
  (cd "$TOOLS" && ./openscad.AppImage --appimage-extract >/dev/null && rm -rf openscad && mv squashfs-root openscad)
fi
LD_LIBRARY_PATH="$XLIBS:${LD_LIBRARY_PATH:-}" QT_QPA_PLATFORM=offscreen "$TOOLS/openscad/AppRun" --version 2>&1 | head -1

# ---------------------------------------------------------------------------------------------- glslang
if [ ! -x "$TOOLS/glslang/bin/glslang" ]; then
  log "downloading glslang 16.5.0"
  curl -L -o "$TOOLS/glslang.tgz" https://github.com/KhronosGroup/glslang/releases/download/16.5.0/glslang-16.5.0-linux-x86_64-release.tar.gz
  mkdir -p "$TOOLS/glslang" && tar -xzf "$TOOLS/glslang.tgz" -C "$TOOLS/glslang" && rm "$TOOLS/glslang.tgz"
fi
"$TOOLS/glslang/bin/glslang" --version | head -1

log "done. Next: hf auth login (token with ilabai/* access), then"
echo "  $PY -m llm.config && $PY -m llm.download --core --views --logs && $PY -m llm.build_prompts && SEED=0 $PY -m llm.build_refs"
