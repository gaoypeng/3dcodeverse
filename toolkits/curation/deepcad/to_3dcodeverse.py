"""
to_3dcodeverse.py
Emit the official 3DCodeVerse directory layout, aligned to the REAL reference
dataset ilabai/3dcodeverse (3dcodebench/factories_geo/<sample>/):

  deepcad/<sub>/<name>/
      code.py
      meta.json          # FLAT official fields (matches reference exactly)
      curation.json      # OUR extras (iou/quality/split/dedup/Text2CAD) -- kept
                         #   out of meta.json so the toolkit reads a clean card
      renders/
          view_00.png view_01.png view_02.png view_03.png   # iso/front/right/top
          object.glb                                          # mesh artifact
          model.step                                          # CAD BREP (extra)

captions.json is intentionally NOT written -- the 3DCodeVerse maintainer captions
the data. Our Text2CAD text (CC-BY-NC-SA) is preserved in curation.json (clearly
licensed) for reference, not as the official caption.

meta.json reference (factories_geo sample):
  {id,name,type,language,entry,multi_file,environment,renders[],source,license,
   curator,status}
"""
import os
import json
import shutil
import argparse
from pathlib import Path
from joblib import Parallel, delayed
from tqdm import tqdm

VIEW_MAP = [("iso", "view_00"), ("front", "view_01"),
            ("right", "view_02"), ("top", "view_03")]


def emit_one(entry, step_dir, render_dir, gt_dir, out_root, curator, want_glb):
    md = entry["metadata"]
    did = md["deepcad_id"]            # "0001/00013172"
    sub, name = did.split("/")
    d = Path(out_root) / "deepcad" / sub / name
    rdir = d / "renders"
    rdir.mkdir(parents=True, exist_ok=True)

    (d / "code.py").write_text(entry["code"])

    renders_arr = []
    for view, vname in VIEW_MAP:
        src = Path(render_dir) / sub / f"{name}_{view}.png"
        if src.exists():
            shutil.copy(src, rdir / f"{vname}.png")
            renders_arr.append(f"renders/{vname}.png")
    if want_glb:
        gt = Path(gt_dir) / sub / (name + ".stl")
        if gt.exists():
            try:
                import trimesh
                trimesh.load(str(gt), force="mesh").export(str(rdir / "object.glb"))
                renders_arr.append("renders/object.glb")
            except Exception:
                pass
    step_src = Path(step_dir) / sub / (name + ".step")
    if step_src.exists():
        shutil.copy(step_src, rdir / "model.step")
        renders_arr.append("renders/model.step")

    # status: curated for clean high-fidelity, revise for flagged/low-fidelity
    status = "curated" if (md.get("quality") == "ok" and not md.get("low_fidelity")) else "revise"

    meta = {
        "id": f"deepcad/{sub}/{name}",
        "name": f"deepcad_{name}",
        "type": "3D Objects",
        "language": "CadQuery Python",
        "entry": "code.py",
        "multi_file": False,
        "environment": "CadQuery 2.7 / Python 3.10",
        "renders": renders_arr,
        "source": "DeepCAD",
        "license": "MIT",
        "curator": curator,
        "status": status,
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    curation = {
        "deepcad_id": did,
        "provenance": md.get("provenance"),
        "fidelity": {
            "iou": md.get("iou"),
            "chamfer": md.get("chamfer"),
            "fidelity_tier": md.get("fidelity_tier"),
            "low_fidelity": md.get("low_fidelity"),
            "quality": md.get("quality"),
        },
        "splits": {
            "split_original": md.get("split"),
            "split_dedup": md.get("split_dedup"),
            "is_canonical": md.get("is_canonical"),
            "duplicate_of": md.get("duplicate_of"),
        },
        "text_text2cad": entry.get("text") or {},
        "text_license": md.get("text_license"),
        "note": "text_text2cad is from Text2CAD (CC-BY-NC-SA-4.0); NOT the official caption.",
    }
    (d / "curation.json").write_text(json.dumps(curation, ensure_ascii=False, indent=2))
    return did, "OK"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--step_dir", required=True)
    ap.add_argument("--render_dir", required=True)
    ap.add_argument("--gt_dir", required=True)
    ap.add_argument("--out_root", required=True)
    ap.add_argument("--curator", default="ziyao")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--canonical_only", action="store_true",
                    help="emit only is_canonical==true samples (dedup)")
    ap.add_argument("--no_glb", action="store_true")
    args = ap.parse_args()

    entries = []
    with open(args.jsonl) as f:
        for line in f:
            e = json.loads(line)
            if args.canonical_only and not e["metadata"].get("is_canonical"):
                continue
            entries.append(e)
            if args.limit and len(entries) >= args.limit:
                break

    print(f"Emitting {len(entries)} samples to {args.out_root}/deepcad/ ...")
    results = Parallel(n_jobs=args.workers)(
        delayed(emit_one)(e, args.step_dir, args.render_dir, args.gt_dir,
                          args.out_root, args.curator, not args.no_glb)
        for e in tqdm(entries)
    )
    ok = sum(1 for _, s in results if s == "OK")
    print(f"Emitted {ok}/{len(entries)} sample dirs.")


if __name__ == "__main__":
    main()
