"""
viz_compare.py
Side-by-side visual diff: OUR converted geometry (from STEP) vs DeepCAD's
official ground-truth (from gt STL), same iso view, labeled with IoU.
Stacks several models into one comparison grid PNG.
"""
import os
os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")
import sys
import json
import argparse
import numpy as np
import trimesh
import pyrender
import cadquery as cq
from PIL import Image, ImageDraw

W = "/data/deepcad2cq"


def look_at(eye, target, up):
    f = target - eye; f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-8:
        up = np.array([1., 0, 0]); s = np.cross(f, up)
    s = s / np.linalg.norm(s); u = np.cross(s, f)
    m = np.eye(4); m[:3, 0] = s; m[:3, 1] = u; m[:3, 2] = -f; m[:3, 3] = eye
    return m


def render(m, size=420, color=(150, 170, 200)):
    m = m.copy(); m.apply_translation(-m.bounding_box.centroid)
    rad = float(np.linalg.norm(m.bounding_box.extents)) / 2 or 1.0
    dist = rad * 3.0
    d = np.array([1., -1., 1.]) / np.sqrt(3); eye = d * dist
    pose = look_at(eye, np.zeros(3), np.array([0, 0, 1.]))
    mat = pyrender.MetallicRoughnessMaterial(baseColorFactor=[c/255 for c in color] + [1.0],
                                             metallicFactor=0.1, roughnessFactor=0.7)
    pm = pyrender.Mesh.from_trimesh(m, smooth=False, material=mat)
    sc = pyrender.Scene(bg_color=[1, 1, 1, 1], ambient_light=[0.35, 0.35, 0.35])
    sc.add(pm)
    cam = pyrender.PerspectiveCamera(yfov=np.pi/4, aspectRatio=1)
    sc.add(cam, pose=pose)
    sc.add(pyrender.DirectionalLight(color=[1, 1, 1], intensity=4.0), pose=pose)
    r = pyrender.OffscreenRenderer(size, size)
    col, _ = r.render(sc); r.delete()
    return Image.fromarray(col)


def ours_mesh(mid):
    sub, name = mid.split("/")
    shape = cq.importers.importStep(f"{W}/step_files/{sub}/{name}.step").val()
    v, f = shape.tessellate(0.02)
    return trimesh.Trimesh(np.array([[p.x, p.y, p.z] for p in v]), np.array(f), process=False)


def gt_mesh(mid):
    sub, name = mid.split("/")
    return trimesh.load(f"{W}/gt_stl/{sub}/{name}.stl", force="mesh")


def main():
    ids = sys.argv[1].split(",")
    iou = json.load(open(f"{W}/iou_results_full.json"))
    rows = []
    sz = 420
    for mid in ids:
        try:
            o = render(ours_mesh(mid), sz, (120, 160, 210))
            g = render(gt_mesh(mid), sz, (160, 200, 160))
            rec = iou.get(mid, {})
            label = f"{mid}   IoU={rec.get('iou')}"
            row = Image.new("RGB", (sz*2 + 30, sz + 28), (255, 255, 255))
            row.paste(o, (0, 28)); row.paste(g, (sz + 30, 28))
            dr = ImageDraw.Draw(row)
            dr.text((6, 8), f"OURS (CadQuery)  |  GROUND-TRUTH (DeepCAD)     {label}", fill=(0, 0, 0))
            rows.append(row)
        except Exception as e:
            print(f"skip {mid}: {e}")
    if rows:
        H = sum(r.height for r in rows) + 10*len(rows)
        grid = Image.new("RGB", (rows[0].width, H), (255, 255, 255))
        y = 0
        for r in rows:
            grid.paste(r, (0, y)); y += r.height + 10
        grid.save(f"{W}/compare_grid.png")
        print(f"saved {W}/compare_grid.png  ({len(rows)} models)")


if __name__ == "__main__":
    main()
