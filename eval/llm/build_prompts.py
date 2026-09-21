"""Build the prompt sets under data/prompts/ from the Hub assets (run once after `download.py`).

One JSONL row per task, one schema for every suite::

    {"id": str,                 unique within the suite (filesystem-safe)
     "suite": str,              e.g. "3dcodebench_text"
     "dialect": str,            blender | cadquery | openscad | glsl | threejs
     "messages": [{"role": "system"|"user", "content": str}],    the chat prompt sent to the model
     "images": [str],           image paths relative to $CV3D_EVAL_DATA (VLM suites only), one <image> tag
                                per image appears in the user message
     "reference": {"code": str|null, "mesh": str|null, "renders": [str]},   ground truth (code inline; mesh/renders
                                relative to $CV3D_EVAL_DATA, filled by build_refs.py / download.py)
     "meta": {...}}             source ids, caption type, tier, must_have, ...

Suites built here (sizes are what the assets give):
  3dcodebench_text        212  Blender  prompt_instruction.txt   (the report's headline suite)
  3dcodebench_desc        212  Blender  prompt_description.txt   (short caption variant)
  3dcodebench_img1/img4   212  Blender  1 / 4 GT reference views (image→code, VLM)
  3dcodebench_img4text    212  Blender  4 views + description
  heldout_blender         103, heldout_cadquery 200, heldout_openscad 50, heldout_glsl 200, heldout_threejs 40
                               (ilabai/3dcodeverse-llamafactory/test/*.parquet, never trained on)
  harness_<battery>       rubric-judged batteries copied from harness/bench/prompts (no GT; scored by
                               execution + optional VLM judge), one suite per YAML

System prompts are the exact strings used in finetune/docs/REPORT.md so numbers stay comparable; the
held-out sets carry their own system prompt inside the parquet.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from . import config

SYSTEM_BENCH = ("You are an expert in procedural 3D modeling with Blender Python (bpy). Given a description of an object, "
                "write a complete, standalone Blender 5 Python script that builds it from scratch (clear the default scene "
                "first, create geometry with bpy/bmesh, assign simple materials when relevant, leave the finished mesh objects "
                "in the scene). Output ONLY the code in one ```python block.")
SYSTEM_BENCH_IMG = ("You are an expert in procedural 3D modeling with Blender Python (bpy). Given reference renders of an "
                    "object, write a complete, standalone Blender 5 Python script that reproduces it (clear the default scene "
                    "first). Output ONLY the code in one ```python block.")
SYSTEM_BENCH_IMGTEXT = ("You are an expert in procedural 3D modeling with Blender Python (bpy). Given reference renders and a "
                        "description of an object, write a complete, standalone Blender 5 Python script that builds the requested "
                        "object from scratch (clear the default scene first). Output ONLY the code in one ```python block.")

# one-shot system prompts for the harness batteries (derived from harness/codeverse/prompts/<lang>/contract.md)
SYSTEM_BATTERY = {
    "blender": ("You are an expert Blender (bpy) modeller writing raw code. Write ONE complete, standalone Blender 5 Python "
                "script that builds the requested object from scratch in an emptied scene. Conventions: Z-up, the object's "
                "front faces -Y, units are metres, the object rests on z=0. Give every mesh object a descriptive unique name "
                "and a simple material. Do not render, export, import files or access the network. "
                "Output ONLY the code in one ```python block."),
    "cadquery": ("You are an expert CAD programmer. Write ONE complete, standalone CadQuery (Python) script that builds the "
                 "requested part and leaves the final solid in a variable named `result`. Units are millimetres unless the "
                 "brief says otherwise. Output ONLY the code in one ```python block."),
    "threejs": ("You are an expert three.js developer. Write ONE complete, standalone HTML page that renders the requested "
                "object with three.js (import three from https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js "
                "via an importmap; procedural geometry only, no external assets), with lighting, a camera framing the object "
                "and OrbitControls. Output ONLY the code in one ```html block."),
    "glsl_shader": ("You are an expert shader programmer. Write ONE complete Shadertoy-style GLSL ES 3.00 fragment shader "
                    "defining `void mainImage(out vec4 fragColor, in vec2 fragCoord)` that renders the requested scene "
                    "(animate with iTime). Output ONLY the code in one ```glsl block."),
    "urdf_blender": None,  # articulated: two-file contract, not a one-shot suite here
}
BATTERY_DIALECT = {"blender": "blender", "cadquery": "cadquery", "threejs": "threejs", "glsl_shader": "glsl"}


def rel(p: Path) -> str:
    return str(Path(p).resolve().relative_to(config.DATA_DIR.resolve()))


def write_jsonl(rows: list[dict], name: str) -> Path:
    out = config.PROMPTS_DIR / f"{name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[prompts] {name}: {len(rows)} rows -> {out}")
    return out


def bench_tasks() -> list[Path]:
    d = config.BENCH_TASKS_DIR
    if not d.exists():
        raise SystemExit(f"3DCodeBench tasks not found at {d}; run download.py first")
    return sorted(p for p in d.iterdir() if p.is_dir() and (p / "prompt_instruction.txt").exists())


def bench_images(task: str) -> list[Path]:
    """The official 4 GT views (3DCodeBench_ModelLogs/inputs/<task>/images/Image_0{05,15,25,35}.png)."""
    d = config.HUB_DIR / "3DCode" / "3DCodeBench_ModelLogs" / "inputs" / task / "images"
    return sorted(d.glob("*.png")) if d.exists() else []


def build_3dcodebench() -> None:
    text, desc, img1, img4, img4text = [], [], [], [], []
    n_img = 0
    for t in bench_tasks():
        task = t.name
        instr = (t / "prompt_instruction.txt").read_text().strip()
        descr = (t / "prompt_description.txt").read_text().strip()
        code = (t / f"{task}.py").read_text() if (t / f"{task}.py").exists() else None
        ref_mesh = config.REFS_DIR / "3dcodebench" / task / "ref.glb"
        imgs = bench_images(task)
        ref = {"code": code, "mesh": rel(ref_mesh) if ref_mesh.exists() else None, "renders": [rel(p) for p in imgs]}
        meta = {"factory": task.replace("_seed0", ""), "source": "YipengGao/3DCode/3DCodeBench"}
        ref_nocode = {**ref, "code": None}        # the code is stored once, in 3dcodebench_text (see meta.code_from)
        text.append({"id": task, "suite": "3dcodebench_text", "dialect": "blender",
                     "messages": [{"role": "system", "content": SYSTEM_BENCH}, {"role": "user", "content": instr}],
                     "reference": ref, "meta": {**meta, "caption_type": "instruction"}})
        desc.append({"id": task, "suite": "3dcodebench_desc", "dialect": "blender",
                     "messages": [{"role": "system", "content": SYSTEM_BENCH}, {"role": "user", "content": descr}],
                     "reference": ref_nocode, "meta": {**meta, "caption_type": "description", "code_from": "3dcodebench_text"}})
        if imgs:
            n_img += 1
            img1.append({"id": task, "suite": "3dcodebench_img1", "dialect": "blender",
                         "messages": [{"role": "system", "content": SYSTEM_BENCH_IMG},
                                      {"role": "user", "content": "<image>\nReproduce this 3D object with Blender Python code."}],
                         "images": [rel(imgs[0])], "reference": ref_nocode, "meta": {**meta, "n_images": 1, "code_from": "3dcodebench_text"}})
            img4.append({"id": task, "suite": "3dcodebench_img4", "dialect": "blender",
                         "messages": [{"role": "system", "content": SYSTEM_BENCH_IMG},
                                      {"role": "user", "content": "<image>" * len(imgs[:4]) + "\nThese are reference renders of one object. "
                                                                  "Reproduce this 3D object with Blender Python code."}],
                         "images": [rel(p) for p in imgs[:4]], "reference": ref_nocode, "meta": {**meta, "n_images": len(imgs[:4]), "code_from": "3dcodebench_text"}})
            img4text.append({"id": task, "suite": "3dcodebench_img4text", "dialect": "blender",
                             "messages": [{"role": "system", "content": SYSTEM_BENCH_IMGTEXT},
                                          {"role": "user", "content": "<image>" * len(imgs[:4]) + "\nThese are reference renders of one object. "
                                                                      "Write the Blender Python code that builds it.\n\nThe object: " + descr}],
                             "images": [rel(p) for p in imgs[:4]], "reference": ref_nocode,
                             "meta": {**meta, "n_images": len(imgs[:4]), "caption_type": "description", "code_from": "3dcodebench_text"}})
    write_jsonl(text, "3dcodebench_text")
    write_jsonl(desc, "3dcodebench_desc")
    # official protocol: description prompt under the official raw-python system prompt (configs default
    # prompt_type: description); image task = the 4 views alone, no text
    off_dir = config.PKG_DIR / "data" / "official_prompts"
    sys_text = (off_dir / "text_to_3d_system_prompt.txt").read_text().strip()
    sys_img = (off_dir / "image_to_3d_system_prompt.txt").read_text().strip()
    off_text = [{**r, "suite": "3dcodebench_official_text",
                 "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": r["messages"][1]["content"]}],
                 "meta": {**r["meta"], "protocol": "official", "source": "3dcodebench/prompts/text_to_3d_system_prompt.txt"}}
                for r in desc]
    write_jsonl(off_text, "3dcodebench_official_text")
    if n_img:
        off_img = [{**r, "suite": "3dcodebench_official_img4",
                    "messages": [{"role": "system", "content": sys_img},
                                 {"role": "user", "content": "<image>" * len(r["images"]) + "\nReconstruct the object shown in the following "
                                                             "reference image(s) as a Blender 5.0 Python script. Treat all images as views of the SAME object."}],
                    "meta": {**r["meta"], "protocol": "official", "source": "3dcodebench/prompts/image_to_3d_system_prompt.txt"}}
                   for r in img4]
        write_jsonl(off_img, "3dcodebench_official_img4")
    if n_img:
        write_jsonl(img1, "3dcodebench_img1")
        write_jsonl(img4, "3dcodebench_img4")
        write_jsonl(img4text, "3dcodebench_img4text")
    else:
        print("[prompts] no GT views found under 3DCodeBench_ModelLogs/inputs — image suites skipped")


def build_heldout() -> None:
    import pyarrow.parquet as pq

    d = config.LF_HUB / "test"
    if not d.exists():
        raise SystemExit(f"held-out parquets not found at {d}; run download.py first")
    ext = {"blender": "glb", "cadquery": "stl", "openscad": "stl", "glsl": None, "threejs": None}
    for dialect in ("blender", "cadquery", "openscad", "glsl", "threejs"):
        p = d / f"{dialect}.parquet"
        if not p.exists():
            print(f"[prompts] missing {p}")
            continue
        rows = []
        for r in pq.read_table(p).to_pylist():
            tid = r["id"].replace("/", "__")
            ref_mesh = config.REFS_DIR / f"heldout_{dialect}" / tid / f"ref.{ext[dialect]}" if ext[dialect] else None
            code = r["reference_code"]
            rows.append({"id": tid, "suite": f"heldout_{dialect}", "dialect": dialect,
                         "messages": [{"role": "system", "content": r["system"]}, {"role": "user", "content": r["prompt"]}],
                         "reference": {"code": code, "mesh": rel(ref_mesh) if ref_mesh and ref_mesh.exists() else None, "renders": []},
                         "meta": {"source_id": r["id"], "source": "ilabai/3dcodeverse-llamafactory/test"}})
        write_jsonl(rows, f"heldout_{dialect}")


def build_batteries() -> None:
    for y in (config.BATTERIES_DIR / f"{name}.yaml" for name in config.HARNESS_BATTERIES):
        bat = yaml.safe_load(y.read_text())
        lang = bat.get("language", "blender")
        rows = []
        for p in bat["prompts"]:
            pl = p.get("language") or lang
            sysmsg = SYSTEM_BATTERY.get(pl)
            if sysmsg is None:
                continue
            brief = p["prompt"].strip()
            extra = []
            if p.get("dimensions_m"):
                extra.append("Dimensions (m): " + ", ".join(f"{k}={v}" for k, v in p["dimensions_m"].items()))
            if p.get("must_have"):
                extra.append("MUST HAVE:\n" + "\n".join(f"- {m}" for m in p["must_have"]))
            user = brief + ("\n\n" + "\n\n".join(extra) if extra else "")
            rows.append({"id": p["id"], "suite": f"harness_{bat['name']}", "dialect": BATTERY_DIALECT[pl],
                         "messages": [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}],
                         "reference": {"code": None, "mesh": None, "renders": []},
                         "meta": {"tier": p.get("tier"), "category": p.get("category"), "must_have": p.get("must_have", []),
                                  "dimensions_m": p.get("dimensions_m"), "tags": p.get("tags", []), "track": bat.get("track"),
                                  "source": f"harness/bench/prompts/{y.name}"}})
        if rows:
            write_jsonl(rows, f"harness_{bat['name']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", choices=["bench", "heldout", "batteries"], default=None)
    a = ap.parse_args()
    which = set(a.only or ["bench", "heldout", "batteries"])
    if "bench" in which:
        build_3dcodebench()
    if "heldout" in which:
        build_heldout()
    if "batteries" in which:
        build_batteries()


if __name__ == "__main__":
    main()
