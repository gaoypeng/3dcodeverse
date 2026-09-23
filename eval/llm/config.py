"""Paths and tool discovery for 3dcodeverse_eval.

Nothing in this package hardcodes a machine.  Every location is resolved here, from environment
variables first and sensible defaults second:

  C3D_EVAL_DATA   root for downloaded assets, reference meshes and run outputs
                   (default ~/3dcodeverse_data/3dcodeverse_eval)
  C3D_EVAL_OUT    run outputs (default $C3D_EVAL_DATA/out)
  C3D_TOOLS       directory holding blender-5.0.1-linux-x64/, openscad/, glslang/
                   (default ~/3dcodeverse_data/tools)
  C3D_BLENDER, C3D_OPENSCAD, C3D_GLSLANG, C3D_CQ_PYTHON   explicit binaries
  C3D_XLIBS       extra shared-library dir prepended to LD_LIBRARY_PATH for Blender/OpenSCAD
                   (default ~/.local/xlibs/usr/lib/x86_64-linux-gnu, where libSM/libICE/libOpenGL
                   were unpacked on a box without sudo)
  PLAYWRIGHT_BROWSERS_PATH  honoured by Playwright itself

`python -m llm.config` prints a doctor report.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = PKG_DIR / "data" / "prompts"
# the harness batteries are ONE set of files, owned by the harness evaluation next door (eval/bench/prompts);
# these ten are the ones a bare LLM can be asked one-shot (static + graphics, no plan, no tools)
BATTERIES_DIR = PKG_DIR.parent / "bench" / "prompts"
HARNESS_BATTERIES = ("articulated_v2", "compare_v4", "complexity_v3", "fancy_v1_blender", "fancy_v1_cadquery",
                     "fancy_v1_threejs", "graphics_v2", "static_objects_v1", "static_objects_v2", "teaser_v1_static")

DATA_DIR = Path(os.environ.get("C3D_EVAL_DATA", "~/3dcodeverse_data/3dcodeverse_eval")).expanduser()
HUB_DIR = DATA_DIR / "hub"                     # snapshot_download targets (ilabai/*, YipengGao/3DCode)
REFS_DIR = DATA_DIR / "refs"                   # reference meshes / renders per suite
OUT_DIR = Path(os.environ.get("C3D_EVAL_OUT", DATA_DIR / "out")).expanduser()
TOOLS_DIR = Path(os.environ.get("C3D_TOOLS", "~/3dcodeverse_data/tools")).expanduser()
XLIBS = Path(os.environ.get("C3D_XLIBS", "~/.local/xlibs/usr/lib/x86_64-linux-gnu")).expanduser()

BENCH_TASKS_DIR = HUB_DIR / "3DCode" / "3DCodeBench"                 # 212 <task>/{<task>.py, prompt_*.txt}
C3D_HUB = HUB_DIR / "3dcodeverse"                                   # ilabai/3dcodeverse partial snapshot
LF_HUB = HUB_DIR / "3dcodeverse-llamafactory"                        # ilabai/3dcodeverse-llamafactory (test/*.parquet)


def _first_existing(*cands: str | Path | None) -> Path | None:
    for c in cands:
        if not c:
            continue
        p = Path(c).expanduser()
        if p.exists():
            return p
    return None


def find_blender() -> Path | None:
    return _first_existing(
        os.environ.get("C3D_BLENDER"),
        "~/.local/bin/blender-5.0",
        TOOLS_DIR / "blender-5.0.1-linux-x64" / "blender",
        shutil.which("blender"),
    )


def find_openscad() -> Path | None:
    return _first_existing(
        os.environ.get("C3D_OPENSCAD"),
        TOOLS_DIR / "openscad" / "AppRun",
        TOOLS_DIR / "openscad.AppImage",
        shutil.which("openscad"),
    )


def find_glslang() -> Path | None:
    return _first_existing(
        os.environ.get("C3D_GLSLANG"),
        TOOLS_DIR / "glslang" / "bin" / "glslang",
        TOOLS_DIR / "glslang" / "bin" / "glslangValidator",
        shutil.which("glslang"),
        shutil.which("glslangValidator"),
    )


def cadquery_python() -> str:
    """Interpreter that has `cadquery` importable (default: the current one)."""
    return os.environ.get("C3D_CQ_PYTHON") or sys.executable


#: a variable whose NAME carries one of these words is a credential: model-written code runs in the
#: tool subprocesses, so it never sees one (the harness's own rule is proc/cli_common clean_env; this
#: package does not import the harness)
_SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH", "COOKIE", "SESSION")


def tool_env(**extra: str) -> dict[str, str]:
    """Environment for every subprocess that runs or compiles model-written code (Blender, CadQuery,
    OpenSCAD, glslang): no credentials, private HOME, extra libs."""
    env = {k: v for k, v in os.environ.items() if not any(w in k.upper() for w in _SECRET_WORDS)}
    if XLIBS.exists():
        env["LD_LIBRARY_PATH"] = f"{XLIBS}:{env.get('LD_LIBRARY_PATH', '')}"
    env.update(extra)
    return env


def doctor(verbose: bool = True) -> dict[str, object]:
    rep: dict[str, object] = {
        "data_dir": str(DATA_DIR),
        "out_dir": str(OUT_DIR),
        "bench_tasks": BENCH_TASKS_DIR.exists() and len([p for p in BENCH_TASKS_DIR.iterdir() if p.is_dir()]),
        "heldout_parquets": sorted(p.name for p in (LF_HUB / "test").glob("*.parquet")) if (LF_HUB / "test").exists() else [],
        "blender": str(find_blender()),
        "openscad": str(find_openscad()),
        "glslang": str(find_glslang()),
        "cadquery_python": cadquery_python(),
    }
    for mod in ("cadquery", "trimesh", "scipy", "playwright", "vllm", "openai", "pyarrow", "PIL"):
        try:
            __import__(mod)
            rep[f"py:{mod}"] = "ok"
        except Exception as e:  # noqa: BLE001
            rep[f"py:{mod}"] = f"MISSING ({type(e).__name__})"
    if verbose:
        for k, v in rep.items():
            print(f"{k:20s} {v}")
    return rep


if __name__ == "__main__":
    doctor()
