# Environment setup (what was actually installed; reproduce on a new box)

Machine used: DGX (8×H100 80 GB, driver 570 / CUDA 12.8, 224 cores, 2 TB RAM), Ubuntu; only GPUs 0–3 were used.
`$HOME` was full, so **everything lives under one workspace dir** (`WORKSPACE=/wekafs/ict/hx_624` in `env.sh`) — Anaconda, HF cache, models, tools, caches. Set `WORKSPACE` to your own path and keep the rest of `env.sh` as is.

## 0. Anaconda
```bash
wget https://repo.anaconda.com/archive/Anaconda3-2026.07-Linux-x86_64.sh
bash Anaconda3-2026.07-Linux-x86_64.sh -b -p $WORKSPACE/anaconda3
source $WORKSPACE/anaconda3/etc/profile.d/conda.sh
```

## 1. Three conda envs (all Python 3.12; pip freezes in `env/`)

Why three: LLaMA-Factory (training) and vLLM (inference) pin incompatible torch versions; the data/eval stack has its own deps (CadQuery, Playwright, trimesh).

### `lf` — training (LLaMA-Factory, unpatched)
```bash
conda create -y -n lf python=3.12 && conda activate lf
pip install torch==2.8.0 torchvision --index-url https://download.pytorch.org/whl/cu128
git clone https://github.com/hiyouga/LLaMA-Factory.git $WORKSPACE/tools/LLaMA-Factory
cd $WORKSPACE/tools/LLaMA-Factory && git checkout c4e09c7      # 0.9.6.dev0, 2026-08-20 ("support GDN Ulysses cp")
pip install -e ".[torch,metrics,deepspeed]"                    # transformers 5.8.0, peft, trl, deepspeed 0.19.5
pip install flash-attn==2.8.3 --no-build-isolation               # H100: FA2 (prebuilt wheel for torch 2.8/cu128 if available)
pip install flash-linear-attention==0.5.2 causal-conv1d==1.7.0   # Qwen3.5 Gated-DeltaNet kernels
pip install triton==3.7.1                                        # REQUIRED on Hopper: fla refuses the GDN backward with triton 3.4–3.7.0
```
Pins that matter (see `env/requirements-lf.txt`): torch 2.8.0+cu128 (LLaMA-Factory rejects torch 2.9.x with models that contain Conv3D — Qwen3.5's vision tower), transformers 5.8.0, flash_attn 2.8.3, flash-linear-attention 0.5.2, causal-conv1d 1.7.0, triton 3.7.1, deepspeed 0.19.5.

### `vllm` — generation for every evaluation
```bash
conda create -y -n vllm python=3.12 && conda activate vllm
pip install vllm==0.27.1            # brings torch 2.13.0+cu129; runs on the 12.8 driver via CUDA minor-version compatibility
pip install flash-linear-attention==0.5.2 triton==3.7.1
```
Use with `export VLLM_CACHE_ROOT=$WORKSPACE/cache/vllm` (torch.compile cache; default is `~/.cache`). Qwen3.5 needs `limit_mm_per_prompt={"image":0,"video":0}` and `chat_template_kwargs={"enable_thinking": False}` — both are set in `eval/generate_vllm.py`.

### `llmft` — data building, HF-side checks, executors, metrics
```bash
conda create -y -n llmft python=3.12 && conda activate llmft
pip install torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128
pip install transformers==5.15.1 trl==1.10.0 peft==0.20.0 deepspeed==0.19.5 flash-attn==2.8.3 flash-linear-attention==0.5.2
pip install trimesh==5.0.0 numpy scipy pyarrow pandas pyyaml markdown pillow huggingface_hub
pip install cadquery==2.8.0                        # CadQuery executor (OCP 7.8 / 6.0 wheels)
conda install -y -c conda-forge glslang            # glslangValidator for the GLSL executor
pip install playwright==1.62.0 && PLAYWRIGHT_BROWSERS_PATH=$WORKSPACE/cache/ms-playwright playwright install chromium   # three.js executor
pip uninstall -y torchaudio                        # a mismatched torchaudio breaks `import transformers`
```
(`trl`/`peft` here were only used for early hand-written trainer checks; all real training went through `lf`.)

## 2. External tools (called by absolute path from `eval/`)
| tool | how | path key |
|---|---|---|
| Blender 5.0.1 (headless; executes generated bpy code, exports GLB) | `wget https://download.blender.org/release/Blender5.0/blender-5.0.1-linux-x64.tar.xz && tar xf … -C $WORKSPACE/tools` — **then `blender-5.0.1-linux-x64/5.0/python/bin/python3.11 -m pip install scipy`**: 15 of the 212 3DCodeBench reference scripts import scipy, and without it any model that writes the same idiom is scored as a failure (this cost the tuned 27B 8.1 points before we caught it) | `BLENDER` in `eval/run_bench.py`, `eval/blender_dialect_eval.py`, `scripts/*dpo*.sh` |
| OpenSCAD (nightly AppImage, extracted so it runs without FUSE) | `wget …/OpenSCAD-2026.08.19-x86_64.AppImage && ./OpenSCAD-*.AppImage --appimage-extract` → `squashfs-root/AppRun` | `OPENSCAD` in `eval/dialect_runners.py` |
| glslangValidator | conda-forge `glslang` (in `llmft`) | `GLSLANG` in `eval/dialect_runners.py` |
| Chromium (Playwright) | see `llmft` above | `PLAYWRIGHT_BROWSERS_PATH` |
| Models | `hf download Qwen/Qwen3.5-9B --local-dir $WORKSPACE/models/Qwen3.5-9B` (also used: Qwen3.5-4B, Qwen3-8B, Qwen2.5-Coder-7B-Instruct) | `model_name_or_path` in `configs/lf/*.yaml` |
| Data | only `metadata.parquet` of each 3DCodeVerse subset (`ilabai/3dcodeverse` on the HF Hub) + the 3DCodeBench tarball; `scripts/fetch_hf_files.py` is more robust than `hf download` for many small files on a network FS | `scripts/prep_data.py`, `scripts/build_multidialect.py` |

Secrets: `env.sh` sources `$WORKSPACE/.secrets/hf.env` (`export HF_TOKEN=...`, chmod 600). Never put tokens in configs or scripts.

## 3. Caches on a network filesystem (important for parallel runs)
* `TRITON_CACHE_DIR` on a network FS races between concurrent trainings (atomic rename is not reliable) → `scripts/lf_train.sh` gives every run a private copy on local disk (`/tmp/<user>_triton/<run>`).
* `VLLM_CACHE_ROOT`, `HF_HOME`, `TORCH_EXTENSIONS_DIR`, `PIP_CACHE_DIR` all point into `$WORKSPACE/cache` (`env.sh`).

## 4. Smoke tests
```bash
source env.sh                                     # llmft
python - <<'PY'
import torch, transformers, trimesh, cadquery; print(torch.__version__, torch.cuda.is_available(), transformers.__version__)
PY
conda activate lf && python -c "import llamafactory, fla, flash_attn, triton; print(llamafactory.__version__, triton.__version__)"
conda activate vllm && python -c "import vllm; print(vllm.__version__)"
$WORKSPACE/tools/blender-5.0.1-linux-x64/blender -b --version | head -1
$WORKSPACE/tools/openscad/squashfs-root/AppRun --version
# 1-GPU LoRA dry run (tiny dataset), then merge, then a 5-prompt generation:
GPUS=0 scripts/lf_train.sh configs/lf/lora_v1.yaml num_train_epochs=0.01
```
