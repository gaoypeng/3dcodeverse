# Vendored verbatim from the official 3DCodeBench repo (metrics/structural_integrity.py, github.com/gaoypeng/3dcodebench, 2026-09)
# so the numbers match the paper. Used through score_glb(); the CLI below still works standalone.
#!/usr/bin/env python3
"""Structural-integrity metric — is the mesh one coherent object, or a pile of parts?

Motivation
----------
Perceptual scores (SigLIP-2 / DINOv3 image similarity, Uni3D, even Chamfer on
sampled surface points) are computed on *appearance* or on *surface samples*.
None of them can tell the difference between

    (a) a chair whose four legs are welded to the seat, and
    (b) a chair whose four legs float 3 cm below the seat,

because both render nearly identically from a turntable and both produce almost
the same point cloud. This scorer measures the properties those metrics are
blind to: connectivity, fragmentation, manifoldness, and static plausibility.

Inputs (same layout as shape_chamfer.py)
----------------------------------------
    reference: <reference-dir>/<inst>/glb/<inst>.glb
    generated: <results-dir>/<inst>/glb/<inst>.glb

Run with only ``--reference-dir`` (no ``--results-dir``) to score the reference
meshes themselves and establish the GT baseline. This is not optional bookkeeping:
the Infinigen procedural factories emit meshes that are frequently non-watertight
and almost never single-component (a fern legitimately has one component per
leaflet), so several of these numbers are only interpretable as a *ratio to the
reference*, never as absolute quality.

Pipeline (per mesh)
-------------------
 1. ``trimesh.load(force="mesh")`` — concatenates all scene primitives into one
    Trimesh, exactly like shape_chamfer.py.
 2. Normalize to a unit bounding sphere: subtract the area-weighted surface
    centroid, divide by max ||v||. Same normalization target as
    shape_chamfer.py (which uses the mean of its surface samples; the
    area-weighted centroid is the deterministic version of that estimator).
    Doing this *before* welding also makes the weld tolerance scale-free.
 3. Weld vertices by position with ``merge_vertices(merge_tex=True,
    merge_norm=True)``. This is load-bearing: glTF splits vertices at UV seams
    and normal discontinuities, so an unwelded Infinigen tree reports 2.26M
    "components" for 3.95M faces (i.e. raw triangle soup). Ignoring UV/normal
    splits is the only way to get a topologically meaningful component count.
 4. Connected components on the **vertex** graph (faces that share a vertex are
    in the same component), not on ``mesh.face_adjacency``. trimesh's
    face_adjacency silently drops any edge incident to 3+ faces, so on a
    non-manifold mesh it *over*-reports components — welding a mesh can make its
    face_adjacency component count go **up**, which is nonsense for our purpose.
 5. Everything else is vectorized per-component via ``np.bincount`` on the face
    labels; no per-component submesh is ever materialized (some GT meshes have
    350k+ components).

Metrics
-------
n_components                        connected-component count (vertex graph)
component_ratio                     n_components / n_components_ref
log2_component_ratio                signed log2 of the above (0 = matches ref)
abs_log2_component_ratio            |log2 ratio| — the aggregation-friendly form
largest_component_volume_fraction   |vol(largest)| / sum_c |vol(c)|.  KEY
                                    fragmentation measure. Each component's
                                    volume is the divergence-theorem integral
                                    taken about *that component's own* area
                                    centroid, so an open planar sheet integrates
                                    to ~0 instead of to an origin-dependent
                                    garbage value.
largest_component_area_fraction     same but surface area. Always well defined,
                                    including for meshes that are pure sheets.
largest_component_face_fraction     same but face count (tessellation-dependent,
                                    diagnostic only).
floating_part_rate                  fraction of components not within eps of any
                                    other component.
floating_area_fraction              surface area of those components / total.
floating_volume_fraction            volume of those components / total.
nonmanifold_edge_rate               edges incident to >=3 faces / unique edges.
boundary_edge_rate                  edges incident to exactly 1 face / unique
                                    edges (open boundary, NOT non-manifold).
is_watertight, euler_number         trimesh topology summary.
ground_contact                      is there a real base? True iff the footprint
                                    (points within eps of the min-up plane) has
                                    >=3 points and a 2D convex-hull area of at
                                    least --ground-min-area-frac of the
                                    horizontal bbox area. This is a *degeneracy*
                                    filter, not a flatness test: a sphere passes
                                    (its eps-band cap has radius ~sqrt(2 R eps),
                                    ~5% of the bbox area at eps_frac=1%), a cone
                                    balanced on its apex fails (radius grows
                                    only linearly in eps, ~2e-4 of bbox area),
                                    and an object suspended above its own lowest
                                    stray vertex fails. Expect it to be True for
                                    almost everything; read
                                    ground_contact_area_frac instead if you want
                                    a graded signal.
ground_contact_area_frac            the raw continuous version of the above.
com_over_support                    standard static-stability test: is the
                                    horizontal projection of the centre of mass
                                    inside the convex hull of the footprint?
com_support_margin                  signed distance from the COM to the support
                                    polygon boundary / horizontal bbox diagonal.
                                    Positive = inside = stable. Continuous, so
                                    it degrades gracefully; prefer it to the bool.

Choice of epsilon
-----------------
eps = --eps-frac * (bounding-box diagonal of the normalized mesh), default
--eps-frac = 0.01, i.e. **1% of the object's bbox diagonal**. Rationale: this is
roughly the width of a visible seam at typical render resolution (a 512px render
of an object filling the frame puts 1% of the diagonal at ~5px), and it is an
order of magnitude above the float32 quantization of a glTF vertex buffer
(~1e-7 relative) so it cannot be tripped by export noise. It is also small
enough that a genuinely detached chair leg — the failure mode the reviewer asked
about — sits well outside it. Sweep it with --eps-frac if you want a sensitivity
curve; 0.005 and 0.02 are the natural bracketing values.

Contact test
------------
Contact between components is decided on an axis-aligned grid of cell size eps:
two components are "in contact" iff they have surface points in the same or in
one of the 26 adjacent cells. Any two points at distance <= eps necessarily fall
in the same or an adjacent cell, so **no true contact at <= eps is ever missed**;
the test can additionally accept pairs up to sqrt(3)*eps ~ 1.73 eps apart. The
bias is therefore toward calling things connected, i.e. toward *under*-reporting
floating parts, which is the conservative direction for a claim about model
failure. The alternative (an exact all-pairs KD-tree) is O(C^2) tree builds and
is not tractable at 350k components.

The point set used for contact / footprint / COM is the welded vertices plus
enough area-proportional surface samples to reach ~1 point per eps^2 of surface
(capped). Vertices alone are not sufficient: an LLM-generated mesh may be a
12-triangle box whose vertices are metres apart, and a sphere resting on the
middle of that box's top face would be scored as floating.

Up axis
-------
The spec talks about the "z-min plane". These GLBs are Blender exports and the
glTF exporter converts Z-up to **Y-up**; trimesh does not convert back
(verified: TableDining_seed0 has extent [2.32, 0.70, 1.17], the 0.70 being the
table height on Y). ``--up-axis`` therefore defaults to ``y``. Everything below
says "up" where the spec says "z".

Aggregation (matches shape_chamfer.py / image_similarity.py)
------------------------------------------------------------
    conditional — averaged over instances whose mesh loaded and had faces.
                  "Given that a mesh came out at all, how sound was it?"
    penalized   — instances with no/empty/broken mesh are folded in at the
                  worst possible value for each field (floating_part_rate -> 1,
                  largest_component_volume_fraction -> 0, every boolean -> False,
                  com_support_margin -> the worst observed in the run).
                  "Overall structural soundness, including hard failures."
Booleans aggregate as rates; scalars report mean / median / std / p10 / p90.

Output
------
    per-instance JSONL  ->  --out            (default <out-dir>/structural_integrity.jsonl)
    aggregate JSON      ->  same stem + .json

Usage
-----
    # GT baseline (no model involved)
    python metrics/structural_integrity.py \
        --reference-dir data/data --workers 6 \
        --out notes/e5_structural/reference_baseline.jsonl

    # A model run, with ratios against the reference
    python metrics/structural_integrity.py \
        --results-dir results/text_to_3D/gemini-3-flash-preview \
        --reference-dir data/data --workers 8

    # Sanity checks on hand-built meshes
    python metrics/structural_integrity.py --selftest
"""

import argparse
import json
import math
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components as _csgraph_cc
from scipy.spatial import ConvexHull

EVAL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = EVAL_ROOT / "data"

UP_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}

# Fields that are booleans in the per-instance record and aggregate as rates.
BOOL_FIELDS = ["is_watertight", "is_single_component", "ground_contact",
               "com_over_support", "winding_consistent"]

# Scalar fields -> value used when the instance produced no usable mesh.
# None means "use the worst value observed in this run".
PENALTY = {
    "n_components": None,
    "largest_component_volume_fraction": 0.0,
    "largest_component_area_fraction": 0.0,
    "largest_component_face_fraction": 0.0,
    "floating_part_rate": 1.0,
    "floating_area_fraction": 1.0,
    "floating_volume_fraction": 1.0,
    "nonmanifold_edge_rate": 1.0,
    "boundary_edge_rate": 1.0,
    "ground_contact_area_frac": 0.0,
    "com_support_margin": None,
    "abs_log2_component_ratio": None,
}
SCALAR_FIELDS = list(PENALTY.keys())

# Direction of "worse" for fields whose penalty is run-relative.
WORSE_IS_MAX = {"n_components": True, "com_support_margin": False,
                "abs_log2_component_ratio": True}


# --------------------------------------------------------------------------- #
# args
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results-dir", type=Path, default=None,
                   help="Model results tree: <results-dir>/<inst>/glb/<inst>.glb. "
                        "Omit to score the reference meshes themselves.")
    p.add_argument("--reference-dir", type=Path, default=DEFAULT_DATA_ROOT,
                   help="Reference tree: <reference-dir>/<inst>/glb/<inst>.glb")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--out", type=Path, default=None,
                   help="Per-instance JSONL path. The aggregate summary is "
                        "written next to it with a .json suffix.")
    p.add_argument("--eps-frac", type=float, default=0.01,
                   help="Contact/footprint tolerance as a fraction of the "
                        "normalized bbox diagonal (default 0.01 = 1%%).")
    p.add_argument("--ground-min-area-frac", type=float, default=1e-3,
                   help="Footprint convex-hull area, as a fraction of the "
                        "horizontal bbox area, below which the base counts as "
                        "degenerate (default 1e-3).")
    p.add_argument("--up-axis", choices=["x", "y", "z"], default="y",
                   help="Vertical axis. Blender->glTF exports are Y-up "
                        "(default y).")
    p.add_argument("--max-contact-samples", type=int, default=100_000,
                   help="Cap on extra surface samples added to the contact "
                        "point set (on top of every welded vertex).")
    p.add_argument("--instances", nargs="*", default=None)
    p.add_argument("--limit", type=int, default=None,
                   help="Cap number of instances (debug).")
    p.add_argument("--selftest", action="store_true",
                   help="Run the hand-built sanity meshes and exit.")
    return p.parse_args(argv)


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _hull_2d(points_2d):
    """2D convex hull; returns (hull, area) or (None, 0.0) if degenerate."""
    if len(points_2d) < 3:
        return None, 0.0
    try:
        h = ConvexHull(np.ascontiguousarray(points_2d, dtype=np.float64))
    except Exception:
        return None, 0.0
    # For a 2D hull scipy reports `volume` = enclosed area, `area` = perimeter.
    return h, float(h.volume)


def normalize_unit_sphere(mesh):
    """Center on the area-weighted surface centroid, scale to max ||v|| = 1.

    Mesh analogue of shape_chamfer.py's point-cloud normalization. Returns the
    scale factor that was applied (useful only for logging).
    """
    af = mesh.area_faces
    tot = float(af.sum())
    if tot > 0:
        center = (mesh.triangles_center * af[:, None]).sum(0) / tot
    else:
        center = np.asarray(mesh.vertices).mean(0)
    mesh.apply_translation(-center)
    r = float(np.linalg.norm(np.asarray(mesh.vertices), axis=1).max())
    if r > 1e-12:
        mesh.apply_scale(1.0 / r)
    return r


def vertex_graph_components(mesh):
    """Connected components where two faces are connected iff they share a vertex.

    Returns (face_label int32[F], n_components).

    Deliberately NOT mesh.face_adjacency: trimesh builds face_adjacency only
    from edges incident to exactly two faces, so a T-junction or any 3-faces-
    to-an-edge configuration severs the graph. On Bird_seed0 that inflates the
    count from 8329 real components to whatever the manifold patches happen to
    be. The vertex graph is the definition that matches "is this one object".
    """
    faces = np.asarray(mesh.faces)
    n_v = len(mesh.vertices)
    if len(faces) == 0:
        return np.zeros(0, dtype=np.int32), 0
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    data = np.ones(len(e), dtype=np.int8)
    g = coo_matrix((data, (e[:, 0], e[:, 1])), shape=(n_v, n_v))
    _, vlab = _csgraph_cc(g, directed=False, return_labels=True)
    face_label = vlab[faces[:, 0]]
    # Drop labels belonging to isolated (unreferenced) vertices and compact.
    uniq, face_label = np.unique(face_label, return_inverse=True)
    return face_label.astype(np.int32), int(len(uniq))


def per_component_area_volume(mesh, face_label, n_comp):
    """Vectorized per-component surface area and (origin-free) volume.

    Volume uses the divergence theorem with each component's own area centroid
    as the origin:  V_c = sum_{f in c} (a-o).((b-o) x (c-o)) / 6.
    For a closed component this equals the enclosed volume (origin-independent).
    For an open planar sheet every tetrahedron is degenerate and the sum is ~0,
    which is exactly the behaviour we want -- otherwise a leaf far from the
    world origin would be credited with a huge phantom volume and would
    dominate largest_component_volume_fraction.
    """
    af = np.asarray(mesh.area_faces, dtype=np.float64)
    area = np.bincount(face_label, weights=af, minlength=n_comp)

    # Area centroid per component.
    tc = np.asarray(mesh.triangles_center, dtype=np.float64)
    cen = np.stack([np.bincount(face_label, weights=tc[:, k] * af,
                                minlength=n_comp) for k in range(3)], axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cen = np.where(area[:, None] > 0, cen / np.maximum(area, 1e-300)[:, None], 0.0)

    tri = np.asarray(mesh.triangles, dtype=np.float64)
    o = cen[face_label]
    a = tri[:, 0] - o
    b = tri[:, 1] - o
    c = tri[:, 2] - o
    vf = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
    vol = np.abs(np.bincount(face_label, weights=vf, minlength=n_comp))
    return area, vol


def contact_point_set(mesh, face_label, eps, max_samples, rng):
    """Welded vertices + area-proportional surface samples, each with a label.

    Vertices alone under-sample coarse geometry (a 12-triangle box), which would
    make a sphere sitting on that box read as floating. We top up to roughly one
    point per eps^2 of surface area, capped.
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    v_lab = np.zeros(len(verts), dtype=np.int32)
    # Label every referenced vertex by (any) incident face's component.
    v_lab[faces.reshape(-1)] = np.repeat(face_label, 3)

    pts = [verts]
    labs = [v_lab]

    total_area = float(mesh.area)
    want = int(total_area / max(eps * eps, 1e-12)) - len(verts)
    n_extra = int(np.clip(want, 0, max_samples))
    if n_extra > 0:
        try:
            s_pts, s_fidx = trimesh.sample.sample_surface(
                mesh, n_extra, seed=int(rng.integers(0, 2 ** 31 - 1)))
            pts.append(np.asarray(s_pts, dtype=np.float64))
            labs.append(face_label[np.asarray(s_fidx)])
        except Exception:
            pass
    return np.concatenate(pts), np.concatenate(labs).astype(np.int64)


def _cell_keys(points, eps):
    """Pack floor(p/eps) into a single int64 key per point."""
    ijk = np.floor(points / eps).astype(np.int64)
    ijk -= ijk.min(axis=0)                       # shift to non-negative
    m = ijk.max(axis=0) + 3                      # +3 leaves room for +/-1 probes
    base_y = int(m[2])
    base_x = int(m[1]) * base_y
    return (ijk[:, 0] + 1) * base_x + (ijk[:, 1] + 1) * base_y + (ijk[:, 2] + 1), \
           (base_x, base_y)


def components_in_contact(points, labels, n_comp, eps):
    """Boolean[n_comp]: does this component have another component within eps?

    Grid method: two points within eps always land in the same or a 26-adjacent
    cell of an eps-sized grid, so this never misses a true contact; it can
    over-accept out to sqrt(3)*eps. See module docstring.
    """
    touching = np.zeros(n_comp, dtype=bool)
    if n_comp <= 1 or len(points) == 0:
        return touching

    keys, (bx, by) = _cell_keys(points, eps)

    # Unique (cell, component) pairs, then per-cell summary.
    pair = np.stack([keys, labels], axis=1)
    pair = np.unique(pair, axis=0)               # sorted by cell then component
    p_cell, p_lab = pair[:, 0], pair[:, 1]

    cells, first_idx, counts = np.unique(p_cell, return_index=True,
                                         return_counts=True)
    cell_first_lab = p_lab[first_idx]            # only meaningful when count==1

    offsets = [dx * bx + dy * by + dz
               for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]

    hit = np.zeros(len(p_cell), dtype=bool)
    for off in offsets:
        probe = p_cell + off
        idx = np.searchsorted(cells, probe)
        np.clip(idx, 0, len(cells) - 1, out=idx)
        ok = cells[idx] == probe
        # Neighbour cell holds a *different* component if it holds >1 component,
        # or holds exactly one that isn't ours.
        diff = (counts[idx] > 1) | (cell_first_lab[idx] != p_lab)
        hit |= ok & diff

    np.logical_or.at(touching, p_lab, hit)
    return touching


def support_metrics(points, mesh, up, eps, ground_min_area_frac, is_watertight):
    """Ground contact + static stability.

    Footprint = points within eps of the minimum-up plane, projected to the two
    horizontal axes. Support polygon = convex hull of that footprint.
    """
    horiz = [i for i in range(3) if i != up]
    lo = points.min(axis=0)
    hi = points.max(axis=0)
    ext = hi - lo
    bbox_h_area = float(ext[horiz[0]] * ext[horiz[1]])
    bbox_h_diag = float(math.hypot(ext[horiz[0]], ext[horiz[1]]))

    mask = points[:, up] <= (lo[up] + eps)
    foot = points[mask][:, horiz]
    n_foot = int(mask.sum())

    hull, hull_area = _hull_2d(foot)
    area_frac = hull_area / bbox_h_area if bbox_h_area > 1e-12 else 0.0
    ground_contact = bool(n_foot >= 3 and area_frac >= ground_min_area_frac)

    # Centre of mass. mesh.center_mass is the volume integral and is meaningless
    # for an open mesh, so fall back to the area-weighted surface centroid.
    com_mode = "volume"
    com = None
    if is_watertight:
        try:
            cm = np.asarray(mesh.center_mass, dtype=np.float64)
            if np.all(np.isfinite(cm)) and abs(mesh.volume) > 1e-12:
                com = cm
        except Exception:
            com = None
    if com is None:
        com_mode = "area"
        af = np.asarray(mesh.area_faces, dtype=np.float64)
        tot = float(af.sum())
        if tot > 0:
            com = (np.asarray(mesh.triangles_center) * af[:, None]).sum(0) / tot
        else:
            com = np.asarray(mesh.vertices, dtype=np.float64).mean(0)

    com_h = np.asarray(com, dtype=np.float64)[horiz]
    if hull is None:
        com_over = False
        margin = None
    else:
        # ConvexHull.equations: outward normals, inside <=> all n.x + d <= 0.
        vals = hull.equations[:, :2] @ com_h + hull.equations[:, 2]
        signed = -float(vals.max())              # >0 inside
        com_over = bool(signed >= 0.0)
        margin = signed / bbox_h_diag if bbox_h_diag > 1e-12 else 0.0

    return {
        "ground_contact": ground_contact,
        "ground_contact_area_frac": float(area_frac),
        "n_footprint_points": n_foot,
        "footprint_point_frac": float(n_foot / max(len(points), 1)),
        "com_over_support": com_over,
        "com_support_margin": margin,
        "com_mode": com_mode,
        "bbox_extent": [float(x) for x in ext],
    }


# --------------------------------------------------------------------------- #
# per-mesh scoring
# --------------------------------------------------------------------------- #
def score_mesh_object(mesh, eps_frac, up, ground_min_area_frac,
                      max_contact_samples, seed=0):
    """Score an already-loaded Trimesh. Normalizes and welds in place."""
    rng = np.random.default_rng(seed)

    if mesh is None or len(getattr(mesh, "faces", [])) == 0:
        return None

    normalize_unit_sphere(mesh)
    # Weld by position, ignoring UV / normal splits introduced by glTF.
    try:
        mesh.merge_vertices(merge_tex=True, merge_norm=True)
    except Exception:
        pass
    if len(mesh.faces) == 0:
        return None

    face_label, n_comp = vertex_graph_components(mesh)
    if n_comp == 0:
        return None

    ext = mesh.extents
    bbox_diag = float(np.linalg.norm(ext))
    eps = eps_frac * bbox_diag
    if not np.isfinite(eps) or eps <= 0:
        eps = eps_frac * 2.0

    area_c, vol_c = per_component_area_volume(mesh, face_label, n_comp)
    face_c = np.bincount(face_label, minlength=n_comp).astype(np.float64)
    tot_area, tot_vol, tot_face = area_c.sum(), vol_c.sum(), face_c.sum()

    def frac(arr, tot):
        return float(arr.max() / tot) if tot > 1e-15 else None

    pts, plab = contact_point_set(mesh, face_label, eps, max_contact_samples, rng)
    touching = components_in_contact(pts, plab, n_comp, eps)
    if n_comp <= 1:
        floating = np.zeros(n_comp, dtype=bool)
    else:
        floating = ~touching
    n_float = int(floating.sum())

    # Edge manifoldness.
    inv = np.asarray(mesh.edges_unique_inverse)
    n_unique_edges = int(len(mesh.edges_unique))
    if n_unique_edges > 0:
        per_edge = np.bincount(inv, minlength=n_unique_edges)
        nonmanifold = int((per_edge >= 3).sum())
        boundary = int((per_edge == 1).sum())
    else:
        nonmanifold = boundary = 0

    try:
        watertight = bool(mesh.is_watertight)
    except Exception:
        watertight = False
    try:
        winding = bool(mesh.is_winding_consistent)
    except Exception:
        winding = False
    try:
        euler = int(mesh.euler_number)
    except Exception:
        euler = None

    rec = {
        "n_vertices": int(len(mesh.vertices)),
        "n_faces": int(len(mesh.faces)),
        "n_edges_unique": n_unique_edges,
        "n_components": int(n_comp),
        "is_single_component": bool(n_comp == 1),
        "largest_component_volume_fraction": frac(vol_c, tot_vol),
        "largest_component_area_fraction": frac(area_c, tot_area),
        "largest_component_face_fraction": frac(face_c, tot_face),
        "n_floating_components": n_float,
        "floating_part_rate": float(n_float / n_comp),
        "floating_area_fraction": (float(area_c[floating].sum() / tot_area)
                                   if tot_area > 1e-15 else 0.0),
        "floating_volume_fraction": (float(vol_c[floating].sum() / tot_vol)
                                     if tot_vol > 1e-15 else 0.0),
        "n_nonmanifold_edges": nonmanifold,
        "nonmanifold_edge_rate": float(nonmanifold / max(n_unique_edges, 1)),
        "n_boundary_edges": boundary,
        "boundary_edge_rate": float(boundary / max(n_unique_edges, 1)),
        "is_watertight": watertight,
        "winding_consistent": winding,
        "euler_number": euler,
        "surface_area": float(tot_area),
        "abs_volume_sum": float(tot_vol),
        "bbox_diag": bbox_diag,
        "eps": float(eps),
        "n_contact_points": int(len(pts)),
    }
    rec.update(support_metrics(pts, mesh, up, eps, ground_min_area_frac,
                               watertight))
    return rec


def score_glb(glb_path, eps_frac, up, ground_min_area_frac,
              max_contact_samples, seed=0):
    """Load, score. Returns (record_or_None, status)."""
    try:
        mesh = trimesh.load(glb_path, force="mesh")
    except Exception:
        return None, "LOAD_FAIL"
    if mesh is None or not hasattr(mesh, "faces") or len(mesh.faces) == 0:
        return None, "EMPTY_MESH"
    try:
        rec = score_mesh_object(mesh, eps_frac, up, ground_min_area_frac,
                                max_contact_samples, seed=seed)
    except Exception:
        return None, "SCORE_FAIL:" + traceback.format_exc(limit=2).strip().splitlines()[-1][:160]
    if rec is None:
        return None, "EMPTY_MESH"
    return rec, "OK"


def _worker(job):
    (inst, ref_glb, gen_glb, eps_frac, up, gmaf, mcs) = job
    t0 = time.time()
    out = {"instance": inst}
    ref_rec = gen_rec = None

    if ref_glb is not None:
        ref_glb = Path(ref_glb)
        if ref_glb.exists():
            ref_rec, st = score_glb(str(ref_glb), eps_frac, up, gmaf, mcs)
            out["ref_status"] = st
        else:
            out["ref_status"] = "NO_REF_GLB"
    if gen_glb is not None:
        gen_glb = Path(gen_glb)
        if gen_glb.exists():
            gen_rec, st = score_glb(str(gen_glb), eps_frac, up, gmaf, mcs)
            out["gen_status"] = st
        else:
            out["gen_status"] = "NO_GEN_GLB"

    if gen_glb is None:
        # reference-only mode: the reference IS the subject
        out["status"] = out.get("ref_status", "NO_REF_GLB")
        if ref_rec:
            out.update(ref_rec)
    else:
        out["status"] = out.get("gen_status", "NO_GEN_GLB")
        if gen_rec:
            out.update(gen_rec)
        if ref_rec:
            out["ref_n_components"] = ref_rec["n_components"]
            out["ref_is_watertight"] = ref_rec["is_watertight"]
            out["ref_largest_component_volume_fraction"] = \
                ref_rec["largest_component_volume_fraction"]
            out["ref_floating_part_rate"] = ref_rec["floating_part_rate"]
            if gen_rec:
                r = gen_rec["n_components"] / max(ref_rec["n_components"], 1)
                out["component_ratio"] = float(r)
                out["log2_component_ratio"] = float(np.log2(max(r, 1e-9)))
                out["abs_log2_component_ratio"] = abs(out["log2_component_ratio"])

    out["seconds"] = round(time.time() - t0, 2)
    return out


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def _stats(vals):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)],
                   dtype=np.float64)
    if len(v) == 0:
        return None
    return {"n": int(len(v)), "mean": float(v.mean()), "median": float(np.median(v)),
            "std": float(v.std()), "p10": float(np.percentile(v, 10)),
            "p90": float(np.percentile(v, 90)), "min": float(v.min()),
            "max": float(v.max())}


def aggregate(rows):
    """conditional (OK rows only) vs penalized (failures at worst value)."""
    ok = [r for r in rows if r.get("status") == "OK"]
    n, n_ok = len(rows), len(ok)

    penalties = {}
    for f in SCALAR_FIELDS:
        p = PENALTY[f]
        if p is None:
            vals = [r.get(f) for r in ok if r.get(f) is not None]
            vals = [v for v in vals if np.isfinite(v)]
            if vals:
                p = max(vals) if WORSE_IS_MAX.get(f, True) else min(vals)
            else:
                p = None
        penalties[f] = p

    out = {"n_instances": n, "n_ok": n_ok,
           "ok_rate": (n_ok / n) if n else None,
           "penalties": penalties, "scalars": {}, "booleans": {}}

    for f in SCALAR_FIELDS:
        cond = _stats([r.get(f) for r in ok])
        p = penalties[f]
        if p is None:
            pen = None
        else:
            pen = _stats([(r.get(f) if r.get("status") == "OK"
                           and r.get(f) is not None else p) for r in rows])
        out["scalars"][f] = {"conditional": cond, "penalized": pen}

    for f in BOOL_FIELDS:
        vals = [r.get(f) for r in ok if r.get(f) is not None]
        cond = (float(np.mean(vals)) if vals else None)
        pen_vals = [bool(r.get(f)) if r.get("status") == "OK" else False
                    for r in rows]
        out["booleans"][f] = {
            "conditional_rate": cond,
            "penalized_rate": float(np.mean(pen_vals)) if pen_vals else None,
            "n_true_conditional": int(sum(1 for v in vals if v)),
            "n_conditional": len(vals),
        }

    from collections import Counter
    out["status_counts"] = dict(Counter(r.get("status") for r in rows))
    return out


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def _sphere(radius=1.0, center=(0, 0, 0), subdiv=3):
    m = trimesh.creation.icosphere(subdivisions=subdiv, radius=radius)
    m.apply_translation(np.asarray(center, dtype=np.float64))
    return m


def selftest(eps_frac=0.01, up=1, gmaf=1e-3, mcs=100_000, verbose=True):
    """Hand-built meshes with known answers. Returns (n_pass, n_fail, lines)."""
    lines, npass, nfail = [], 0, 0

    def check(name, got, want, tol=None):
        nonlocal npass, nfail
        if tol is None:
            ok = (got == want)
        else:
            ok = (got is not None) and abs(got - want) <= tol
        npass, nfail = npass + int(ok), nfail + int(not ok)
        g = f"{got:.4f}" if isinstance(got, float) else str(got)
        w = f"{want:.4f}" if isinstance(want, float) else str(want)
        lines.append(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={g} want={w}"
                     + (f" (tol {tol})" if tol else ""))
        return ok

    def score(m, tag):
        lines.append(f"\n--- {tag} ---")
        r = score_mesh_object(m.copy(), eps_frac, up, gmaf, mcs)
        lines.append(f"  n_comp={r['n_components']} "
                     f"lcvf={r['largest_component_volume_fraction']} "
                     f"lcaf={r['largest_component_area_fraction']} "
                     f"float_rate={r['floating_part_rate']} "
                     f"wt={r['is_watertight']} euler={r['euler_number']} "
                     f"nm={r['nonmanifold_edge_rate']:.4f} "
                     f"ground={r['ground_contact']} "
                     f"gcaf={r['ground_contact_area_frac']:.4f} "
                     f"com_ok={r['com_over_support']} "
                     f"margin={r['com_support_margin']}")
        return r

    # 1. Single sphere: one component, watertight, no floaters, euler 2.
    r = score(_sphere(1.0), "single sphere")
    check("n_components", r["n_components"], 1)
    check("is_single_component", r["is_single_component"], True)
    check("is_watertight", r["is_watertight"], True)
    check("euler_number", r["euler_number"], 2)
    check("largest_component_volume_fraction",
          r["largest_component_volume_fraction"], 1.0, 1e-9)
    check("largest_component_area_fraction",
          r["largest_component_area_fraction"], 1.0, 1e-9)
    check("floating_part_rate", r["floating_part_rate"], 0.0, 1e-9)
    check("nonmanifold_edge_rate", r["nonmanifold_edge_rate"], 0.0, 1e-9)
    # A sphere is tangent to its ground plane at a point, but within the eps
    # band the contact patch is a cap of radius ~sqrt(2*R*eps), i.e. ~5% of the
    # horizontal bbox area at eps_frac=1%. So it passes -- correctly, a ball
    # does rest on the floor. ground_contact is a *degeneracy* filter, not a
    # flatness test; see the cone case below for what it actually rejects.
    check("ground_contact (sphere rests on floor)", r["ground_contact"], True)

    # 1b. Cone balanced on its apex: the genuinely degenerate base. Footprint
    #     radius grows only linearly in eps, so the area fraction stays ~2e-4.
    cone = trimesh.creation.cone(radius=1.0, height=2.0, sections=64)
    cone.apply_transform(trimesh.transformations.rotation_matrix(
        np.pi / 2, [1, 0, 0]))                      # apex down along -y
    r = score(cone, "cone balanced on its apex")
    check("ground_contact (apex only)", r["ground_contact"], False)

    # 1c. Same cone, flipped to sit on its base.
    cone2 = trimesh.creation.cone(radius=1.0, height=2.0, sections=64)
    cone2.apply_transform(trimesh.transformations.rotation_matrix(
        -np.pi / 2, [1, 0, 0]))                     # base down
    r = score(cone2, "cone sitting on its base")
    check("ground_contact (flat base)", r["ground_contact"], True)
    check("com_over_support (cone on base)", r["com_over_support"], True)

    # 2. Two equal disjoint spheres, far apart: 2 components, both floating,
    #    largest volume fraction 0.5.
    a, b = _sphere(1.0, (-3, 0, 0)), _sphere(1.0, (3, 0, 0))
    r = score(trimesh.util.concatenate([a, b]), "two disjoint equal spheres")
    check("n_components", r["n_components"], 2)
    check("is_single_component", r["is_single_component"], False)
    check("largest_component_volume_fraction",
          r["largest_component_volume_fraction"], 0.5, 1e-6)
    check("largest_component_area_fraction",
          r["largest_component_area_fraction"], 0.5, 1e-6)
    check("floating_part_rate", r["floating_part_rate"], 1.0, 1e-9)
    check("floating_area_fraction", r["floating_area_fraction"], 1.0, 1e-9)
    check("is_watertight (2 closed spheres)", r["is_watertight"], True)
    check("euler_number (2 spheres)", r["euler_number"], 4)

    # 3. Unequal disjoint spheres r=1 and r=0.5: volume fraction 8/9.
    a, b = _sphere(1.0, (-3, 0, 0)), _sphere(0.5, (3, 0, 0))
    r = score(trimesh.util.concatenate([a, b]), "disjoint spheres r=1, r=0.5")
    check("n_components", r["n_components"], 2)
    check("largest_component_volume_fraction",
          r["largest_component_volume_fraction"], 8.0 / 9.0, 2e-3)
    check("largest_component_area_fraction",
          r["largest_component_area_fraction"], 4.0 / 5.0, 2e-3)

    # 4. Sphere floating above a box: the reviewer's failure mode.
    #    Box 4x1x4 sitting at y in [0,1]; sphere r=1 centred at y=2.6, so its
    #    bottom is 0.6 above the box -> ~12% of the bbox diagonal, far above eps.
    box = trimesh.creation.box(extents=(4, 1, 4))
    box.apply_translation([0, 0.5, 0])
    sph = _sphere(1.0, (0, 2.6, 0))
    r = score(trimesh.util.concatenate([box, sph]), "sphere FLOATING above box")
    check("n_components", r["n_components"], 2)
    check("floating_part_rate", r["floating_part_rate"], 1.0, 1e-9)
    check("ground_contact (box base)", r["ground_contact"], True)
    check("com_over_support", r["com_over_support"], True)

    # 5. Same box + sphere, but the sphere is embedded in the box top.
    #    Still 2 topological components, but 0 floating parts.
    sph = _sphere(1.0, (0, 1.9, 0))          # bottom at y=0.9, inside the box
    r = score(trimesh.util.concatenate([box, sph]), "sphere RESTING on box")
    check("n_components", r["n_components"], 2)
    check("floating_part_rate", r["floating_part_rate"], 0.0, 1e-9)
    check("ground_contact", r["ground_contact"], True)
    check("com_over_support", r["com_over_support"], True)

    # 6. Static instability: a tall thin column whose mass hangs off the side of
    #    a small base. COM must fall outside the support polygon.
    base = trimesh.creation.box(extents=(0.4, 0.2, 0.4))
    base.apply_translation([0, 0.1, 0])
    slab = trimesh.creation.box(extents=(3.0, 0.2, 0.4))
    slab.apply_translation([1.6, 0.3, 0])     # cantilevered far to +x
    r = score(trimesh.util.concatenate([base, slab]), "cantilever (unstable)")
    check("ground_contact", r["ground_contact"], True)
    check("com_over_support (should be False)", r["com_over_support"], False)
    check("com_support_margin < 0", bool(r["com_support_margin"] < 0), True)

    # 7. Open sheet: a single square, no volume. Volume fraction must fall back
    #    gracefully and area fraction must still be 1.0.
    plane = trimesh.Trimesh(
        vertices=np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
                          dtype=np.float64),
        faces=np.array([[0, 1, 2], [0, 2, 3]]))
    r = score(plane, "single open sheet (flat quad)")
    check("n_components", r["n_components"], 1)
    check("is_watertight", r["is_watertight"], False)
    check("largest_component_area_fraction",
          r["largest_component_area_fraction"], 1.0, 1e-9)
    check("boundary_edge_rate", r["boundary_edge_rate"], 4.0 / 5.0, 1e-9)
    check("ground_contact (flat sheet is its own base)", r["ground_contact"], True)

    # 8. Non-manifold: three squares sharing one edge (a "T" / fin).
    v = np.array([[0, 0, 0], [1, 0, 0],            # shared edge 0-1
                  [0, 1, 0], [1, 1, 0],
                  [0, -1, 0], [1, -1, 0],
                  [0, 0, 1], [1, 0, 1]], dtype=np.float64)
    f = np.array([[0, 1, 3], [0, 3, 2],
                  [0, 1, 5], [0, 5, 4],
                  [0, 1, 7], [0, 7, 6]])
    r = score(trimesh.Trimesh(vertices=v, faces=f), "three fins sharing an edge")
    check("n_components (fins are one object)", r["n_components"], 1)
    check("n_nonmanifold_edges >= 1", bool(r["n_nonmanifold_edges"] >= 1), True)
    check("is_watertight", r["is_watertight"], False)

    # 9. Fragmentation sweep: 1 big sphere + N tiny far-apart spheres. The volume
    #    fraction must stay near 1 while n_components explodes -- this is the
    #    property that makes largest_component_volume_fraction more honest than
    #    a raw component count.
    parts = [_sphere(1.0, (0, 0, 0))]
    for i in range(20):
        parts.append(_sphere(0.05, (3 + 0.3 * i, 0, 0), subdiv=1))
    r = score(trimesh.util.concatenate(parts), "1 big + 20 tiny spheres")
    check("n_components", r["n_components"], 21)
    check("largest_component_volume_fraction > 0.99",
          bool(r["largest_component_volume_fraction"] > 0.99), True)

    # 10. eps sensitivity: two spheres separated by a hair (0.2% of bbox diag)
    #     are "in contact" at eps=1%, and "floating" at eps=0.1%.
    gap = 0.01
    a = _sphere(1.0, (-1 - gap / 2, 0, 0))
    b = _sphere(1.0, (1 + gap / 2, 0, 0))
    near = trimesh.util.concatenate([a, b])
    r1 = score_mesh_object(near.copy(), 0.01, up, gmaf, mcs)
    r2 = score_mesh_object(near.copy(), 0.0005, up, gmaf, mcs)
    lines.append(f"\n--- eps sensitivity (gap={gap}) ---")
    lines.append(f"  eps_frac=0.01   -> floating_part_rate={r1['floating_part_rate']}")
    lines.append(f"  eps_frac=0.0005 -> floating_part_rate={r2['floating_part_rate']}")
    check("near-touching spheres joined at eps=1%",
          r1["floating_part_rate"], 0.0, 1e-9)
    check("near-touching spheres split at eps=0.05%",
          r2["floating_part_rate"], 1.0, 1e-9)

    lines.append(f"\n{npass} passed, {nfail} failed")
    if verbose:
        print("\n".join(lines))
    return npass, nfail, lines


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def list_instances(root, override=None):
    insts = sorted(d.name for d in root.iterdir()
                   if d.is_dir() and not d.name.startswith("_"))
    if override:
        keep = set(override)
        insts = [i for i in insts if i in keep]
    return insts


def main(argv=None):
    args = parse_args(argv)

    if args.selftest:
        npass, nfail, _ = selftest(eps_frac=args.eps_frac,
                                   up=UP_AXIS_INDEX[args.up_axis],
                                   gmaf=args.ground_min_area_frac,
                                   mcs=args.max_contact_samples)
        return 0 if nfail == 0 else 1

    ref_root = args.reference_dir
    res_root = args.results_dir
    reference_only = res_root is None

    if reference_only:
        if not ref_root or not ref_root.exists():
            raise SystemExit(f"No such reference dir: {ref_root}")
        insts = list_instances(ref_root, args.instances)
        subject = f"REFERENCE {ref_root}"
        out_default = ref_root / "_metrics" / "structural_integrity.jsonl"
    else:
        if not res_root.exists():
            raise SystemExit(f"No such results dir: {res_root}")
        insts = list_instances(res_root, args.instances)
        subject = f"{res_root}"
        out_default = res_root / "_metrics" / "structural_integrity.jsonl"

    if args.limit:
        insts = insts[:args.limit]
    if not insts:
        raise SystemExit("No instances.")

    out_path = args.out or out_default
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sum_path = out_path.with_suffix(".json")

    up = UP_AXIS_INDEX[args.up_axis]
    jobs = []
    for inst in insts:
        ref_glb = (ref_root / inst / "glb" / f"{inst}.glb") if ref_root else None
        gen_glb = None if reference_only else (res_root / inst / "glb" / f"{inst}.glb")
        jobs.append((inst,
                     str(ref_glb) if ref_glb else None,
                     str(gen_glb) if gen_glb else None,
                     args.eps_frac, up, args.ground_min_area_frac,
                     args.max_contact_samples))

    # Big meshes first: they dominate wall-clock, and starting them early keeps
    # the tail short. Peak RSS is bounded by --workers, so keep it modest.
    def _sz(j):
        p = j[2] or j[1]
        try:
            return -os.path.getsize(p)
        except Exception:
            return 0
    jobs.sort(key=_sz)

    print(f"structural_integrity: {subject}")
    print(f"  instances={len(jobs)} workers={args.workers} "
          f"eps_frac={args.eps_frac} up_axis={args.up_axis} "
          f"ground_min_area_frac={args.ground_min_area_frac}")

    rows, t0 = [], time.time()
    if args.workers <= 1:
        for i, j in enumerate(jobs):
            rows.append(_worker(j))
            print(f"  [{i+1}/{len(jobs)}] {rows[-1]['instance']} "
                  f"{rows[-1]['status']} {rows[-1]['seconds']}s", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_worker, j): j[0] for j in jobs}
            for i, fut in enumerate(as_completed(futs)):
                try:
                    rows.append(fut.result())
                except Exception as e:
                    rows.append({"instance": futs[fut],
                                 "status": f"WORKER_FAIL:{e}"})
                if (i + 1) % 10 == 0 or i == len(jobs) - 1:
                    ok = sum(1 for r in rows if r.get("status") == "OK")
                    print(f"  [{i+1}/{len(jobs)}] ok={ok} "
                          f"elapsed={time.time()-t0:.1f}s", flush=True)

    rows.sort(key=lambda r: r["instance"])
    with out_path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    summary = aggregate(rows)
    summary.update({
        "subject": subject,
        "mode": "reference_only" if reference_only else "results",
        "results_dir": str(res_root) if res_root else None,
        "reference_dir": str(ref_root) if ref_root else None,
        "eps_frac": args.eps_frac,
        "up_axis": args.up_axis,
        "ground_min_area_frac": args.ground_min_area_frac,
        "normalization": "unit_sphere(area_weighted_centroid)",
        "weld": "merge_vertices(merge_tex=True, merge_norm=True) post-normalize",
        "component_definition": "vertex-graph connected components",
        "contact_test": "eps-grid, 26-neighbourhood (detects <=eps, may accept <=sqrt(3)eps)",
        "jsonl": str(out_path),
        "elapsed_s": round(time.time() - t0, 1),
    })
    sum_path.write_text(json.dumps(summary, indent=2))

    # stdout
    def f2(x, d=3):
        return "  --  " if x is None else f"{x:.{d}f}"
    s = summary["scalars"]
    b = summary["booleans"]
    print()
    print(f"=== structural integrity — {subject} ===")
    print(f"  instances={summary['n_instances']}  ok={summary['n_ok']}  "
          f"({summary['ok_rate']:.1%})" if summary["ok_rate"] is not None else "")
    print(f"  status: {summary['status_counts']}")
    for f in ["n_components", "largest_component_volume_fraction",
              "largest_component_area_fraction", "floating_part_rate",
              "floating_area_fraction", "nonmanifold_edge_rate",
              "boundary_edge_rate", "ground_contact_area_frac",
              "com_support_margin"]:
        c = s[f]["conditional"]
        p = s[f]["penalized"]
        if c is None:
            continue
        print(f"  {f:38s} cond mean={f2(c['mean'],4):>10} med={f2(c['median'],4):>10}"
              f"   pen mean={f2(p['mean'],4) if p else '  --  ':>10}")
    for f in BOOL_FIELDS:
        print(f"  {f:38s} cond rate={f2(b[f]['conditional_rate']):>8}"
              f"   pen rate={f2(b[f]['penalized_rate']):>8}")
    print(f"  Wrote {out_path}")
    print(f"  Wrote {sum_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
