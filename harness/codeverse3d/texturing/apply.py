"""Derived PBR maps: roughness and normal, computed from a generated albedo tile.

The texture pass used to ship **albedo only** — one image per material, plus two
scalar factors.  A flat roughness is exactly what the judge keeps calling out:
*"the blue material lacks roughness variation or bump mapping, making it look
like smooth plastic rather than cast iron"* (`tool_hard_bench_vise` r1),
*"materials are flat colors with no texture character (no wood grain, no leather
bump)"* (`furn_hard_rolltop_desk` r2).  A grain that only changes colour is a
sticker; a grain that also changes how the light scatters is a material.

Both maps are derived from the albedo, so they cost nothing (no extra image
call), tile exactly like it does, and can never disagree with it:

* **roughness** — dark, recessed-looking pixels of a surface are its pores, its
  end grain, its casting pits: rougher.  Bright ones are the polished high
  points: smoother.  So roughness is the family's base value modulated by
  ``-(luminance - mean)``, with the amplitude set per family (wood grain and
  cast iron vary a lot, glazed ceramic almost not at all).
* **normal** — wrap-around central differences of that same detail signal, read
  as a height field.  Same reasoning: what is dark is low.

Written into the GLB as a glTF ``metallicRoughnessTexture`` (roughness in G,
metallic in B — the packing the spec mandates) and a ``normalTexture``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import trimesh
from PIL import Image, ImageFilter
from pydantic import BaseModel, Field

from codeverse3d.contracts.plan import StaticPlan
from codeverse3d.conventions import to_snake
from codeverse3d.spatial.measure import GlbLoadError, instance_groups, load_scene
from codeverse3d.texturing.materials import (
    COARSE_TO_FINE,
    FamilyMatch,
    family_for,
    is_framework_default,
    looks_painted,
    out_of_band,
    pbr_for,
)
from codeverse3d.texturing.plan import TexturePart, TexturePlan

#: family → (roughness amplitude, normal-map strength).  Amplitude is the peak
#: deviation from the base roughness across the tile; strength scales the Sobel
#: gradient before it becomes a slope.
VARIATION: dict[str, tuple[float, float]] = {
    "hardwood": (0.16, 0.55),
    "softwood": (0.20, 0.85),
    "painted_wood": (0.10, 0.35),
    "cast_iron": (0.22, 0.90),
    "brushed_metal": (0.12, 0.25),
    "machined_steel": (0.10, 0.25),
    "chrome": (0.05, 0.10),
    "brass": (0.10, 0.25),
    "copper_patina": (0.24, 0.90),
    "painted_metal": (0.08, 0.25),
    "rough_plastic": (0.12, 0.40),
    "glossy_plastic": (0.07, 0.20),
    "rubber": (0.10, 0.45),
    "fabric": (0.14, 0.95),
    "leather": (0.18, 0.90),
    "ceramic": (0.06, 0.20),
    "concrete": (0.20, 0.95),
    "stone": (0.20, 0.90),
    "paper": (0.10, 0.35),
    "glass": (0.02, 0.05),
}
DEFAULT_VARIATION = (0.14, 0.55)

#: blur radius (as a fraction of the tile) used to separate the tile's large-scale
#: shading from the fine detail that actually is surface relief
_DETAIL_BLUR_FRAC = 0.06


def variation_for(family: str) -> tuple[float, float]:
    return VARIATION.get(family, DEFAULT_VARIATION)


def _luminance(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def _blur(a: np.ndarray, radius: float) -> np.ndarray:
    """Gaussian blur of a 0..1 float array (through PIL, so no scipy dependency)."""
    img = Image.fromarray(np.round(np.clip(a, 0.0, 1.0) * 255).astype(np.uint8), mode="L")
    return np.asarray(img.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255.0


def _detail(img: Image.Image) -> np.ndarray:
    """Luminance with its low-frequency component removed, rescaled to ~[-1, 1].

    Removing the low frequencies matters: a generated tile often has a broad
    lighting ramp baked in, and using raw luminance would turn that ramp into a
    roughness gradient across the whole part.  The blurred reference is built from
    the SAME luminance array (not PIL's ``convert("L")``, which uses the ITU-R 601
    weights and would leave a constant offset behind on a solid colour).
    """
    lum = _luminance(img)
    d = lum - _blur(lum, max(1.0, _DETAIL_BLUR_FRAC * max(img.size)))
    scale = float(np.percentile(np.abs(d), 98))
    return np.clip(d / scale, -1.0, 1.0) if scale > 1e-6 else np.zeros_like(d)


def roughness_array(albedo: Image.Image, base_roughness: float, family: str) -> np.ndarray:
    """Per-pixel roughness in 0..1: ``base - amplitude × detail`` (dark = rough)."""
    amp = variation_for(family)[0]
    return np.clip(float(base_roughness) - amp * _detail(albedo), 0.0, 1.0)


def metallic_roughness_image(
    albedo: Image.Image, base_roughness: float, metallic: float, family: str
) -> Image.Image:
    """glTF ``metallicRoughnessTexture``: R unused, **G = roughness**, **B = metallic**."""
    rough = roughness_array(albedo, base_roughness, family)
    h, w = rough.shape
    out = np.zeros((h, w, 3), dtype=np.uint8)
    out[..., 1] = np.round(rough * 255).astype(np.uint8)
    out[..., 2] = np.uint8(round(float(np.clip(metallic, 0.0, 1.0)) * 255))
    return Image.fromarray(out, mode="RGB")


def normal_image(albedo: Image.Image, family: str, *, strength: float | None = None) -> Image.Image:
    """Tangent-space normal map from the albedo's detail, read as a height field.

    Gradients are taken with ``np.roll`` (wrap-around), so a tile that is seamless
    in albedo stays seamless in its normals.
    """
    s = variation_for(family)[1] if strength is None else float(strength)
    height = _detail(albedo)
    # central differences, wrapping at the tile edges
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * 0.5
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * 0.5
    nx, ny, nz = -dx * s * 4.0, dy * s * 4.0, np.ones_like(height)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    rgb = np.stack([nx / norm, ny / norm, nz / norm], axis=-1) * 0.5 + 0.5
    return Image.fromarray(np.round(np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8), mode="RGB")


def is_flat(img: Image.Image, *, tol: float = 0.01) -> bool:
    """A tile with no detail at all (a solid colour): derived maps would be noise."""
    lum = _luminance(img)
    return bool(np.percentile(np.abs(lum - float(lum.mean())), 99) < tol)


# ===================================================================== uv
#: long/mid extent ratio above which ``auto`` picks a cylinder projection ...
CYLINDER_ASPECT = 2.5
#: ... provided the cross-section is roughly round (mid/min below this); slabs → box
CYLINDER_SECTION = 2.0
#: |n · axis| above which a face is a cylinder cap (planar projected)
CAP_COS = 0.75


@dataclass
class Unwrapped:
    vertices: np.ndarray  # (N', 3) LOCAL coordinates (possibly split)
    faces: np.ndarray  # (F, 3)
    normals: np.ndarray | None  # (N', 3) local normals or None
    uv: np.ndarray  # (N', 2)
    projection: str  # box | cylinder | planar_y | planar_z
    axis: int  # projection axis (cylinder axis / planar normal axis)
    n_split: int  # vertices added by seam splitting


def choose_projection(extents_world: np.ndarray, requested: str) -> tuple[str, int]:
    """Resolve ``auto`` → (projection, axis).  Elongated with a round-ish section →
    cylinder around the long axis; everything else (slabs, boxes, frames) → box.  Explicit projections pass through (cylinder axis =
    the longest world axis; planar axes are fixed: planar_y → 1, planar_z → 2)."""
    e = np.asarray(extents_world, dtype=float)
    order = np.argsort(e)[::-1]
    long_axis = int(order[0])
    if requested == "cylinder":
        return "cylinder", long_axis
    if requested == "planar_y":
        return "planar_y", 1
    if requested == "planar_z":
        return "planar_z", 2
    if requested == "box":
        return "box", int(np.argmin(e))
    mid = max(float(e[order[1]]), 1e-9)
    small = max(float(e[order[2]]), 1e-9)
    if float(e[order[0]]) / mid >= CYLINDER_ASPECT and mid / small <= CYLINDER_SECTION:
        return "cylinder", long_axis
    return "box", int(np.argmin(e))


def _face_normals(P: np.ndarray, F: np.ndarray) -> np.ndarray:
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.maximum(ln, 1e-12)


def _box_uv(P: np.ndarray, F: np.ndarray, tile: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-face dominant axis of the face normal → planar projection on the other two."""
    n = _face_normals(P, F)
    axis = np.argmax(np.abs(n), axis=1)  # (F,)
    sign = np.sign(n[np.arange(len(F)), axis])
    sign[sign == 0] = 1.0
    other = np.array([[2, 1], [0, 2], [0, 1]])  # axis → (u axis, v axis)
    corners = P[F]  # (F,3,3)
    ua, va = other[axis, 0], other[axis, 1]
    u = np.take_along_axis(corners, ua[:, None, None].repeat(3, 1), axis=2)[:, :, 0]
    v = np.take_along_axis(corners, va[:, None, None].repeat(3, 1), axis=2)[:, :, 0]
    u = u * sign[:, None]  # keep handedness consistent on opposite faces
    uv = np.stack([u, v], axis=2) / tile
    key = axis[:, None].repeat(3, 1) * 2 + (sign[:, None].repeat(3, 1) < 0).astype(int)
    return uv, key


def _planar_uv(P: np.ndarray, F: np.ndarray, axis: int, tile: float) -> tuple[np.ndarray, np.ndarray]:
    other = {0: (2, 1), 1: (0, 2), 2: (0, 1)}[axis]
    corners = P[F]
    uv = np.stack([corners[:, :, other[0]], corners[:, :, other[1]]], axis=2) / tile
    return uv, np.zeros((len(F), 3), dtype=int)


def _cylinder_uv(P: np.ndarray, F: np.ndarray, axis: int, tile: float) -> tuple[np.ndarray, np.ndarray]:
    a, b = [i for i in range(3) if i != axis]
    center = 0.5 * (P.min(axis=0) + P.max(axis=0))
    d = P - center
    radius = float(np.mean(np.hypot(d[:, a], d[:, b])))
    radius = max(radius, 1e-4)
    circ = 2.0 * np.pi * radius
    ang = np.arctan2(d[:, b], d[:, a])  # (-π, π]
    u_v = (ang + np.pi) / (2.0 * np.pi) * circ / tile  # 0..circ/tile
    v_v = P[:, axis] / tile
    u = u_v[F]  # (F,3)
    v = v_v[F]
    key = np.zeros((len(F), 3), dtype=int)
    # seam: faces straddling angle ±π have a u spread > half circumference → shift the low corners
    span = u.max(axis=1) - u.min(axis=1)
    wrap = span > 0.5 * circ / tile
    if wrap.any():
        low = u < (u.max(axis=1, keepdims=True) - 0.5 * circ / tile)
        shift = wrap[:, None] & low
        u = u + shift * (circ / tile)
        key = key + shift.astype(int)
    # caps: planar
    n = _face_normals(P, F)
    cap = np.abs(n[:, axis]) > CAP_COS
    if cap.any():
        corners = P[F]
        u = np.where(cap[:, None], corners[:, :, a] / tile, u)
        v = np.where(cap[:, None], corners[:, :, b] / tile, v)
        key = np.where(cap[:, None], 2, key)
    return np.stack([u, v], axis=2), key


def split_by_key(
    vertices: np.ndarray, faces: np.ndarray, normals: np.ndarray | None, uv_corner: np.ndarray, key_corner: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray, int]:
    """Duplicate a vertex once per distinct (vertex, key) pair; UV per new vertex =
    mean of its corner UVs (they agree within a key by construction)."""
    F = faces
    flat_v = F.reshape(-1)
    flat_k = key_corner.reshape(-1)
    pair = flat_v.astype(np.int64) * (int(flat_k.max()) + 1 if flat_k.size else 1) + flat_k
    uniq, inverse = np.unique(pair, return_inverse=True)
    n_new = len(uniq)
    src = np.zeros(n_new, dtype=np.int64)
    src[inverse] = flat_v
    new_vertices = vertices[src]
    new_normals = normals[src] if normals is not None else None
    new_uv = np.zeros((n_new, 2), dtype=np.float64)
    counts = np.zeros(n_new, dtype=np.float64)
    np.add.at(new_uv, inverse, uv_corner.reshape(-1, 2))
    np.add.at(counts, inverse, 1.0)
    new_uv /= np.maximum(counts, 1.0)[:, None]
    new_faces = inverse.reshape(-1, 3)
    return new_vertices, new_faces, new_normals, new_uv, int(n_new - len(vertices))


def unwrap(
    vertices_local: np.ndarray,
    faces: np.ndarray,
    transform: np.ndarray,
    *,
    projection: str = "auto",
    tile_size_m: float = 0.3,
    normals_local: np.ndarray | None = None,
) -> Unwrapped:
    """Compute UVs for one mesh given its 4×4 world ``transform``.  Returns the
    (possibly vertex-split) mesh in LOCAL coordinates plus per-vertex UVs."""
    V = np.asarray(vertices_local, dtype=np.float64)
    F = np.asarray(faces, dtype=np.int64)
    if V.ndim != 2 or V.shape[1] != 3 or F.ndim != 2 or F.shape[1] != 3 or len(F) == 0:
        raise ValueError("unwrap needs (N,3) vertices and (F,3) triangles")
    tile = float(tile_size_m)
    if not tile > 0:
        raise ValueError("tile_size_m must be > 0")
    T = np.asarray(transform, dtype=np.float64)
    P = V @ T[:3, :3].T + T[:3, 3]
    ext = P.max(axis=0) - P.min(axis=0)
    proj, axis = choose_projection(ext, projection)
    if proj == "box":
        uv_c, key_c = _box_uv(P, F, tile)
    elif proj == "cylinder":
        uv_c, key_c = _cylinder_uv(P, F, axis, tile)
    else:
        uv_c, key_c = _planar_uv(P, F, axis, tile)
    nv, nf, nn, uv, n_split = split_by_key(V, F, normals_local, uv_c, key_c)
    # keep numbers small for float32 TEXCOORD precision (pattern is periodic anyway)
    uv = uv - np.floor(uv.min(axis=0))
    return Unwrapped(vertices=nv, faces=nf, normals=nn, uv=uv, projection=proj, axis=axis, n_split=n_split)


# ===================================================================== apply
log = logging.getLogger(__name__)


class ApplyReport(BaseModel):
    glb_out: str
    parts_textured: list[str] = Field(default_factory=list)
    parts_skipped: list[str] = Field(default_factory=list)
    parts_unmatched: list[str] = Field(default_factory=list, description="scene nodes with no plan part")
    projections: dict[str, str] = Field(default_factory=dict, description="node → projection used")
    n_materials: int = 0
    n_derived_maps: int = 0
    n_split_vertices: int = 0
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = 0


def node_part_lookup(node_names: list[str], plan_parts: dict[str, TexturePart]) -> dict[str, TexturePart | None]:
    """Map scene node names to plan parts: exact (snake) → instance base (``Leg_0`` →
    ``Leg``) → link piece (``Link__2`` → ``Link``)."""
    groups = instance_groups(list(node_names))
    base_of: dict[str, str] = {}
    for base, members in groups.items():
        for m in members:
            base_of[m] = base
    out: dict[str, TexturePart | None] = {}
    for n in node_names:
        cands = [to_snake(n), to_snake(base_of.get(n, n)), to_snake(n.split("__", 1)[0])]
        hit = next((plan_parts[c] for c in cands if c in plan_parts), None)
        out[n] = hit
    return out


def _material(
    part: TexturePart, image: Image.Image, *, derived_maps: bool = True
) -> trimesh.visual.material.PBRMaterial:
    """PBR material for one texture id.

    Beyond the albedo the material carries two maps DERIVED from that albedo
    (``derived_maps`` below): a metallic/roughness texture so the grain also modulates
    the specular lobe, and a normal map so it catches light in relief.  They cost
    no extra image call and cannot disagree with the colour.  ``derived_maps=False``
    reproduces the albedo-only material the pass shipped before.
    """
    tint = part.tint_rgb or (1.0, 1.0, 1.0)
    factor = [int(round(255 * float(c))) for c in tint] + [255]
    fine = COARSE_TO_FINE.get(part.material_family, part.material_family)
    extra: dict[str, Any] = {}
    if derived_maps and not is_flat(image):
        extra["metallicRoughnessTexture"] = metallic_roughness_image(
            image, float(part.roughness), float(part.metallic), fine)
        extra["normalTexture"] = normal_image(image, fine)
    return trimesh.visual.material.PBRMaterial(
        name=part.texture_id,
        baseColorTexture=image,
        baseColorFactor=factor,
        metallicFactor=float(part.metallic),
        roughnessFactor=float(part.roughness),
        doubleSided=False,
        **extra,
    )


def _node_transform(scene: trimesh.Scene, node: str) -> np.ndarray:
    T, _ = scene.graph.get(node)
    return np.asarray(T, dtype=np.float64)


def apply_textures(
    glb_in: Path | str,
    texture_plan: TexturePlan,
    textures: dict[str, Path | str],
    glb_out: Path | str,
    *,
    verify: bool = True,
    derived_maps: bool = True,
) -> ApplyReport:
    """Write ``glb_out`` = ``glb_in`` with textured PBR materials on the planned parts.
    ``textures`` maps texture_id → PNG path (missing ids leave those parts untextured)."""
    t0 = time.time()
    glb_in, glb_out = Path(glb_in), Path(glb_out)
    scene = load_scene(glb_in)
    rep = ApplyReport(glb_out=str(glb_out))
    parts = texture_plan.by_part()
    nodes = list(scene.graph.nodes_geometry)
    lookup = node_part_lookup(nodes, parts)
    images: dict[str, Image.Image] = {}
    for tid, p in textures.items():
        p = Path(p)
        if p.is_file():
            images[tid] = Image.open(p).convert("RGB")
        else:
            rep.warnings.append(f"texture file missing for {tid}: {p.name}")
    materials: dict[tuple[str, float, float, tuple[float, ...] | None], trimesh.visual.material.PBRMaterial] = {}
    geom_proj: dict[str, str] = {}
    for node in nodes:
        part = lookup.get(node)
        if part is None:
            rep.parts_unmatched.append(node)
            continue
        if part.skip:
            rep.parts_skipped.append(node)
            continue
        img = images.get(part.texture_id)
        if img is None:
            rep.parts_skipped.append(node)
            rep.warnings.append(f"{node}: no image for texture {part.texture_id}")
            continue
        _, geom_name = scene.graph.get(node)
        if geom_name is None:
            continue
        if geom_name in geom_proj:  # instance sharing an already-unwrapped geometry
            rep.parts_textured.append(node)
            rep.projections[node] = geom_proj[geom_name]
            continue
        geom = scene.geometry.get(geom_name)
        if not isinstance(geom, trimesh.Trimesh) or len(geom.faces) == 0:
            rep.parts_unmatched.append(node)
            continue
        try:
            uw = unwrap(geom.vertices, geom.faces, _node_transform(scene, node), projection=part.projection,
                        tile_size_m=part.tile_size_m, normals_local=np.asarray(geom.vertex_normals))
        except ValueError as e:
            rep.warnings.append(f"{node}: unwrap failed: {e}")
            rep.parts_skipped.append(node)
            continue
        mkey = (part.texture_id, float(part.roughness), float(part.metallic), part.tint_rgb)
        mat = materials.get(mkey)
        if mat is None:
            mat = materials[mkey] = _material(part, img, derived_maps=derived_maps)
        mesh = trimesh.Trimesh(vertices=uw.vertices, faces=uw.faces, vertex_normals=uw.normals, process=False)
        mesh.visual = trimesh.visual.TextureVisuals(uv=uw.uv, material=mat)
        mesh.metadata = dict(geom.metadata or {})
        scene.geometry[geom_name] = mesh
        geom_proj[geom_name] = uw.projection
        rep.parts_textured.append(node)
        rep.projections[node] = uw.projection
        rep.n_split_vertices += uw.n_split
    rep.n_materials = len(materials)
    rep.n_derived_maps = sum(1 for m in materials.values() if getattr(m, "normalTexture", None) is not None)
    glb_out.parent.mkdir(parents=True, exist_ok=True)
    scene.export(glb_out)
    if verify:
        rep.warnings.extend(verify_textured_glb(glb_in, glb_out, expected_textured=len(rep.parts_textured)))
    rep.duration_ms = int((time.time() - t0) * 1000)
    return rep


def verify_textured_glb(glb_in: Path, glb_out: Path, *, expected_textured: int) -> list[str]:
    """Reload and compare: same node-name set, textures present.  Returns warnings
    (empty = ok); raises ``GlbLoadError`` when the output does not load at all."""
    warnings: list[str] = []
    try:
        a = load_scene(glb_in)
        b = load_scene(glb_out)
    except GlbLoadError as e:
        raise GlbLoadError(f"textured GLB verification failed: {e}") from e
    na, nb = set(a.graph.nodes_geometry), set(b.graph.nodes_geometry)
    if na != nb:
        warnings.append(f"node set changed: missing={sorted(na - nb)[:5]} extra={sorted(nb - na)[:5]}")
    n_tex = sum(
        1 for g in b.geometry.values()
        if isinstance(g.visual, trimesh.visual.TextureVisuals) and getattr(g.visual.material, "baseColorTexture", None) is not None
    )
    if expected_textured and n_tex == 0:
        warnings.append("no textured geometry found after reload")
    fa = sum(len(g.faces) for g in a.geometry.values() if isinstance(g, trimesh.Trimesh))
    fb = sum(len(g.faces) for g in b.geometry.values() if isinstance(g, trimesh.Trimesh))
    if fa != fb:
        warnings.append(f"face count changed {fa} → {fb}")
    return warnings


# ===================================================================== normalise


class MaterialChange(BaseModel):
    node: str
    material: str = ""
    family: str
    keyword: str = ""
    reason: str  # untouched | impossible
    metallic: tuple[float, float]  # before, after
    roughness: tuple[float, float]


class NormaliseReport(BaseModel):
    glb_out: str = ""
    changes: list[MaterialChange] = Field(default_factory=list)
    n_materials: int = 0
    n_unchanged: int = 0
    n_unresolved: int = 0
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = 0

    def changed(self) -> bool:
        return bool(self.changes)

    def table(self) -> str:
        if not self.changes:
            return "no material needed normalising"
        rows = [f"{'node':22} {'family':15} {'why':11} metallic      roughness"]
        for c in self.changes:
            rows.append(f"{c.node[:22]:22} {c.family:15} {c.reason:11} "
                        f"{c.metallic[0]:.2f}→{c.metallic[1]:.2f}   {c.roughness[0]:.2f}→{c.roughness[1]:.2f}")
        return "\n".join(rows)


def _plan_materials(plan: StaticPlan | None) -> dict[str, str]:
    """snake(part name) → the plan's ``material`` string for that part."""
    if plan is None:
        return {}
    return {to_snake(p.name): (p.material or p.description or "") for p in plan.parts}


def _factors(mat: object) -> tuple[float | None, float | None]:
    m = getattr(mat, "metallicFactor", None)
    r = getattr(mat, "roughnessFactor", None)
    return (None if m is None else float(m)), (None if r is None else float(r))


def _base_rgb(mat: object) -> tuple[float, float, float] | None:
    """``baseColorFactor`` as 0..1 RGB (trimesh stores it as 0-255 RGBA)."""
    f = getattr(mat, "baseColorFactor", None)
    if f is None or len(f) < 3:
        return None
    vals = [float(c) for c in f[:3]]
    scale = 255.0 if max(vals) > 1.0 else 1.0
    return (vals[0] / scale, vals[1] / scale, vals[2] / scale)


def _has_texture(mat: object) -> bool:
    return any(getattr(mat, k, None) is not None
               for k in ("baseColorTexture", "metallicRoughnessTexture", "normalTexture"))


class Verdict(NamedTuple):
    family: str
    reason: str      # untouched | impossible
    keyword: str
    metallic: float
    roughness: float


def _clamp(v: float, lo: float, hi: float) -> float:
    return round(min(hi, max(lo, v)), 3)


def _evidence(material_name: str, node: str, plan_text: str) -> tuple[FamilyMatch | None, str]:
    """Best family match and WHERE it came from (``material`` > ``node`` > ``plan``)."""
    for tier, text in (("material", material_name), ("node", node), ("plan", plan_text)):
        hit = family_for(text)
        if hit is not None:
            return hit, tier
    return None, ""


def classify(
    node: str, material_name: str, plan_text: str, metallic: float | None, roughness: float | None,
    base_rgb: tuple[float, float, float] | None = None,
) -> Verdict | None:
    """What (if anything) to do with one material.

    ``untouched`` snaps to the family's canonical numbers; ``impossible`` only
    *clamps* the offending factor to the nearest edge of the family's plausible
    band, so an author's deliberate-but-extreme choice survives as far as physics
    allows.  ``None`` = leave it exactly as it is."""
    hit, tier = _evidence(material_name, node, plan_text)
    if hit is None:
        return None
    family = hit.family
    if looks_painted(family, base_rgb):
        # a saturated colour on a "cast iron" part means paint over the iron
        family = "painted_wood" if family == "cast_iron" and (base_rgb or (0, 0, 0))[1] > 0.5 else "painted_metal"
    target = pbr_for(family)
    if target is None:
        return None
    if is_framework_default(metallic, roughness):
        return Verdict(target.family, "untouched", hit.keyword, target.metallic, target.roughness)
    if tier == "plan":
        # the plan's prose covers a whole part, not one material: it is good enough to
        # spot a never-configured default, never good enough to overrule real numbers
        return None
    bad = out_of_band(target, metallic, roughness)
    if not bad:
        return None
    m = 1.0 if metallic is None else float(metallic)
    r = 1.0 if roughness is None else float(roughness)
    return Verdict(
        target.family, "impossible", hit.keyword,
        _clamp(m, *target.metallic_band) if "metallic" in bad else m,
        _clamp(r, *target.roughness_band) if "roughness" in bad else r,
    )


def normalise_materials(
    glb_in: Path | str, glb_out: Path | str, *, plan: StaticPlan | None = None, write: bool = True
) -> NormaliseReport:
    """Rewrite the flat-default / impossible materials of ``glb_in`` into ``glb_out``.

    Geometry, node names, UVs and base colours are untouched; only
    ``metallicFactor`` / ``roughnessFactor`` move.  When nothing needs changing the
    output is not written and ``report.glb_out`` stays empty.
    """
    t0 = time.time()
    glb_in, glb_out = Path(glb_in), Path(glb_out)
    scene = load_scene(glb_in)
    rep = NormaliseReport()
    plan_mats = _plan_materials(plan)
    seen: dict[int, tuple[float, float] | None] = {}
    for node in scene.graph.nodes_geometry:
        _, geom_name = scene.graph.get(node)
        geom = scene.geometry.get(geom_name) if geom_name else None
        mat = getattr(getattr(geom, "visual", None), "material", None)
        if mat is None:
            rep.n_unresolved += 1
            continue
        if id(mat) in seen:  # a material shared by several nodes is normalised once
            continue
        rep.n_materials += 1
        if _has_texture(mat):
            rep.n_unchanged += 1
            seen[id(mat)] = None
            continue
        metallic, roughness = _factors(mat)
        snake = to_snake(node)
        plan_text = plan_mats.get(snake) or plan_mats.get(to_snake(node.split("__", 1)[0])) or ""
        verdict = classify(node, str(getattr(mat, "name", "") or ""), plan_text, metallic, roughness,
                           _base_rgb(mat))
        if verdict is None:
            rep.n_unchanged += 1
            seen[id(mat)] = None
            continue
        before = (1.0 if metallic is None else metallic, 1.0 if roughness is None else roughness)
        mat.metallicFactor = float(verdict.metallic)
        mat.roughnessFactor = float(verdict.roughness)
        seen[id(mat)] = (verdict.metallic, verdict.roughness)
        rep.changes.append(MaterialChange(
            node=node, material=str(getattr(mat, "name", "") or ""), family=verdict.family,
            keyword=verdict.keyword, reason=verdict.reason,
            metallic=(round(before[0], 3), verdict.metallic), roughness=(round(before[1], 3), verdict.roughness),
        ))
    if rep.changes and write:
        glb_out.parent.mkdir(parents=True, exist_ok=True)
        scene.export(glb_out)
        rep.glb_out = str(glb_out)
        rep.warnings.extend(_verify(glb_in, glb_out))
    rep.duration_ms = int((time.time() - t0) * 1000)
    return rep


def _verify(glb_in: Path, glb_out: Path) -> list[str]:
    """Same nodes, same triangles — this pass may only move two floats per material."""
    return verify_textured_glb(glb_in, glb_out, expected_textured=0)
