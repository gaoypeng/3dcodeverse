"""Isolated per-geometry verification: compare a REAL Articraft sdk geometry
(triangle mesh) against a candidate CadQuery shape via normalized chamfer.

Usage inside a family agent:

    import sys; sys.path.insert(0, os.path.dirname(__file__))  # this folder
    import sys; sys.path.insert(0, os.environ["ARTICRAFT_FORK"])  # the real sdk (Apache-2.0 fork)
    import sdk                      # the REAL sdk (ground truth meshes)
    import geom_chamfer as gc
    import mymodule                 # your cadquery shim family

    real = sdk.TorusGeometry(0.1, 0.02)
    mine = mymodule.TorusGeometry(0.1, 0.02)
    print(gc.chamfer(real, mine))   # {'chamfer':..., 'bbox_ratio':[...]}  pass if chamfer<0.02 & bbox in [0.9,1.1]
"""
import os, sys, tempfile
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))


def _tm_from_vf(verts, faces):
    import trimesh
    return trimesh.Trimesh(vertices=np.asarray(verts, float),
                           faces=np.asarray(faces, int), process=False)


def _tm_from_sdk(geom):
    """Real sdk geometry -> trimesh. sdk MeshGeometry exposes .vertices/.faces."""
    import trimesh
    if hasattr(geom, "vertices") and hasattr(geom, "faces") and len(getattr(geom, "faces")):
        return _tm_from_vf(geom.vertices, geom.faces)
    # some sdk helpers return a Mesh referencing an obj file
    raise RuntimeError("object is not a triangle-mesh sdk geometry: %r" % type(geom))


def _tm_from_cq(shape):
    import cadquery as cq
    import trimesh
    if hasattr(shape, "_cq"):
        shape = shape._cq()
    if isinstance(shape, cq.Assembly):
        shape = shape.toCompound()
    if isinstance(shape, cq.Workplane):
        vals = [v for v in shape.vals() if isinstance(v, cq.Shape)]
        shape = vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    fd, tmp = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        cq.exporters.export(shape, tmp, tolerance=1e-4, angularTolerance=0.1)
        m = trimesh.load(tmp, process=False)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return m


def _norm(m):
    m = m.copy()
    m.apply_translation(-m.bounds.mean(axis=0))
    e = m.extents
    s = e.max()
    if s <= 0:
        raise RuntimeError("degenerate mesh")
    m.apply_scale(1.0 / s)
    return m, e


def chamfer(real_geom, mine, n=20000):
    """Return {'chamfer','bbox_ratio','pass'} comparing a real sdk geometry to a
    candidate cadquery geometry/shape (both normalized to unit bbox)."""
    import trimesh
    from scipy.spatial import cKDTree
    gt = real_geom if hasattr(real_geom, "extents") else _tm_from_sdk(real_geom)
    mn = _tm_from_cq(mine)
    gtn, ge = _norm(gt)
    mnn, me = _norm(mn)
    pg = trimesh.sample.sample_surface(gtn, n, seed=0)[0]
    pm = trimesh.sample.sample_surface(mnn, n, seed=0)[0]
    d1, _ = cKDTree(pg).query(pm)
    d2, _ = cKDTree(pm).query(pg)
    ch = float((d1.mean() + d2.mean()) / 2.0)
    br = [float(x) for x in (me / ge)]
    return {"chamfer": round(ch, 5), "bbox_ratio": [round(x, 4) for x in br],
            "pass": ch < 0.02 and all(0.9 <= r <= 1.1 for r in br)}
