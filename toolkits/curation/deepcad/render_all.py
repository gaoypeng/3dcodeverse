"""
render_all.py
Render multi-view (iso / front / right / top) PNG previews of each STEP file
using cadquery tessellation + pyrender (headless OSMesa).

Usage:
  python render_all.py --step_dir /data/.../step_files --out_dir /data/.../renders \
      --workers 24 [--size 512] [--limit N]
  python render_all.py --one /path/to.step --out_prefix /tmp/foo   # single-model test
"""
import os
os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")
import sys
import argparse
from pathlib import Path

import numpy as np
import trimesh
import pyrender
import cadquery as cq
from PIL import Image

# view direction (camera sits along +dir looking at origin) and up vector
VIEWS = {
    "iso":   (np.array([1.0, -1.0, 1.0]),  np.array([0.0, 0.0, 1.0])),
    "front": (np.array([0.0, -1.0, 0.0]),  np.array([0.0, 0.0, 1.0])),
    "right": (np.array([1.0, 0.0, 0.0]),   np.array([0.0, 0.0, 1.0])),
    "top":   (np.array([0.0, 0.0, 1.0]),   np.array([0.0, 1.0, 0.0])),
}


def look_at(eye, target, up):
    """4x4 camera pose (OpenGL convention: camera looks down -Z)."""
    f = (target - eye); f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-8:
        up = np.array([1.0, 0.0, 0.0])
        s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[:3, 0] = s
    m[:3, 1] = u
    m[:3, 2] = -f
    m[:3, 3] = eye
    return m


def step_to_mesh(step_path, tol=0.05):
    shape = cq.importers.importStep(str(step_path)).val()
    verts, faces = shape.tessellate(tol)
    v = np.array([[p.x, p.y, p.z] for p in verts], dtype=float)
    f = np.array(faces, dtype=np.int64)
    return trimesh.Trimesh(vertices=v, faces=f, process=False)


def render_mesh(mesh, out_prefix, size=512):
    mesh = mesh.copy()
    mesh.apply_translation(-mesh.bounding_box.centroid)
    radius = float(np.linalg.norm(mesh.bounding_box.extents)) / 2.0 or 1.0
    dist = radius * 3.0

    pmesh = pyrender.Mesh.from_trimesh(mesh, smooth=False)
    r = pyrender.OffscreenRenderer(size, size)
    for name, (d, up) in VIEWS.items():
        d = d / np.linalg.norm(d)
        eye = d * dist
        pose = look_at(eye, np.zeros(3), up)
        scene = pyrender.Scene(bg_color=[1.0, 1.0, 1.0, 1.0],
                               ambient_light=[0.3, 0.3, 0.3])
        scene.add(pmesh)
        cam = pyrender.PerspectiveCamera(yfov=np.pi / 4.0, aspectRatio=1.0)
        scene.add(cam, pose=pose)
        scene.add(pyrender.DirectionalLight(color=[1, 1, 1], intensity=4.0), pose=pose)
        color, _ = r.render(scene)
        Image.fromarray(color).save(f"{out_prefix}_{name}.png")
    r.delete()


def render_one(step_path, out_prefix, size=512):
    mesh = step_to_mesh(step_path)
    render_mesh(mesh, out_prefix, size)


def _worker(step_path, out_dir, size):
    sub = step_path.parent.name
    name = step_path.stem
    od = Path(out_dir) / sub
    od.mkdir(parents=True, exist_ok=True)
    try:
        render_one(step_path, str(od / name), size)
        return f"{sub}/{name}", "OK"
    except Exception as e:
        return f"{sub}/{name}", f"FAIL: {type(e).__name__}: {e}"[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step_dir")
    ap.add_argument("--out_dir")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--one")
    ap.add_argument("--out_prefix")
    args = ap.parse_args()

    if args.one:
        render_one(Path(args.one), args.out_prefix or "/tmp/render", args.size)
        print("rendered:", args.out_prefix)
        return

    from joblib import Parallel, delayed
    from tqdm import tqdm
    steps = sorted(Path(args.step_dir).rglob("*.step"))
    if args.limit:
        steps = steps[: args.limit]
    print(f"Rendering {len(steps)} models x4 views, {args.workers} workers...")
    results = Parallel(n_jobs=args.workers)(
        delayed(_worker)(s, args.out_dir, args.size) for s in tqdm(steps)
    )
    fails = [r for r in results if r[1] != "OK"]
    print(f"Rendered OK: {len(results)-len(fails)}/{len(results)}, failed: {len(fails)}")
    import json
    json.dump({"fail": fails}, open(Path(args.out_dir) / "render_report.json", "w"), indent=2)


if __name__ == "__main__":
    main()
