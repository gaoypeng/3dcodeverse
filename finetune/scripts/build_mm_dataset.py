"""Build the multimodal (render + instruction → code) datasets in standard LLaMA-Factory ShareGPT format.

Three variants are emitted from the SAME samples, registered as separate datasets so they can be mixed or ablated
against each other without rebuilding anything:

  3dcv_mm_img        image(s) only            "<image>… Write the Blender Python code that builds this object."
  3dcv_mm_img_text   image(s) + instruction   the realistic setting: a reference render AND what the user asked for
  3dcv_mm_text       instruction only         the control, identical samples and split, no images

Format notes (checked against LLaMA-Factory's loader):
  * `images` is a list of absolute paths; the number of `<image>` placeholders in the user turn must equal
    len(images) — `mm_plugin._validate_input` raises otherwise.
  * `conversations` is [human, gpt]; `system` is a plain string column.
  * multimodal rows cannot be packed (`packing: false`), because image tokens must stay aligned with their sample.

Sources: bioinspired3d (tars, 4 views), 3dcodebench factories (tars, 4 views, benchmark factories excluded),
blender_distill / threejs_distill / animation2code (file trees, 4 views).

usage: python scripts/build_mm_dataset.py [--views 4] [--out data/mm]
"""
import argparse
import collections
import glob
import json
import os
import random
import tarfile

IMG_ROOT = "/wekafs/ict/hx_624/data/images"
BENCH = {os.path.basename(p).replace("_seed0", "") for p in glob.glob("/wekafs/ict/hx_624/data/3dcodebench/data/*_seed0")}

SYS = {
    "blender": "You are an expert in procedural 3D modeling with Blender Python (bpy). Write a complete, standalone Blender 5 Python script that builds the requested object from scratch (clear the default scene first). Output ONLY the code in one ```python block.",
    "threejs": "You are an expert three.js developer. Write a complete, standalone HTML page that renders the requested scene with three.js. Output ONLY the code in one ```html block.",
    "web": "You are an expert creative web developer. Write a complete, standalone HTML page (inline CSS/JS, no local assets) that reproduces the requested animation. Output ONLY the code in one ```html block.",
}
ASK_IMG = {
    "blender": "These are reference renders of one object. Write the Blender Python code that builds it.",
    "threejs": "These are reference renders of one scene. Write the single-file three.js page that renders it.",
    "web": "These are frames of one animation. Write the single-file animated web page that reproduces it.",
}
ASK_TEXT = {
    "blender": "Write the Blender Python code that builds this object: ",
    "threejs": "Write a single-file three.js page that renders: ",
    "web": "Write a single-file animated web page that reproduces: ",
}
ASK_BOTH = {
    "blender": "These are reference renders of one object. Write the Blender Python code that builds it.\n\nThe object: ",
    "threejs": "These are reference renders of one scene. Write the single-file three.js page that renders it.\n\nThe scene: ",
    "web": "These are frames of one animation. Write the single-file animated web page that reproduces it.\n\nThe animation: ",
}
FENCE = {"blender": "python", "threejs": "html", "web": "html"}
IMPERATIVE = ("write", "create", "implement", "make", "build", "generate", "design", "produce")


def caption_of(caps):
    """the best available human description, normalised to a noun phrase (the ASK_* strings supply the verb)"""
    for k in ("user_instruction", "instruction", "detailed", "brief", "factory"):
        v = (caps.get(k) or "").strip()
        if not v or len(v) < 15:
            continue
        low = v.lower()
        if low.startswith(IMPERATIVE):                 # "Write a Blender script that builds a X" -> "a X"
            for sep in (" that builds ", " that renders ", " that reproduces ", " of ", " for "):
                if sep in low:
                    v = v[low.index(sep) + len(sep):].strip()
                    break
            else:
                continue
        return v[:1200]
    return None


def add(rows, sample_id, dialect, imgs, caption, code):
    base = {"id": sample_id, "dialect": dialect, "system": SYS[dialect]}
    gpt = {"from": "gpt", "value": f"```{FENCE[dialect]}\n{code.strip()}\n```"}
    ph = "<image>" * len(imgs)
    out = {}
    out["img"] = dict(base, images=list(imgs),
                      conversations=[{"from": "human", "value": ph + "\n" + ASK_IMG[dialect]}, gpt])
    if caption:
        out["img_text"] = dict(base, images=list(imgs),
                               conversations=[{"from": "human", "value": ph + "\n" + ASK_BOTH[dialect] + caption}, gpt])
        out["text"] = dict(base, conversations=[{"from": "human", "value": ASK_TEXT[dialect] + caption}, gpt])
    for k, v in out.items():
        rows[k].append(v)


def from_tars(pattern, dialect, rows, views, drop_bench=False):
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
            key = m.name.split("/")[0]
            if m.name.endswith("code.py"):
                members[key]["code"] = m
            elif m.name.endswith("captions.json"):
                members[key]["caps"] = m
            elif "/renders/view_" in m.name and m.name.endswith(".png"):
                members[key].setdefault("imgs", []).append(m)
        for key, d in members.items():
            if "code" not in d or "imgs" not in d:
                continue
            if drop_bench and key.replace("Factory", "").replace("_geo", "").replace("_tex", "") in BENCH:
                continue
            try:
                code = tf.extractfile(d["code"]).read().decode("utf-8", "replace")
            except Exception:
                continue
            if len(code.strip()) < 40:
                continue
            caps = {}
            if "caps" in d:
                try:
                    caps = json.load(tf.extractfile(d["caps"]))
                except Exception:
                    pass
            paths = []
            for m in sorted(d["imgs"], key=lambda m: m.name)[:views]:
                dst = os.path.join(IMG_ROOT, sub, key, os.path.basename(m.name))
                if not os.path.exists(dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as f:
                        f.write(tf.extractfile(m).read())
                paths.append(dst)
            if paths:
                add(rows, f"{sub}/{key}", dialect, paths, caption_of(caps), code)
                n += 1
        tf.close()
    return n


def from_tree(root, dialect, rows, views, code_name):
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
        add(rows, os.path.relpath(d, "/wekafs/ict/hx_624/data/3dcodeverse_files"), dialect, imgs, caption_of(caps), code)
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/wekafs/ict/hx_624/llm-ft/data/mm")
    ap.add_argument("--views", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(IMG_ROOT, exist_ok=True)
    rows = collections.defaultdict(list)
    got = {
        "bioinspired3d": from_tars("/wekafs/ict/hx_624/data/3dcodeverse_tars/bioinspired3d/*/*.tar", "blender", rows, a.views),
        "3dcodebench_factories": from_tars("/wekafs/ict/hx_624/data/3dcodeverse_tars/3dcodebench/factories_*/*.tar", "blender", rows, a.views, drop_bench=True),
        "blender_distill": from_tree("blender_distill", "blender", rows, a.views, "code.py"),
        "threejs_distill": from_tree("threejs_distill", "threejs", rows, a.views, "code.html"),
        "animation2code": from_tree("animation2code", "web", rows, a.views, "index.html"),
    }
    # one split shared by all three variants, so a comparison is never confounded by different val sets
    ids = sorted({r["id"] for r in rows["img"]})
    random.seed(7)
    random.shuffle(ids)
    val_ids = set(ids[:200])
    os.makedirs(a.out, exist_ok=True)
    info = {}
    for variant in ("img", "img_text", "text"):
        tr = [r for r in rows[variant] if r["id"] not in val_ids]
        va = [r for r in rows[variant] if r["id"] in val_ids]
        for split, part in (("train", tr), ("val", va)):
            name = f"mm_{variant}_{split}"
            json.dump(part, open(os.path.join(a.out, f"{name}.json"), "w"), ensure_ascii=False)
            cols = {"messages": "conversations", "system": "system"}
            if variant != "text":
                cols["images"] = "images"
            info[f"3dcv_{name}"] = {"file_name": f"{name}.json", "formatting": "sharegpt", "columns": cols}
        print(f"[mm] {variant:9s} train={len(tr):6,} val={len(va):4,}")
    json.dump(info, open(os.path.join(a.out, "dataset_info.json"), "w"), indent=2)
    # a sanity check on the invariant LLaMA-Factory enforces
    bad = [r for v in ("img", "img_text") for r in rows[v]
           if r["conversations"][0]["value"].count("<image>") != len(r.get("images", []))]
    print(f"[mm] sources: {got}")
    print(f"[mm] placeholder/image-count mismatches: {len(bad)} (must be 0)")
    print(f"[mm] dialects: {dict(collections.Counter(r['dialect'] for r in rows['img']))}")
    print(f"[mm] views per sample: {a.views} | shared val split: {len(val_ids)} ids -> {a.out}")


if __name__ == "__main__":
    main()
