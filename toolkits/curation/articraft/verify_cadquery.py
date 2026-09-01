#!/usr/bin/env python3
"""Deterministic verification harness for Articraft CadQuery reproductions.

Usage:
    python verify_cadquery.py <sample_dir> <code_py_path> [--render out.png]

It compares a self-contained CadQuery script (which builds the model in REST
pose) against the baked ground-truth meshes that the original Articraft record
produced (HF dataset camvsl/Articraft-10K).

Pipeline
--------
1. Read <sample_dir>/meta.json -> hf_name. Download
   https://huggingface.co/datasets/camvsl/Articraft-10K/resolve/main/<hf_name>.tar.gz
   (Bearer token), extract model.urdf + assets/meshes/*.obj, and combine the
   meshes/primitives placed per the URDF kinematic tree (rest pose) into ONE
   trimesh. Cache to /tmp/articraft_gt/<hf_name>.{stl,dir}.
2. Exec <code_py_path> in a fresh namespace; obtain the model object (see
   MODEL EXTRACTION CONVENTION below). Tessellate to ONE trimesh.
3. Normalize BOTH meshes (center to bbox origin, scale so max bbox dim = 1).
   Compute symmetric chamfer distance (~30k points each way), per-axis
   bbox-dim ratio (mine/GT), convex-hull volume ratio.
4. Print a single JSON line with chamfer / bbox_ratio / volume_ratio / pass /
   error. pass = (error is None) AND chamfer < 0.02 AND every bbox_ratio in
   [0.9, 1.1].
5. --render: save a side-by-side PNG (GT vs candidate).

Always exits 0 with a parseable JSON line (errors -> pass=false, error set).

MODEL EXTRACTION CONVENTION
---------------------------
The generated code.py SHOULD define a module-level variable `result` holding
the whole model (cq.Assembly preferred, or cq.Workplane / cq.Shape) in rest
pose. The extractor looks, in order:
    1. module-level `result`
    2. module-level `object_model`
    3. the last module-level value that is a cq.Assembly / cq.Workplane /
       cq.Shape (Compound, Solid, ...)
    4. fallback for hand-written PoCs that build under `if __name__ ==
       "__main__"`: call a zero-arg module-level builder function whose name
       matches a known allowlist (build_assembly, build_compound,
       build_model, build_object_model, build, make_model, ...) and return its
       cq object.
The module is exec'd with __name__ != "__main__" so any bottom `if __name__ ==
"__main__"` block (file writes / sys.argv) does NOT run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

import numpy as np

GT_CACHE = "/tmp/articraft_gt"
HF_URL = "https://huggingface.co/datasets/camvsl/Articraft-10K/resolve/main/{}.tar.gz"
TOKEN_PATH = os.path.expanduser("~/.hf_token")

CHAMFER_PASS = 0.02
BBOX_LO, BBOX_HI = 0.9, 1.1
N_SAMPLES = 30000
TESS_TOL = 1e-4
TESS_ANG = 0.1


# --------------------------------------------------------------------------- #
# Ground-truth construction from the URDF
# --------------------------------------------------------------------------- #
def _rpy_to_R(roll, pitch, yaw):
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    Rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    Ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _origin_T(elem):
    T = np.eye(4)
    if elem is None:
        return T
    o = elem.find("origin")
    if o is None:
        return T
    xyz = [float(v) for v in o.get("xyz", "0 0 0").split()]
    rpy = [float(v) for v in o.get("rpy", "0 0 0").split()]
    T[:3, :3] = _rpy_to_R(*rpy)
    T[:3, 3] = xyz
    return T


def _read_token():
    for p in (TOKEN_PATH, os.environ.get("HF_TOKEN_FILE", "")):
        if p and os.path.exists(p):
            with open(p) as f:
                return f.read().strip()
    tok = os.environ.get("HF_TOKEN", "").strip()
    if tok:
        return tok
    raise RuntimeError("HF token not found at %s" % TOKEN_PATH)


def _download_and_extract(hf_name, dest_dir):
    """Download <hf_name>.tar.gz and extract into dest_dir. Returns the root
    directory that contains model.urdf."""
    os.makedirs(dest_dir, exist_ok=True)
    tar_path = os.path.join(GT_CACHE, hf_name + ".tar.gz")
    if not os.path.exists(tar_path) or os.path.getsize(tar_path) == 0:
        url = HF_URL.format(hf_name)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + _read_token()})
        with urllib.request.urlopen(req, timeout=300) as resp, open(tar_path, "wb") as out:
            out.write(resp.read())
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(dest_dir)
    # locate model.urdf
    for root, _dirs, files in os.walk(dest_dir):
        if "model.urdf" in files:
            return root
    raise RuntimeError("model.urdf not found in %s" % tar_path)


def _coerce_trimesh(obj):
    import trimesh

    if isinstance(obj, trimesh.Trimesh):
        return obj
    if isinstance(obj, trimesh.Scene):
        geoms = list(obj.dump())
        if not geoms:
            raise RuntimeError("empty scene")
        return trimesh.util.concatenate(geoms)
    # PointCloud / Path / other
    raise RuntimeError("unsupported geometry type %r" % type(obj))


def _build_gt_mesh(urdf_root):
    import trimesh

    tree = ET.parse(os.path.join(urdf_root, "model.urdf"))
    root = tree.getroot()
    links = {l.get("name"): l for l in root.findall("link")}
    joints = root.findall("joint")

    parent_of, joint_T, children = {}, {}, set()
    for j in joints:
        child = j.find("child").get("link")
        parent = j.find("parent").get("link")
        parent_of[child] = parent
        joint_T[child] = _origin_T(j)  # rest pose: joint variable = 0
        children.add(child)

    def world_T(name):
        T = np.eye(4)
        chain = []
        cur = name
        while cur in parent_of:
            chain.append(cur)
            cur = parent_of[cur]
        for n in reversed(chain):
            T = T @ joint_T[n]
        return T

    meshes = []
    for name, link in links.items():
        Tlink = world_T(name)
        for vis in link.findall("visual"):
            Tvis = Tlink @ _origin_T(vis)
            geom = vis.find("geometry")
            if geom is None:
                continue
            mesh_e = geom.find("mesh")
            box_e = geom.find("box")
            cyl_e = geom.find("cylinder")
            sph_e = geom.find("sphere")
            m = None
            if mesh_e is not None:
                fn = os.path.join(urdf_root, mesh_e.get("filename"))
                m = _coerce_trimesh(trimesh.load(fn, process=False))
                sc = mesh_e.get("scale")
                if sc:
                    s = [float(v) for v in sc.split()]
                    m = m.copy()
                    m.apply_scale(s if len(s) == 3 else s[0])
            elif box_e is not None:
                sx, sy, sz = [float(v) for v in box_e.get("size").split()]
                m = trimesh.creation.box(extents=(sx, sy, sz))
            elif cyl_e is not None:
                r = float(cyl_e.get("radius"))
                h = float(cyl_e.get("length"))
                m = trimesh.creation.cylinder(radius=r, height=h, sections=48)
            elif sph_e is not None:
                m = trimesh.creation.icosphere(radius=float(sph_e.get("radius")))
            if m is not None:
                m = m.copy()
                m.apply_transform(Tvis)
                meshes.append(m)
    if not meshes:
        raise RuntimeError("no visual geometry in URDF")
    return trimesh.util.concatenate(meshes)


def get_gt_mesh(hf_name):
    """Build & cache the ground-truth combined mesh. Returns a trimesh.Trimesh."""
    import trimesh

    os.makedirs(GT_CACHE, exist_ok=True)
    stl_cache = os.path.join(GT_CACHE, hf_name + ".stl")
    if os.path.exists(stl_cache) and os.path.getsize(stl_cache) > 0:
        return _coerce_trimesh(trimesh.load(stl_cache, process=False))
    urdf_root = _download_and_extract(hf_name, os.path.join(GT_CACHE, hf_name + "_src"))
    gt = _build_gt_mesh(urdf_root)
    try:
        gt.export(stl_cache)
    except Exception:
        pass
    return gt


# --------------------------------------------------------------------------- #
# Candidate model extraction + tessellation
# --------------------------------------------------------------------------- #
BUILDER_NAMES = [
    "build_assembly",
    "build_compound",
    "build_model",
    "build_object_model",
    "build_object",
    "build_result",
    "make_model",
    "build",
]


def _is_cq_model(obj, cq):
    return isinstance(obj, (cq.Assembly, cq.Workplane, cq.Shape))


def extract_model(code_py_path):
    import cadquery as cq

    import types

    src = open(code_py_path).read()
    code_dir = os.path.dirname(os.path.abspath(code_py_path))
    # Register a real module in sys.modules so @dataclass (with string
    # annotations from `from __future__ import annotations`) can resolve
    # cls.__module__ during KW_ONLY detection. name != "__main__" so any
    # bottom `if __name__ == "__main__"` block won't run.
    modname = "verify_candidate"
    mod = types.ModuleType(modname)
    mod.__file__ = os.path.abspath(code_py_path)
    mod.__dict__["__builtins__"] = __builtins__
    sys.modules[modname] = mod
    sys.path.insert(0, code_dir)
    try:
        code = compile(src, code_py_path, "exec")
        exec(code, mod.__dict__)
    finally:
        sys.modules.pop(modname, None)
        if code_dir in sys.path:
            sys.path.remove(code_dir)
    ns = mod.__dict__

    # 1 & 2: preferred named variables
    for key in ("result", "object_model"):
        if key in ns and _is_cq_model(ns[key], cq):
            return ns[key]
    # 3: last module-level cq model object
    last = None
    for v in ns.values():
        if _is_cq_model(v, cq):
            last = v
    if last is not None:
        return last
    # 4: fallback -- call a known zero-arg builder function
    for fname in BUILDER_NAMES:
        fn = ns.get(fname)
        if callable(fn):
            try:
                obj = fn()
            except Exception:
                continue
            if _is_cq_model(obj, cq):
                return obj
    raise RuntimeError(
        "no model object found (expected module-level `result`, `object_model`, "
        "a module-level cq.Assembly/Workplane/Shape, or a known builder function)"
    )


def model_to_trimesh(obj):
    import cadquery as cq
    import trimesh

    if isinstance(obj, cq.Assembly):
        shape = obj.toCompound()
    elif isinstance(obj, cq.Workplane):
        vals = [v for v in obj.vals() if isinstance(v, cq.Shape)]
        if not vals:
            raise RuntimeError("Workplane has no solid/shape values")
        shape = cq.Compound.makeCompound(vals) if len(vals) > 1 else vals[0]
    elif isinstance(obj, cq.Shape):
        shape = obj
    else:
        raise RuntimeError("unsupported model type %r" % type(obj))

    fd, tmp = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        cq.exporters.export(shape, tmp, tolerance=TESS_TOL, angularTolerance=TESS_ANG)
        mesh = _coerce_trimesh(trimesh.load(tmp, process=False))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if mesh.vertices.shape[0] == 0:
        raise RuntimeError("tessellation produced empty mesh")
    return mesh


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _normalize(mesh):
    """Center to bbox origin, scale so max bbox dim = 1. Returns (mesh, extents)."""
    m = mesh.copy()
    center = m.bounds.mean(axis=0)
    m.apply_translation(-center)
    ext = m.extents
    scale = ext.max()
    if scale <= 0:
        raise RuntimeError("degenerate mesh (zero extent)")
    m.apply_scale(1.0 / scale)
    return m, ext


def compute_metrics(gt, mine, n=N_SAMPLES, seed=0):
    from scipy.spatial import cKDTree

    rng = np.random.RandomState(seed)

    gt_n, gt_ext = _normalize(gt)
    mn_n, mn_ext = _normalize(mine)

    bbox_ratio = (mn_ext / gt_ext).tolist()

    try:
        vg = gt.convex_hull.volume
        vm = mine.convex_hull.volume
        volume_ratio = float(vm / vg) if vg > 0 else None
    except Exception:
        volume_ratio = None

    pg = trimesh.sample.sample_surface(gt_n, n, seed=seed)[0]
    pm = trimesh.sample.sample_surface(mn_n, n, seed=seed)[0]

    tg = cKDTree(pg)
    tm = cKDTree(pm)
    d_m2g, _ = tg.query(pm)
    d_g2m, _ = tm.query(pg)
    chamfer = float((d_m2g.mean() + d_g2m.mean()) / 2.0)

    return {
        "chamfer": chamfer,
        "bbox_ratio": [float(r) for r in bbox_ratio],
        "volume_ratio": volume_ratio,
        "_norm": (gt_n, mn_n, pg, pm),
    }


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def render_compare(pg, pm, out_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(16, 8))
    views = [(20, -60), (90, -90)]
    cols = [("Ground truth (URDF)", pg, "tab:blue"), ("Candidate (CadQuery)", pm, "tab:orange")]
    for ci, (title, pts, color) in enumerate(cols):
        for ri, (el, az) in enumerate(views):
            ax = fig.add_subplot(2, 2, ri * 2 + ci + 1, projection="3d")
            k = min(15000, len(pts))
            idx = np.random.RandomState(0).choice(len(pts), k, replace=False)
            ax.scatter(pts[idx, 0], pts[idx, 1], pts[idx, 2], s=0.4, c=color)
            ax.view_init(elev=el, azim=az)
            ax.set_box_aspect((1, 1, 1))
            ax.set_xlim(-0.55, 0.55)
            ax.set_ylim(-0.55, 0.55)
            ax.set_zlim(-0.55, 0.55)
            ax.set_title("%s  view(%d,%d)" % (title, el, az), fontsize=9)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_zticks([])
    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample_dir")
    ap.add_argument("code_py_path")
    ap.add_argument("--render", default=None)
    ap.add_argument("--glb", default=None, help="also export the exec'd model to this GLB (reuses the exec)")
    args = ap.parse_args()

    out = {"chamfer": None, "bbox_ratio": None, "volume_ratio": None, "pass": False, "error": None}

    try:
        global trimesh
        import trimesh  # noqa: F401

        with open(os.path.join(args.sample_dir, "meta.json")) as f:
            meta = json.load(f)
        hf_name = meta["hf_name"]

        gt = get_gt_mesh(hf_name)
        model = extract_model(args.code_py_path)

        if args.glb:                       # reuse the exec'd model: export GLB for rendering
            try:
                import cadquery as cq
                import warnings as _w
                _w.filterwarnings("ignore")
                os.makedirs(os.path.dirname(args.glb), exist_ok=True)
                if isinstance(model, cq.Assembly):
                    model.save(args.glb)   # keeps per-part material colors
                else:
                    shp = model.toCompound() if isinstance(model, cq.Workplane) else model
                    cq.exporters.export(shp, args.glb)
            except Exception as e:
                out["glb_error"] = str(e)[:80]

        mine = model_to_trimesh(model)

        res = compute_metrics(gt, mine)
        gt_n, mn_n, pg, pm = res.pop("_norm")
        out["chamfer"] = res["chamfer"]
        out["bbox_ratio"] = res["bbox_ratio"]
        out["volume_ratio"] = res["volume_ratio"]

        if args.render:
            try:
                render_compare(pg, pm, args.render)
            except Exception as e:  # rendering must never fail the run
                out["error"] = "render_failed: %s" % e

        ok_bbox = all(BBOX_LO <= r <= BBOX_HI for r in out["bbox_ratio"])
        out["pass"] = (out["error"] is None) and (out["chamfer"] < CHAMFER_PASS) and ok_bbox
    except Exception as e:
        import traceback

        out["error"] = "%s: %s" % (type(e).__name__, e)
        out["pass"] = False
        sys.stderr.write(traceback.format_exc())

    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
