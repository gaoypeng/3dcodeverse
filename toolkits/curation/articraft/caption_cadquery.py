#!/usr/bin/env python3
"""Generate a natural USER-INSTRUCTION caption for each with_mesh (CadQuery-code)
sample with Gemini Flash, and add it to that sample's captions.json.

The instruction is phrased as what a user would type to ask an AI to GENERATE
this 3D object as parametric CadQuery (Python) code (the with_mesh deliverable is
a self-contained CadQuery program; the rendered object keeps per-part colors, so
it INCLUDES materials/colors). Multimodal: 2 rendered views + the model-body
source (the geometry/build code, not the inlined shim). Cover object identity,
key parts, articulation, and materials.

Parallel (network-bound), resumable (skips samples that already have the field),
retries on rate-limit. Writes captions.json[FIELD].

Usage:
  GEMINI_API_KEY=... python tools/caption_cadquery.py [N] [--workers W] [--model M]
                                                      [--field F] [--force]
"""
import os, sys, json, time, re, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
DD = ROOT + "/data/articraft_with_mesh"

argv = sys.argv[1:]
N = 0; WORKERS = 600; MODEL = "gemini-3.5-flash"; FIELD = "user_instruction_gemini_3_5_flash"; FORCE = False; RPM = 900
_i = 0
while _i < len(argv):
    a = argv[_i]
    if a == "--workers": WORKERS = int(argv[_i + 1]); _i += 2; continue
    if a == "--model": MODEL = argv[_i + 1]; _i += 2; continue
    if a == "--field": FIELD = argv[_i + 1]; _i += 2; continue
    if a == "--rpm": RPM = int(argv[_i + 1]); _i += 2; continue
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

import httpx
from google import genai
from google.genai import types as gtypes

# big httpx connection pool so high worker counts actually run concurrently
# (default pool is ~100 connections -> caps throughput regardless of workers)
_POOL = max(WORKERS + 64, 128)
client = genai.Client(api_key=KEY, http_options=gtypes.HttpOptions(
    timeout=120000,  # ms; flash+images can take ~30-50s
    client_args={"limits": httpx.Limits(max_connections=_POOL, max_keepalive_connections=_POOL)},
))


class RateLimiter:
    """Token-spacing limiter: at most RPM requests per minute, shared across all
    worker threads. Lets us push high concurrency up to the model's RPM quota
    (gemini-3.5-flash ~1000/min) without tripping 429s."""
    def __init__(self, rpm):
        self.interval = 60.0 / max(rpm, 1)
        self.lock = threading.Lock()
        self.next_t = 0.0

    def acquire(self):
        with self.lock:
            now = time.time()
            t = self.next_t if self.next_t > now else now
            self.next_t = t + self.interval
            wait = t - now
        if wait > 0:
            time.sleep(wait)


_limiter = RateLimiter(RPM)

PROMPT_HEAD = """You are writing a realistic USER INSTRUCTION: the prompt a person would type to ask an AI assistant to GENERATE this 3D model as parametric CadQuery (Python) code.

PRIMARY SOURCE = the RENDERED IMAGES. Judge from the images what the object IS, its shape and key parts, and its colors/materials.

The STRUCTURED SUMMARY below gives the object's name and its ARTICULATION (which parts move and how) — motion is invisible in a static image, so rely on it for the movement, and use the part names as hints. (Ignore it where it conflicts with what you clearly see in the images.)

Write ONE natural, concise instruction (1-2 sentences, first person / imperative, e.g. "Generate CadQuery code for ..." or "Create a parametric ... model in CadQuery with ..."). Cover: WHAT the object is, its key parts, its ARTICULATION (which parts move and how), and that it should have appropriate materials/colors.

Constraints:
- It must read like a user's request to produce CadQuery code, but do NOT mention internal API or class names and no raw geometry-kernel jargon. Describe the object and its motion naturally.
- Say it should be parametric CadQuery (Python) code.
- This model INCLUDES materials and colors — phrase it so the user asks for appropriate materials/colors on the parts (not bare geometry).
- Output ONLY the instruction text, no quotes, no preamble.

STRUCTURED SUMMARY:
%s
"""

_lock = threading.Lock()
_counts = {"done": 0, "skip": 0, "fail": 0}

try:
    from PIL import Image
    import io
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False


def _small_jpeg(path, maxdim=512):
    """Downscale a render to <=512px on a white background as JPEG — cuts the
    per-request payload from ~1-2MB PNG to ~40KB, which sharply lowers latency
    (the real throughput bottleneck) so we can hit the RPM quota."""
    try:
        if not (os.path.exists(path) and os.path.getsize(path) > 1000):
            return None
        if not _HAVE_PIL:
            return open(path, "rb").read()
        im = Image.open(path).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im).convert("RGB")
        im.thumbnail((maxdim, maxdim))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        try:
            return open(path, "rb").read()
        except Exception:
            return None


def passed(n):
    try:
        return (json.load(open(f"{DD}/{n}/meta.json")).get("conversion") or {}).get("verifier_pass") is True
    except Exception:
        return False


def extract_prior(src):
    """Compact, semantically-dense summary from the source (NOT the geometry math):
    object name + part names + movable joints & their type. Most of the code is
    B-rep/shim with no caption value, so we feed only this prior + the images."""
    lines = []
    m = re.search(r'ArticulatedObject\(\s*name\s*=\s*["\']([^"\']+)', src)
    if m:
        lines.append("Object name: " + m.group(1).replace("_", " "))
    parts = re.findall(r'\.part\(\s*["\']([^"\']+)', src)
    if parts:
        lines.append("Parts: " + ", ".join(p.replace("_", " ") for p in parts))
    arts = re.findall(r'\.articulation\(\s*["\']([^"\']+)["\']\s*,\s*ArticulationType\.(\w+)', src)
    moving = [(n, t) for n, t in arts if t.upper() != "FIXED"]
    if moving:
        lines.append("Movable joints: " + "; ".join(
            f"{n.replace('_', ' ')} ({t.lower()})" for n, t in moving))
    elif arts:
        lines.append("All joints are fixed (rigid assembly, no moving parts).")
    return "\n".join(lines) or "(no structured info available)"


def samples():
    return [n for n in sorted(os.listdir(DD))
            if passed(n) and os.path.exists(f"{DD}/{n}/renders/view_00.png")]


def gen_one(name):
    d = f"{DD}/{name}"
    cp = f"{d}/captions.json"
    try:
        caps = json.load(open(cp)) if os.path.exists(cp) else {}
    except Exception:
        caps = {}
    if not FORCE and caps.get(FIELD):
        with _lock: _counts["skip"] += 1
        return name, "skip"
    try:
        sp = f"{d}/articraft_source_code.py"   # clean structure (ArticulationType, model.part)
        if not os.path.exists(sp):
            sp = f"{d}/code.py"
        prior = extract_prior(open(sp).read())
    except Exception as e:
        with _lock: _counts["fail"] += 1
        return name, f"no-code:{e}"
    contents = [PROMPT_HEAD % prior]
    for v in ("view_00.png", "view_04.png"):
        b = _small_jpeg(f"{d}/renders/{v}")
        if b:
            contents.append(gtypes.Part.from_bytes(data=b, mime_type="image/jpeg"))
    for attempt in range(5):
        try:
            _limiter.acquire()
            r = client.models.generate_content(
                model=MODEL, contents=contents,
                config=gtypes.GenerateContentConfig(
                    temperature=0.7, max_output_tokens=400,
                    thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
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
    items = samples()
    if N:
        items = items[:N]
    total = len(items)
    print(f"captioning {total} with_mesh samples | model={MODEL} field={FIELD} workers={WORKERS}", flush=True)
    t0 = time.time(); fails = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(gen_one, it) for it in items]
        for k, fut in enumerate(as_completed(futs), 1):
            name, st = fut.result()
            if st not in ("ok", "skip"):
                fails.append((name, st))
            if k % 50 == 0 or k == total:
                el = time.time() - t0; c = _counts
                print(f"[{k}/{total}] done={c['done']} skip={c['skip']} fail={c['fail']} "
                      f"{el:.0f}s ETA{el/k*(total-k):.0f}s", flush=True)
    print(f"\nCAPTION_DONE done={_counts['done']} skip={_counts['skip']} fail={_counts['fail']} in {time.time()-t0:.0f}s", flush=True)
    for n, s in fails[:20]:
        print("  FAIL", n[:45], s)


if __name__ == "__main__":
    main()
