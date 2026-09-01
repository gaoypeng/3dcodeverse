"""Re-encode the extracted renders to training resolution.

The originals average 220 KB (640x360 or 1024x1024 PNG) and total 84 GB, which is impractical to ship beside a
dataset. Every vision tower we train downsamples to 448x448 anyway (our own 27B run set image_max_pixels to
448^2), so a <=448px JPEG loses nothing a training run would have seen — and the untouched originals stay in the
corpus tars for anyone who needs them. Measured saving: 95%, so 84 GB becomes about 4 GB.

usage: python scripts/shrink_pair_images.py [--root DIR] [--out DIR] [--max_px 448] [--quality 88] [--workers 64]
"""
import argparse
import glob
import os
from concurrent.futures import ProcessPoolExecutor

from PIL import Image


def one(args):
    src, dst, max_px, q = args
    if os.path.exists(dst):
        return 0, 0, 0
    try:
        im = Image.open(src)
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        im.save(dst, "JPEG", quality=q, optimize=True)
        return os.path.getsize(src), os.path.getsize(dst), 1
    except Exception:
        return 0, 0, -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/wekafs/ict/hx_624/data/pair_images")
    ap.add_argument("--out", default="/wekafs/ict/hx_624/data/pair_images_small")
    ap.add_argument("--max_px", type=int, default=448)
    ap.add_argument("--quality", type=int, default=88)
    ap.add_argument("--workers", type=int, default=64)
    a = ap.parse_args()
    srcs = glob.glob(os.path.join(a.root, "**", "*.png"), recursive=True)
    print(f"[shrink] {len(srcs):,} renders -> {a.out} at <={a.max_px}px q{a.quality}", flush=True)
    jobs = [(s, os.path.join(a.out, os.path.relpath(s, a.root)).rsplit(".", 1)[0] + ".jpg", a.max_px, a.quality)
            for s in srcs]
    big = small = done = failed = 0
    with ProcessPoolExecutor(a.workers) as ex:
        for i, (b, s, ok) in enumerate(ex.map(one, jobs, chunksize=64), 1):
            big += b; small += s
            if ok == 1: done += 1
            elif ok < 0: failed += 1
            if i % 50000 == 0:
                print(f"[shrink] {i:,}/{len(jobs):,} | {big/1e9:.1f} GB -> {small/1e9:.1f} GB | failed {failed}", flush=True)
    print(f"[shrink] DONE {done:,} written, {failed} failed | {big/1e9:.1f} GB -> {small/1e9:.1f} GB "
          f"({(1-small/max(big,1))*100:.1f}% smaller)", flush=True)


if __name__ == "__main__":
    main()
