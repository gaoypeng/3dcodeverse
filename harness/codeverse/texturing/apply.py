"""Apply a ``TexturePlan`` + generated images to the canonical GLB → a textured GLB.

How textures are embedded (trimesh 4.x): every textured geometry gets
``trimesh.visual.TextureVisuals(uv=<per-vertex UV>, material=PBRMaterial(
baseColorTexture=<PIL image>, baseColorFactor=tint|white, metallicFactor,
roughnessFactor))``; ``Scene.export(path)`` (glTF exporter) writes TEXCOORD_0,
one glTF ``image``/``texture``/``material`` per distinct ``PBRMaterial`` object
(materials are shared across parts with the same texture id + factors, so the
GLB carries each PNG once) and keeps node names + transforms.  Each material also
carries a ``metallicRoughnessTexture`` and a ``normalTexture`` DERIVED from its
albedo (:mod:`codeverse.texturing.maps`), so the grain modulates the specular
lobe and the relief, not only the colour — ``derived_maps=False`` restores the
albedo-only behaviour.  Geometry
positions are never altered (only seam-split vertices are duplicated); parts
marked ``skip`` keep their original visuals (flat colours / vertex colours).

Verification: the exported file is reloaded with trimesh and the node-name set
and per-part textured state are checked before the function returns.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image
from pydantic import BaseModel, Field

from codeverse.conventions import to_snake
from codeverse.spatial.measure import GlbLoadError, instance_groups, load_scene
from codeverse.texturing.maps import is_flat, metallic_roughness_image, normal_image
from codeverse.texturing.materials import COARSE_TO_FINE
from codeverse.texturing.plan import TexturePart, TexturePlan
from codeverse.texturing.uv import unwrap

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

    Beyond the albedo the material carries two maps DERIVED from that albedo (see
    ``texturing.maps``): a metallic/roughness texture so the grain also modulates
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


def textured_summary(glb: Path | str) -> dict[str, int]:
    """Counts for reports / tests: geometries, textured geometries, distinct images."""
    s = load_scene(glb)
    textured = 0
    imgs: set[int] = set()
    for g in s.geometry.values():
        vis = getattr(g, "visual", None)
        mat = getattr(vis, "material", None)
        tex = getattr(mat, "baseColorTexture", None)
        if tex is not None:
            textured += 1
            imgs.add(id(tex))
    return {"geometries": len(s.geometry), "textured": textured, "images": len(imgs)}
