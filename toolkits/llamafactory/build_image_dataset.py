"""Build an image-conditioned (render → code) dataset in LLaMA-Factory multimodal format.

The `qwen3_5` template already registers a multimodal plugin (`qwen3_vl`, image token `<|image_pad|>`), so an
`images` column plus an `<image>` placeholder in the user turn is all that is needed — the only change to a normal
run is `freeze_vision_tower: false` if you want the tower to adapt.

Why this matters for this project: the report shows execution rate responds to more data, but *geometric fidelity*
(F-score / Chamfer) barely moved across every text-only SFT variant. A reference render carries exactly the signal
the caption lacks.

Sources (all already on disk):
  bioinspired3d tars   4 views + code per sample, Blender-Python — the dialect 3DCodeBench measures
  blender_distill      4 views per sample
  threejs_distill      4 views per sample
  animation2code       4 views per sample

usage: python scripts/build_image_dataset.py [--out data/img_code] [--views 1] [--max_per_source 20000]
"""
import argparse
import collections
import glob
import json
import os
import tarfile

IMG_ROOT = "/wekafs/ict/hx_624/data/images"
SYS = {
    "blender": "You are an expert in procedural 3D modeling with Blender Python (bpy). Given reference renders of an object, write a complete, standalone Blender 5 Python script that reproduces it (clear the default scene first). Output ONLY the code in one ```python block.",
    "threejs": "You are an expert three.js developer. Given reference renders, write a complete, standalone HTML page that reproduces the scene with three.js. Output ONLY the code in one ```html block.",
    "web": "You are an expert creative web developer. Given reference frames of an animation, write a complete, standalone HTML page (inline CSS/JS) that reproduces it. Output ONLY the code in one ```html block.",
}
ASK = {
    "blender": "Reproduce this 3D object with Blender Python code.",
    "threejs": "Reproduce this scene as a single-file three.js page.",
    "web": "Reproduce this animation as a single-file animated web page.",
}
FENCE = {"blender": "python", "threejs": "html", "web": "html"}


def from_tars(pattern, dialect, out_rows, views, cap, caption_too=True):
    n = 0
    for t in sorted(glob.glob(pattern)):
        sub = os.path.basename(os.path.dirname(t))
        try:
            tf = tarfile.open(t)
        except Exception:
            continue
        members = collections.defaultdict(dict)
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/")
            if len(parts) < 2:
                continue
            key = parts[0]
            if m.name.endswith("code.py"):
                members[key]["code"] = m
            elif m.name.endswith("captions.json"):
                members[key]["caps"] = m
            elif "/renders/view_" in m.name and m.name.endswith(".png"):
                members[key].setdefault("imgs", []).append(m)
        for key, d in members.items():
            if "code" not in d or "imgs" not in d:
                continue
            try:
                code = tf.extractfile(d["code"]).read().decode("utf-8", "replace")
            except Exception:
                continue
            if len(code.strip()) < 40:
                continue
            caps = {}
            if caption_too and "caps" in d:
                try:
                    caps = json.load(tf.extractfile(d["caps"]))
                except Exception:
                    caps = {}
            imgs = sorted(d["imgs"], key=lambda m: m.name)[:views]
            paths = []
            for i, m in enumerate(imgs):
                dst = os.path.join(IMG_ROOT, sub, key, os.path.basename(m.name))
                if not os.path.exists(dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as f:
                        f.write(tf.extractfile(m).read())
                paths.append(dst)
            if not paths:
                continue
            hint = (caps.get("instruction") or caps.get("detailed") or "").strip()
            ask = ASK[dialect] + (f"\nHint: {hint}" if hint and len(hint) < 400 else "")
            out_rows.append({"id": f"{sub}/{key}", "dialect": dialect, "images": paths,
                             "system": SYS[dialect],
                             "conversations": [{"from": "human", "value": "<image>" * len(paths) + "\n" + ask},
                                               {"from": "gpt", "value": f"```{FENCE[dialect]}\n{code.strip()}\n```"}]})
            n += 1
            if n >= cap:
                tf.close()
                return n
        tf.close()
    return n


def from_tree(root, dialect, out_rows, views, cap, code_name):
    n = 0
    for cp in sorted(glob.glob(f"/wekafs/ict/hx_624/data/3dcodeverse_files/{root}/*/*/{code_name}")):
        d = os.path.dirname(cp)
        imgs = sorted(glob.glob(os.path.join(d, "renders", "*.png")))[:views]
        if not imgs:
            continue
        code = open(cp, encoding="utf-8", errors="replace").read()
        if len(code.strip()) < 40:
            continue
        caps = {}
        cj = os.path.join(d, "captions.json")
        if os.path.exists(cj):
            try:
                caps = json.load(open(cj))
            except Exception:
                pass
        hint = (caps.get("user_instruction") or caps.get("instruction") or caps.get("brief") or "").strip()
        ask = ASK[dialect] + (f"\nHint: {hint}" if hint and len(hint) < 400 else "")
        out_rows.append({"id": os.path.relpath(d, "/wekafs/ict/hx_624/data/3dcodeverse_files"),
                         "dialect": dialect, "images": imgs, "system": SYS[dialect],
                         "conversations": [{"from": "human", "value": "<image>" * len(imgs) + "\n" + ask},
                                           {"from": "gpt", "value": f"```{FENCE[dialect]}\n{code.strip()}\n```"}]})
        n += 1
        if n >= cap:
            return n
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/wekafs/ict/hx_624/llm-ft/data/img_code")
    ap.add_argument("--views", type=int, default=1)
    ap.add_argument("--max_per_source", type=int, default=20000)
    a = ap.parse_args()
    os.makedirs(IMG_ROOT, exist_ok=True)
    rows = []
    got = {}
    got["bioinspired3d"] = from_tars("/wekafs/ict/hx_624/data/3dcodeverse_tars/bioinspired3d/*/*.tar",
                                     "blender", rows, a.views, a.max_per_source)
    got["blender_distill"] = from_tree("blender_distill", "blender", rows, a.views, a.max_per_source, "code.py")
    got["threejs_distill"] = from_tree("threejs_distill", "threejs", rows, a.views, a.max_per_source, "code.html")
    got["animation2code"] = from_tree("animation2code", "web", rows, a.views, a.max_per_source, "index.html")
    os.makedirs(a.out, exist_ok=True)
    import random
    random.seed(3)
    random.shuffle(rows)
    val, train = rows[:200], rows[200:]
    for name, part in (("train", train), ("val", val)):
        with open(os.path.join(a.out, f"{name}.json"), "w") as f:
            json.dump(part, f, ensure_ascii=False)
    json.dump({"img_code_train": {"file_name": "train.json", "formatting": "sharegpt",
                                  "columns": {"messages": "conversations", "system": "system", "images": "images"}},
               "img_code_val": {"file_name": "val.json", "formatting": "sharegpt",
                                "columns": {"messages": "conversations", "system": "system", "images": "images"}}},
              open(os.path.join(a.out, "dataset_info.json"), "w"), indent=2)
    print(f"[img] {len(rows)} samples ({dict(got)}), {a.views} view(s) each -> {a.out}")
    print("[img] dialects:", dict(collections.Counter(r["dialect"] for r in rows)))


if __name__ == "__main__":
    main()
