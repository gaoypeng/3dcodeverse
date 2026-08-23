"""Render generated vs GT for a spread of 3DCodeBench tasks and compose a labelled contact sheet.
usage: python scripts/contact_sheet_3dcodebench.py <eval_out_dir> <out_png> [n_per_bucket]"""
import json, os, sys, subprocess, random
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont
BL = "/wekafs/ict/hx_624/tools/blender-5.0.1-linux-x64/blender"; R = os.path.abspath("eval/render_glb.py"); GT = "/wekafs/ict/hx_624/data/3dcodebench/data"
gen_dir, out_png = sys.argv[1], sys.argv[2]; k = int(sys.argv[3]) if len(sys.argv) > 3 else 4
m = [json.loads(l) for l in open(f"{gen_dir}/metrics.jsonl")]; ok = sorted([r for r in m if r["exec"] == "OK" and r.get("f@0.1") is not None], key=lambda r: -r["f@0.1"])
random.seed(3); n = len(ok)
sel = [("best", r) for r in ok[:k]] + [("median", r) for r in ok[n//2 - k//2: n//2 - k//2 + k]] + [("worst", r) for r in ok[-k:]] + [("random", r) for r in random.sample(ok[k:-k], k)]
work = os.path.abspath(f"{gen_dir}/_renders"); os.makedirs(work, exist_ok=True)
def render(glb, png):
    if os.path.exists(png): return
    subprocess.run([BL, "-b", "--factory-startup", "-noaudio", "--python", R, "--", "--glb", glb, "--out", png], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300, env={**os.environ, "HOME": "/tmp"})
jobs = []
for tag, r in sel:
    t = r["task"]; jobs.append((os.path.abspath(f"{gen_dir}/{t}/out.glb"), f"{work}/{t}_gen.png")); jobs.append((f"{GT}/{t}/glb/{t}.glb", f"{work}/{t}_gt.png"))
with ThreadPoolExecutor(8) as ex: list(ex.map(lambda j: render(*j), jobs))
S = 200; cols = k; rows = 4
sheet = Image.new("RGB", (cols * (2 * S + 16) + 40, rows * (S + 52) + 10), "white"); d = ImageDraw.Draw(sheet)
try: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13); fb = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
except Exception: font = fb = ImageFont.load_default()
for i, (tag, r) in enumerate(sel):
    row, col = i // cols, i % cols; x0 = 40 + col * (2 * S + 16); y0 = 10 + row * (S + 52); t = r["task"]
    for j, kind in enumerate(("gen", "gt")):
        p = f"{work}/{t}_{kind}.png"
        try: im = Image.open(p).convert("RGB").resize((S, S))
        except Exception: im = Image.new("RGB", (S, S), (230, 230, 230))
        sheet.paste(im, (x0 + j * S, y0 + 20))
    d.text((x0, y0 + 2), f"{t.replace('_seed0','')}  F@0.1={r['f@0.1']:.2f}", fill="black", font=fb)
    d.text((x0, y0 + S + 24), "generated (md_xl)", fill=(60, 60, 60), font=font); d.text((x0 + S, y0 + S + 24), "ground truth", fill=(60, 60, 60), font=font)
    if col == 0: d.text((4, y0 + S // 2), tag, fill=(120, 0, 0), font=fb)
sheet.save(out_png); print("wrote", out_png, sheet.size, "| tasks:", [r["task"] for _, r in sel])
