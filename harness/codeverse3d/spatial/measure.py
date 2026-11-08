"""Measure the canonical GLB (Y-up, meters, one named top-level node per part)."""

from __future__ import annotations

import contextlib
import json
import struct
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from codeverse3d.contracts.artifacts import Measurement, PartMeasure
from codeverse3d.conventions import LINK_PIECE_SEP, split_instance

__all__ = [
    "load_scene",
    "part_meshes",
    "cached_parts",
    "solid_parts",
    "measure_glb",
    "measure_summary_table",
    "fmt_extent_cm",
    "fmt_vec",
    "instance_groups",
    "merged_mesh",
]

class GlbLoadError(RuntimeError):
    """The GLB could not be loaded or contains no mesh geometry."""


def load_scene(glb: Path | str) -> trimesh.Scene:
    """Load ``glb`` as a ``trimesh.Scene`` (always a scene, even for one mesh)."""
    p = Path(glb)
    if not p.is_file():
        raise GlbLoadError(f"GLB not found: {p.name}")
    try:
        scene = trimesh.load(str(p), force="scene", process=False)
    except Exception as e:  # trimesh raises many types
        raise GlbLoadError(f"cannot load {p.name}: {type(e).__name__}: {e}") from e
    if not isinstance(scene, trimesh.Scene):
        raise GlbLoadError(f"{p.name}: unexpected load result {type(scene).__name__}")
    return scene


def gltf_node_names(glb: Path | str) -> list[str | None]:
    """The ``name`` of every node in the GLB's JSON chunk, in index order (None = unnamed)."""
    with Path(glb).open("rb") as fh:
        head = fh.read(20)
        if len(head) < 20 or head[:4] != b"glTF":
            return []
        (n,) = struct.unpack("<I", head[12:16])
        try:
            js = json.loads(fh.read(n))
        except ValueError:
            return []
    return [node.get("name") for node in js.get("nodes", [])]


def node_name_findings(glb: Path | str) -> list[str]:
    """Findings for a GLB whose node names trimesh cannot key on."""
    names = gltf_node_names(glb)
    if not names:
        return []
    out: list[str] = []
    unnamed = sum(1 for n in names if not n)
    dups = {n: c for n, c in Counter(n for n in names if n).items() if c > 1}
    if unnamed:
        out.append(f"{unnamed} of {len(names)} glTF nodes are unnamed — parts are keyed by node name; "
                   "measurements and contact checks may mis-pose them")
    if dups:
        shown = ", ".join(f"'{n}' x{c}" for n, c in sorted(dups.items())[:6])
        out.append(f"duplicate glTF node names ({shown}) — trimesh re-parents renamed nodes; "
                   "measurements and contact checks may mis-pose them")
    return out


def _subtree_nodes(scene: trimesh.Scene, root: str) -> list[str]:
    """All nodes under ``root`` (inclusive), depth first."""
    children = scene.graph.transforms.children
    out, stack, seen = [], [root], set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
        stack.extend(children.get(n, ()))
    return out


def world_transform(scene: trimesh.Scene, node: str) -> np.ndarray:
    """``node``'s world matrix, composed by walking the edge matrices up to the base frame."""
    edges = scene.graph.transforms.edge_data
    parents = scene.graph.transforms.parents
    m = np.eye(4)
    seen: set[str] = set()
    while node != scene.graph.base_frame and node in parents and node not in seen:
        seen.add(node)
        parent = parents[node]
        local = edges.get((parent, node), {}).get("matrix")
        if local is not None:
            m = np.asarray(local, dtype=float) @ m
        node = parent
    return m


def _world_mesh(scene: trimesh.Scene, node: str) -> trimesh.Trimesh | None:
    """Geometry at ``node`` transformed to world space (None if not a mesh)."""
    try:
        _, geom_name = scene.graph.get(node)
        transform = world_transform(scene, node)
    except Exception:
        return None
    if geom_name is None:
        return None
    geom = scene.geometry.get(geom_name)
    if not isinstance(geom, trimesh.Trimesh) or geom.faces is None or len(geom.faces) == 0:
        return None
    m = geom.copy()
    m.apply_transform(transform)
    with contextlib.suppress(Exception):
        m.merge_vertices(merge_tex=True, merge_norm=True)
    return m


def _children(scene: trimesh.Scene, node: str) -> list[str]:
    return list(scene.graph.transforms.children.get(node, []))


def _node_has_geometry(scene: trimesh.Scene, node: str) -> bool:
    try:
        return scene.graph[node][1] is not None
    except Exception:
        return False


def count_materials(scene: trimesh.Scene) -> int:
    return len({id(g.visual.material) for g in scene.geometry.values() if hasattr(g, "visual") and hasattr(g.visual, "material")})


def effective_top_nodes(scene: trimesh.Scene) -> list[str]:
    """Direct children of the scene root, descending through single wrapper nodes that carry no
    geometry themselves (a three.js root Group, a URDF ``<robot>`` node) so that parts are the top nodes."""
    tops = _children(scene, scene.graph.base_frame)
    seen = 0
    while len(tops) == 1 and not _node_has_geometry(scene, tops[0]) and _children(scene, tops[0]) and seen < 4:
        tops = _children(scene, tops[0])
        seen += 1
    return tops


def part_meshes(scene: trimesh.Scene) -> OrderedDict[str, trimesh.Trimesh | None]:
    """One world-space mesh per part, in scene order."""
    out: OrderedDict[str, trimesh.Trimesh | None] = OrderedDict()
    links = scene.metadata.get("links") if isinstance(scene.metadata, dict) else None
    nodes = set(scene.graph.nodes)
    if isinstance(links, list) and links and all(str(link) in nodes for link in links):
        for link in links:
            link = str(link)
            own = [n for n in _subtree_nodes(scene, link) if n == link or str(n).startswith(link + LINK_PIECE_SEP)]
            meshes = [m for n in own if (m := _world_mesh(scene, n)) is not None]
            out[link] = trimesh.util.concatenate(meshes) if meshes else None
        return out
    tops = effective_top_nodes(scene)
    for top in tops:
        meshes = [m for n in _subtree_nodes(scene, top) if (m := _world_mesh(scene, n)) is not None]
        name = str(top)
        if name in out:
            k = 2
            while f"{name}~{k}" in out:
                k += 1
            name = f"{name}~{k}"
        out[name] = trimesh.util.concatenate(meshes) if meshes else None
    if not tops and scene.geometry:
        for gname, geom in scene.geometry.items():
            if isinstance(geom, trimesh.Trimesh) and len(geom.faces):
                out[str(gname)] = geom.copy()
    return out


# --------------------------------------------------------------------- parse memo
_LRU_MAX = 4
_lru_lock = threading.Lock()


@dataclass
class _CacheEntry:
    parts: OrderedDict[str, trimesh.Trimesh | None] | None = None
    measurement: Measurement | None = None


_lru: OrderedDict[tuple[str, int, int], _CacheEntry] = OrderedDict()


def _stat_key(p: Path) -> tuple[str, int, int] | None:
    """(resolved path, size, mtime_ns) — None when the file cannot be stat'ed."""
    try:
        st = p.stat()
    except OSError:
        return None
    return (str(p.resolve()), st.st_size, st.st_mtime_ns)


def _cache_entry(key: tuple[str, int, int]) -> _CacheEntry:
    """The (possibly fresh) LRU entry for ``key``."""
    with _lru_lock:
        e = _lru.get(key)
        if e is None:
            e = _lru[key] = _CacheEntry()
        _lru.move_to_end(key)
        while len(_lru) > _LRU_MAX:
            _lru.popitem(last=False)
        return e


def cached_parts(glb: Path | str) -> OrderedDict[str, trimesh.Trimesh | None]:
    """``part_meshes(load_scene(glb))`` memoized on the file's stat (LRU of 4)."""
    p = Path(glb)
    key = _stat_key(p)
    if key is None:
        return part_meshes(load_scene(p))  # raises GlbLoadError for a missing file
    entry = _cache_entry(key)
    if entry.parts is None:
        entry.parts = part_meshes(load_scene(p))
    return OrderedDict(entry.parts)


def solid_parts(glb: Path | str) -> OrderedDict[str, trimesh.Trimesh]:
    """:func:`cached_parts` without the empty ones — the loader every geometry gate
    (connectivity, sections) starts from."""
    return OrderedDict((k, v) for k, v in cached_parts(glb).items() if v is not None and len(v.faces))


def merged_mesh(parts: dict[str, trimesh.Trimesh | None]) -> trimesh.Trimesh | None:
    """All part meshes concatenated (None if nothing has faces)."""
    meshes = [m for m in parts.values() if m is not None]
    return trimesh.util.concatenate(meshes) if meshes else None


def _count_islands(mesh: trimesh.Trimesh) -> int:
    try:
        return max(1, len(mesh.split(only_watertight=False)))
    except Exception:
        try:
            return max(1, len(trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(mesh.faces)))))
        except Exception:
            return 1


def _vec3(a: Any) -> tuple[float, float, float]:
    return (float(a[0]), float(a[1]), float(a[2]))


def _measure_part(name: str, mesh: trimesh.Trimesh) -> PartMeasure:
    bmin, bmax = mesh.bounds
    watertight = bool(mesh.is_watertight)
    volume: float | None = None
    if watertight:
        try:
            volume = abs(float(mesh.volume))
        except Exception:
            volume = None
    return PartMeasure(
        name=name,
        bbox_min=_vec3(bmin),
        bbox_max=_vec3(bmax),
        tri_count=int(len(mesh.faces)),
        islands=_count_islands(mesh),
        volume_m3=volume,
        watertight=watertight,
    )


def measure_glb(glb: Path | str) -> Measurement:
    """Compute the language-agnostic census of the canonical GLB."""
    p = Path(glb)
    key = _stat_key(p)
    entry = _cache_entry(key) if key is not None else None
    if entry is not None and entry.measurement is not None:
        return entry.measurement.model_copy(deep=True)
    scene = load_scene(p)
    parts = entry.parts if entry is not None and entry.parts is not None else part_meshes(scene)
    m = _measure(scene, parts)
    m.extra["findings"] = [*node_name_findings(p), *m.extra.get("findings", [])]
    if entry is not None:
        if entry.parts is None:
            entry.parts = parts
        entry.measurement = m
    return m.model_copy(deep=True)


def _measure(scene: trimesh.Scene, parts: OrderedDict[str, trimesh.Trimesh | None]) -> Measurement:
    findings: list[str] = []
    rows: list[PartMeasure] = []
    for name, mesh in parts.items():
        if mesh is None:
            findings.append(f"part '{name}' has no mesh geometry (empty node)")
            continue
        if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
            findings.append(f"part '{name}' has no triangles")
            continue
        ext = mesh.bounds[1] - mesh.bounds[0]
        if float(np.max(ext)) < 1e-6:
            findings.append(f"part '{name}' is degenerate (zero extent)")
        rows.append(_measure_part(name, mesh))

    whole = merged_mesh(parts)
    materials = count_materials(scene)
    if whole is None or len(whole.faces) == 0:
        findings.append("model has no triangles at all")
        return Measurement(
            bbox_min=(0.0, 0.0, 0.0), bbox_max=(0.0, 0.0, 0.0), extents=(0.0, 0.0, 0.0),
            center=(0.0, 0.0, 0.0), tri_count=0, n_meshes=len(parts), n_islands=0, parts=rows,
            ground_gap_m=0.0, footprint_offset_m=0.0, materials=materials,
            extra={"findings": findings, "n_parts": len(parts)},
        )
    bmin, bmax = whole.bounds
    ext = bmax - bmin
    center = (bmin + bmax) / 2.0
    extra: dict[str, Any] = {
        "findings": findings,
        "n_parts": len(parts),
        "part_names": [r.name for r in rows],
    }
    return Measurement(
        bbox_min=_vec3(bmin),
        bbox_max=_vec3(bmax),
        extents=_vec3(ext),
        center=_vec3(center),
        tri_count=int(len(whole.faces)),
        n_meshes=sum(1 for m in parts.values() if m is not None),
        n_islands=int(sum(r.islands for r in rows)),
        parts=rows,
        ground_gap_m=float(bmin[1]),
        footprint_offset_m=float(np.hypot(center[0], center[2])),
        materials=materials,
        extra=extra,
    )


# --------------------------------------------------------------------------- table
def instance_groups(names: list[str]) -> OrderedDict[str, list[str]]:
    """Group instance names such as ``Leg_0, Leg_1, Leg_2`` under their shared base name ``Leg``."""
    groups: OrderedDict[str, list[str]] = OrderedDict()
    for n in names:
        groups.setdefault(split_instance(n)[0], []).append(n)
    out: OrderedDict[str, list[str]] = OrderedDict()
    for key, members in groups.items():
        if len(members) > 1:
            members = sorted(members, key=lambda n: int(split_instance(n)[1] or -1))
        out[key if len(members) > 1 else members[0]] = members
    return out


def fmt_extent_cm(ext: Any) -> str:
    """``34.0×47.0×34.0`` — a size in centimetres."""
    return "×".join(f"{float(v) * 100:.1f}" for v in ext)


def fmt_vec(v: Any, digits: int = 3) -> str:
    """``(+0.010, -0.000, +0.250)`` — a signed metre vector."""
    return "(" + ", ".join(f"{round(float(x), digits) + 0.0:+.{digits}f}" for x in v) + ")"


def measure_summary_table(m: Measurement, max_rows: int = 30, *, topology: bool = True) -> str:
    """Compact markdown table for prompts; ``topology=False`` drops the per-part tris / islands / watertight columns
    (the judge's brief: it cites sizes and positions, the topology stays in gates.json)."""
    lines = [
        f"overall: {fmt_extent_cm(m.extents)} cm (W×H×D, Y-up) · centre ({m.center[0]:.3f}, {m.center[1]:.3f}, {m.center[2]:.3f}) m"
        f" · {m.tri_count} tris · {len(m.parts)} parts · {m.n_islands} islands"
        f" · ground gap {m.ground_gap_m * 1000:.1f} mm · footprint offset {m.footprint_offset_m * 1000:.1f} mm",
        "",
        "| part | size W×H×D (cm) | y range (m) |" + (" tris | islands | watertight |" if topology else ""),
        "|---|---|---|" + ("---|---|---|" if topology else ""),
    ]
    by_name = {p.name: p for p in m.parts}
    rows: list[tuple[float, str]] = []
    for key, members in instance_groups([p.name for p in m.parts]).items():
        ps = [by_name[n] for n in members]
        mins = np.min([p.bbox_min for p in ps], axis=0)
        maxs = np.max([p.bbox_max for p in ps], axis=0)
        ext = maxs - mins
        if len(ps) == 1:
            label, size_txt = key, fmt_extent_cm(ext)
        else:
            label = f"{key} ×{len(ps)} ({members[0]}..{split_instance(members[-1])[1]})"
            each = np.max([np.subtract(p.bbox_max, p.bbox_min) for p in ps], axis=0)
            size_txt = f"{fmt_extent_cm(each)} each, span {fmt_extent_cm(ext)}"
        row = f"| {label} | {size_txt} | {mins[1]:.3f}..{maxs[1]:.3f} |"
        if topology:
            wt = "yes" if all(p.watertight for p in ps) else ("no" if not any(p.watertight for p in ps) else "some")
            row += f" {sum(p.tri_count for p in ps)} | {sum(p.islands for p in ps)} | {wt} |"
        size = float(np.prod(np.maximum(ext, 1e-9)))
        rows.append((size, row))
    rows.sort(key=lambda r: -r[0])
    lines.extend(r[1] for r in rows[:max_rows])
    if len(rows) > max_rows:
        lines.append(f"| … {len(rows) - max_rows} more parts | | |" + (" | | |" if topology else ""))
    finds = m.extra.get("findings") or []
    if finds:
        lines.append("")
        lines.extend(f"- finding: {f}" for f in finds[:10])
    return "\n".join(lines)
