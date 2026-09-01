#!/usr/bin/env python3
"""Generate a natural USER-INSTRUCTION caption for each URDF-only sample with
Gemini Flash, and add it to that sample's captions.json.

The instruction is phrased as what a user would type to ask an AI to GENERATE
this model as URDF code. It is folder-aware:
  - articraft_urdf_geo_only -> geometry-only (box/cylinder/sphere primitives,
    articulated joints, NO colors/materials)
  - articraft_urdf_tex      -> includes materials/colors

Parallel (network-bound), resumable (skips samples that already have the field),
retries on rate-limit. Writes captions.json[FIELD].

Usage:
  GEMINI_API_KEY=... python tools/caption_urdf.py [N] [--workers W] [--model M]
                                                  [--field F] [--scope geo|tex|all] [--force]
"""
import os, sys, json, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
DATA = ROOT + "/data"
GEO = "articraft_urdf_geo_only"
TEX = "articraft_urdf_tex"

argv = sys.argv[1:]
N = 0; WORKERS = 16; MODEL = "gemini-2.5-flash"; FIELD = "user_instruction"; SCOPE = "all"; FORCE = False
_i = 0
while _i < len(argv):
    a = argv[_i]
    if a == "--workers": WORKERS = int(argv[_i + 1]); _i += 2; continue
    if a == "--model": MODEL = argv[_i + 1]; _i += 2; continue
    if a == "--field": FIELD = argv[_i + 1]; _i += 2; continue
    if a == "--scope": SCOPE = argv[_i + 1]; _i += 2; continue
    if a == "--force": FORCE = True; _i += 1; continue
    if a.isdigit(): N = int(a)
    _i += 1

KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
if not KEY:
    kp = os.path.expanduser("~/.gemini_api_key")
    if os.path.exists(kp):
        KEY = open(kp).read().strip()
if not KEY:
    sys.exit("ERROR: no GEMINI_API_KEY (env or ~/.gemini_api_key)")

from google import genai
from google.genai import types as gtypes
client = genai.Client(api_key=KEY)

PROMPT_HEAD = """You are writing a realistic USER INSTRUCTION: the prompt a person would type to ask an AI assistant to GENERATE this 3D model as a URDF file. You are given rendered views of the model AND its URDF source.

Use the RENDERED IMAGES to judge what the object looks like (shape, parts, and — if present — colors/materials). Use the URDF SOURCE to determine the ARTICULATION (which parts move and how: hinges=revolute, sliders=prismatic, spinning=continuous, from the <joint> elements), since motion is not visible in a static image.

Write ONE natural, concise instruction (1-2 sentences, first person / imperative, e.g. "Generate a URDF of ..." or "Create a ... model with ..."). Cover: WHAT the object is, its key parts, and its ARTICULATION (which parts move and how).

Constraints:
- It must read like a user's request to produce a URDF, but do NOT mention raw XML mechanics (no "link tag", "joint element", "<visual>", etc.). Describe the object and its motion naturally.
- Say it should be a URDF model.
- %s
- Output ONLY the instruction text, no quotes, no preamble.

URDF SOURCE:
%s
"""

GEO_LINE = ("This model is GEOMETRY-ONLY: built purely from box/cylinder/sphere PRIMITIVES "
            "with NO colors or materials. Phrase the instruction so the user explicitly asks for a "
            "geometry-only / primitive-only URDF (just shapes and joints, no colors or materials).")
TEX_LINE = ("This model INCLUDES materials and colors. Phrase the instruction so the user asks for a "
            "URDF with appropriate materials/colors on the parts (not just bare geometry).")

_lock = threading.Lock()
_counts = {"done": 0, "skip": 0, "fail": 0}


def samples(scope):
    out = []
    if scope in ("all", "geo"):
        out += [(GEO, n, GEO_LINE) for n in sorted(os.listdir(f"{DATA}/{GEO}"))]
    if scope in ("all", "tex"):
        out += [(TEX, n, TEX_LINE) for n in sorted(os.listdir(f"{DATA}/{TEX}"))]
    return out


def gen_one(item):
    folder, name, line = item
    d = f"{DATA}/{folder}/{name}"
    cp = f"{d}/captions.json"
    try:
        caps = json.load(open(cp)) if os.path.exists(cp) else {}
    except Exception:
        caps = {}
    if not FORCE and caps.get(FIELD):
        with _lock: _counts["skip"] += 1
        return name, "skip"
    try:
        urdf = open(f"{d}/code.urdf").read()
    except Exception as e:
        with _lock: _counts["fail"] += 1
        return name, f"no-urdf:{e}"
    urdf = urdf[:8000]
    prompt = PROMPT_HEAD % (line, urdf)
    contents = [prompt]
    for v in ("view_00.png", "view_04.png"):
        ip = f"{d}/renders/{v}"
        try:
            if os.path.exists(ip) and os.path.getsize(ip) > 1000:
                contents.append(gtypes.Part.from_bytes(data=open(ip, "rb").read(), mime_type="image/png"))
        except Exception:
            pass
    for attempt in range(5):
        try:
            r = client.models.generate_content(
                model=MODEL, contents=contents,
                config=gtypes.GenerateContentConfig(
                    temperature=0.7, max_output_tokens=400,
                    thinking_config=gtypes.ThinkingConfig(thinking_budget=0),  # 关闭thinking,省token给正文
                ),
            )
            txt = (r.text or "").strip().strip('"').strip()
            if not txt:
                raise RuntimeError("empty response")
            caps[FIELD] = txt
            with open(cp, "w") as f:
                json.dump(caps, f, ensure_ascii=False, indent=2)
            with _lock: _counts["done"] += 1
            return name, "ok"
        except Exception as e:
            msg = str(e)
            if any(s in msg for s in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500")):
                time.sleep(2 * (attempt + 1) + (hash(name) % 3))
                continue
            with _lock: _counts["fail"] += 1
            return name, f"err:{msg[:80]}"
    with _lock: _counts["fail"] += 1
    return name, "rate-limited-giveup"


def main():
    items = samples(SCOPE)
    if not FORCE:
        # quick prefilter to estimate
        pass
    if N:
        items = items[:N]
    total = len(items)
    print(f"captioning {total} samples | model={MODEL} field={FIELD} workers={WORKERS}", flush=True)
    t0 = time.time()
    fails = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(gen_one, it) for it in items]
        for k, fut in enumerate(as_completed(futs), 1):
            name, st = fut.result()
            if st not in ("ok", "skip"):
                fails.append((name, st))
            if k % 50 == 0 or k == total:
                el = time.time() - t0
                c = _counts
                print(f"[{k}/{total}] done={c['done']} skip={c['skip']} fail={c['fail']} "
                      f"{el:.0f}s ETA{el/k*(total-k):.0f}s", flush=True)
    print(f"\nDONE done={_counts['done']} skip={_counts['skip']} fail={_counts['fail']} in {time.time()-t0:.0f}s", flush=True)
    for n, s in fails[:20]:
        print("  FAIL", n[:45], s)


if __name__ == "__main__":
    main()
