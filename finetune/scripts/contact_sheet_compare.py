"""3-column contact sheet on the SAME tasks: model A vs model B vs GT (tasks = the selection used by contact_sheet_3dcodebench.py for model A)."""
import json, os, sys, subprocess, random
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont
BL = "/wekafs/ict/hx_624/tools/blender-5.0.1-linux-x64/blender"; R = os.path.abspath("eval/render_glb.py"); GT = "/wekafs/ict/hx_624/data/3dcodebench/data"
dirA, nameA, dirB, nameB, out_png = sys.argv[1:6]; k = 4
mA = {r["task"]: r for r in map(json.loads, open(f"{dirA}/metrics.jsonl"))}; mB = {r["task"]: r for r in map(json.loads, open(f"{dirB}/metrics.jsonl"))}
ok = sorted([r for r in mA.values() if r["exec"] == "OK" and r.get("f@0.1") is not None], key=lambda r: -r["f@0.1"]); random.seed(3); n = len(ok)
sel = [("best", r) for r in ok[:k]] + [("median", r) for r in ok[n//2 - k//2: n//2 - k//2 + k]] + [("worst", r) for r in ok[-k:]] + [("random", r) for r in random.sample(ok[k:-k], k)]
def render(glb, png):
    if os.path.exists(png) or not os.path.exists(glb): return
    subprocess.run([BL, "-b", "--factory-startup", "-noaudio", "--python", R, "--", "--glb", glb, "--out", png], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300, env={**os.environ, "HOME": "/tmp"})
wA = os.path.abspath(f"{dirA}/_renders"); wB = os.path.abspath(f"{dirB}/_renders"); os.makedirs(wA, exist_ok=True); os.makedirs(wB, exist_ok=True)
jobs = []
for tag, r in sel:
    t = r["task"]; jobs += [(os.path.abspath(f"{dirA}/{t}/out.glb"), f"{wA}/{t}_gen.png"), (os.path.abspath(f"{dirB}/{t}/out.glb"), f"{wB}/{t}_gen.png"), (f"{GT}/{t}/glb/{t}.glb", f"{wA}/{t}_gt.png")]
with ThreadPoolExecutor(8) as ex: list(ex.map(lambda j: render(*j), jobs))
S = 180; cols = k; rows = 4
sheet = Image.new("RGB", (cols * (3 * S + 16) + 40, rows * (S + 52) + 10), "white"); d = ImageDraw.Draw(sheet)
try: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12); fb = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
except Exception: font = fb = ImageFont.load_default()
for i, (tag, r) in enumerate(sel):
    row, col = i // cols, i % cols; x0 = 40 + col * (3 * S + 16); y0 = 10 + row * (S + 52); t = r["task"]
    for j, p in enumerate((f"{wA}/{t}_gen.png", f"{wB}/{t}_gen.png", f"{wA}/{t}_gt.png")):
        try: im = Image.open(p).convert("RGB").resize((S, S))
        except Exception: im = Image.new("RGB", (S, S), (230, 230, 230))
        sheet.paste(im, (x0 + j * S, y0 + 20))
    fa = r["f@0.1"]; fbv = mB.get(t, {}).get("f@0.1"); sb = f"{fbv:.2f}" if fbv is not None else mB.get(t, {}).get("exec", "?")
    d.text((x0, y0 + 2), f"{t.replace('_seed0','')}  F@0.1 {nameA}={fa:.2f} | {nameB}={sb}", fill="black", font=fb)
    d.text((x0, y0 + S + 24), nameA, fill=(60, 60, 60), font=font); d.text((x0 + S, y0 + S + 24), nameB, fill=(60, 60, 60), font=font); d.text((x0 + 2 * S, y0 + S + 24), "ground truth", fill=(60, 60, 60), font=font)
    if col == 0: d.text((4, y0 + S // 2), tag, fill=(120, 0, 0), font=fb)
sheet.save(out_png); print("wrote", out_png, sheet.size)
