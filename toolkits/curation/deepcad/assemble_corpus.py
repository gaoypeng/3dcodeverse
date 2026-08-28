"""
assemble_corpus.py
Merge CadQuery code + STEP + renders + Text2CAD text into a 3DCodeVerse JSONL.

Quality policy (from failure analysis):
  - OK       -> quality="ok"               (valid solid)
  - INVALID  -> quality="geometry_warning" (exports STEP/renders but fails strict
                topological validity; usually sub-mm thin geometry from DeepCAD
                quantization). Kept, downstream can filter.
  - others (DEGENERATE/EXEC_FAIL/CRASH/CONVERT_FAIL) -> excluded.

Code source: fixed_cq/ if present (agent-fixed), else converted_cq/.
"""
import json
import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validation", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--cq_dir", required=True)
    ap.add_argument("--fixed_dir", default="")
    ap.add_argument("--step_dir", required=True)
    ap.add_argument("--render_dir", default="")
    ap.add_argument("--iou", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--include_invalid", action="store_true", default=True)
    args = ap.parse_args()

    val = json.load(open(args.validation))
    text_map = json.load(open(args.text))
    iou_map = json.load(open(args.iou)) if args.iou else {}
    split = json.load(open(args.split))
    id2split = {}
    for s in ("train", "validation", "test"):
        for mid in split.get(s, []):
            id2split[mid] = s

    keep = {mid["id"]: "ok" for mid in val.get("OK", [])}
    if args.include_invalid:
        for mid in val.get("INVALID", []):
            keep[mid["id"]] = "geometry_warning"

    cq_dir = Path(args.cq_dir)
    fixed_dir = Path(args.fixed_dir) if args.fixed_dir else None
    step_dir = Path(args.step_dir)
    render_dir = Path(args.render_dir) if args.render_dir else None
    views = ["iso", "front", "right", "top"]

    n_with_text = 0
    n_with_render = 0
    with open(args.out, "w") as out:
        for model_id in sorted(keep):
            sub, name = model_id.split("/")
            quality = keep[model_id]

            py = None
            fixed = False
            if fixed_dir and (fixed_dir / sub / (name + ".py")).exists():
                py = fixed_dir / sub / (name + ".py")
                fixed = True
            else:
                py = cq_dir / sub / (name + ".py")
            if not py.exists():
                continue

            step_path = step_dir / sub / (name + ".step")
            renders = {}
            if render_dir:
                for v in views:
                    rp = render_dir / sub / f"{name}_{v}.png"
                    if rp.exists():
                        renders[v] = str(rp)
            if renders:
                n_with_render += 1

            text = text_map.get(model_id, {})
            if text:
                n_with_text += 1

            iou_rec = iou_map.get(model_id, {})
            iou = iou_rec.get("iou")
            chamfer = iou_rec.get("chamfer")

            entry = {
                "id": f"deepcad_MIT_{sub}_{name}",
                "source": "DeepCAD",
                "license": "MIT",
                "code_language": "cadquery_python",
                "code": py.read_text(),
                "step_path": str(step_path),
                "renders": renders,
                "text": text,  # {abstract, beginner, intermediate, expert} (Text2CAD, CC BY-NC-SA)
                "metadata": {
                    "quality": quality,
                    "iou": iou,            # geometric fidelity vs DeepCAD ground-truth
                    "chamfer": chamfer,
                    "conversion": "agent_fixed" if fixed else "auto",
                    "provenance": "ABC/Onshape -> DeepCAD (ICCV 2021) -> CadQuery",
                    "deepcad_id": model_id,
                    "split": id2split.get(model_id, "unknown"),
                    "text_license": "CC-BY-NC-SA-4.0" if text else None,
                },
            }
            out.write(json.dumps(entry) + "\n")

    n = sum(1 for _ in open(args.out))
    print(f"Wrote {n} entries to {args.out}")
    print(f"  with text: {n_with_text}  with renders: {n_with_render}")
    print(f"  quality: ok={sum(1 for v in keep.values() if v=='ok')}, "
          f"geometry_warning={sum(1 for v in keep.values() if v=='geometry_warning')}")


if __name__ == "__main__":
    main()
