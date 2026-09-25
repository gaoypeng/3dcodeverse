"""The scene_blender census GLB — executed BY Blender (``run_bpy_scene.py``), never imported by the harness.

The geometry gates are the JS ones (``probe_scene.mjs`` → ``host_census`` + ``host_placement``), so a
Blender scene reaches them as a GLB the harness writes itself from the EVALUATED depsgraph — what
renders: modifiers, geometry nodes and instances included.  The official glTF exporter is not usable
for this (DESIGN §1.4: it silently drops geometry-nodes instances, and with ``export_gn_mesh`` it did
not finish in 10 min on 73 k grass blades; this writer: 0.1–0.4 s).  Geometry, names, base colour
and custom properties only: the file is an instrument for gates, never a picture.

Layout (what the census and the placement table read):

* root nodes = env objects (whatever is not in a zone collection), by object name — ``backdrop.mjs``
  classifies ``Ground`` / ``Terrain`` / ``Water`` / ``Sky…`` by name, so names are carried verbatim;
* one root node per ZONE collection, named for it; its children = the zone's root objects, their
  parent chains as child nodes (group nodes carry no transform: every mesh node holds its world
  matrix, so an Empty's rotation can never be applied twice);
* a collection-instance Empty = a node whose children are one mesh node per instanced object, all
  sharing one glTF mesh per source mesh — a clone per placed asset, which ``host_placement`` checks;
* any other instance cloud (geometry nodes, particles, dupli-verts/faces) = one
  ``EXT_mesh_gpu_instancing`` node per (instancer, source mesh) — an ``InstancedMesh`` in three.js,
  counted as instances and exempt from per-asset placement exactly like scatter is today;
* a mesh every material of which is volume-only (a fog box) is not written: it is not matter, and
  the fog is reported from bpy instead (``run_bpy_scene.scene_facts``);
* custom properties of str/int/float/bool value → node ``extras`` → three.js ``userData`` (so
  ``obj["placement"] = "free"`` exempts a deliberately airborne thing, as ``userData.placement`` does);
* the scene's ``extras.c3d_cameras`` carries the harness cameras in the GLB frame — the adapter
  (``runtime_js/lib/glb_scene.mjs``) hands them to the host.

Frame: ``basis`` (3×3 rows, from ``conventions.glb_basis`` via the wrapper's argv) takes a Blender
vector into the GLB frame (Y-up, +Z front); nothing here restates it.
"""

from __future__ import annotations

import json
import struct
import time
from typing import Any

import numpy as np

MEASURABLE_TYPES = ("MESH", "CURVE", "SURFACE", "FONT", "META")
_FLOAT, _UINT = 5126, 5125
_ARRAY_BUFFER, _ELEMENT_ARRAY_BUFFER = 34962, 34963


# ----------------------------------------------------------------------------- material facts
def _active_output(tree: Any, kind: str) -> Any:
    outs = [n for n in tree.nodes if n.bl_idname == kind]
    return next((n for n in outs if getattr(n, "is_active_output", False)), outs[0] if outs else None)


def is_volume_material(mat: Any) -> bool:
    """A material whose active output feeds Volume and not Surface: fog, not matter."""
    tree = getattr(mat, "node_tree", None) if mat is not None else None
    if tree is None:
        return False
    out = _active_output(tree, "ShaderNodeOutputMaterial")
    if out is None:
        return False
    return bool(out.inputs["Volume"].is_linked and not out.inputs["Surface"].is_linked)


def is_volume_only(obj: Any) -> bool:
    """Every material slot of ``obj`` is a volume material (and it has at least one)."""
    mats = [s.material for s in getattr(obj, "material_slots", ())]
    return bool(mats) and all(is_volume_material(m) for m in mats)


def _principled(mat: Any) -> Any:
    tree = getattr(mat, "node_tree", None) if mat is not None else None
    if tree is None:
        return None
    return next((n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)


def _input_value(node: Any, name: str, default: Any) -> Any:
    sock = node.inputs.get(name) if node is not None else None
    if sock is None or sock.is_linked:
        return default
    try:
        return sock.default_value
    except AttributeError:
        return default


# ----------------------------------------------------------------------------- math
def _quaternions(rot: np.ndarray) -> np.ndarray:
    """(n, 3, 3) rotation matrices → (n, 4) quaternions (x, y, z, w), Shepperd's method."""
    n = rot.shape[0]
    q = np.empty((n, 4), np.float64)
    tr = rot[:, 0, 0] + rot[:, 1, 1] + rot[:, 2, 2]
    cases = np.stack([tr, rot[:, 0, 0], rot[:, 1, 1], rot[:, 2, 2]], 1).argmax(1)
    for case in range(4):
        m = cases == case
        if not m.any():
            continue
        r = rot[m]
        if case == 0:
            s = np.sqrt(np.maximum(tr[m] + 1.0, 1e-12)) * 2
            q[m] = np.stack([(r[:, 2, 1] - r[:, 1, 2]) / s, (r[:, 0, 2] - r[:, 2, 0]) / s,
                             (r[:, 1, 0] - r[:, 0, 1]) / s, 0.25 * s], 1)
        else:
            i = case - 1
            j, k = (i + 1) % 3, (i + 2) % 3
            s = np.sqrt(np.maximum(1.0 + r[:, i, i] - r[:, j, j] - r[:, k, k], 1e-12)) * 2
            out = np.empty((r.shape[0], 4))
            out[:, i] = 0.25 * s
            out[:, j] = (r[:, j, i] + r[:, i, j]) / s
            out[:, k] = (r[:, k, i] + r[:, i, k]) / s
            out[:, 3] = (r[:, k, j] - r[:, j, k]) / s
            q[m] = out
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def _trs(mats: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(n, 4, 4) GLB-frame matrices → translations, quaternions, scales (a mirror flips one scale)."""
    t = mats[:, :3, 3]
    lin = mats[:, :3, :3]
    s = np.linalg.norm(lin, axis=1)          # column norms: L = R · diag(s)
    s = np.where(s < 1e-12, 1e-12, s)
    r = lin / s[:, None, :]
    neg = np.linalg.det(r) < 0
    if neg.any():
        s[neg, 0] *= -1
        r[neg, :, 0] *= -1
    return t, _quaternions(r), s


# ----------------------------------------------------------------------------- writer
class _Writer:
    def __init__(self, basis: np.ndarray) -> None:
        self.b3 = basis
        self.b4 = np.eye(4)
        self.b4[:3, :3] = basis
        self.buf = bytearray()
        self.views: list[dict[str, Any]] = []
        self.accessors: list[dict[str, Any]] = []
        self.meshes: list[dict[str, Any]] = []
        self.mesh_ix: dict[Any, int | None] = {}
        self.mesh_tris: dict[int, int] = {}
        self.materials: list[dict[str, Any]] = []
        self.mat_ix: dict[str, int] = {}
        self.nodes: list[dict[str, Any]] = []
        self.roots: list[int] = []
        self.group_ix: dict[tuple[str, ...], int] = {}

    # -- buffers
    def add(self, arr: np.ndarray, *, target: int | None = None, comp: int = _FLOAT, typ: str = "VEC3") -> int:
        a = np.ascontiguousarray(arr)
        off = len(self.buf)
        self.buf.extend(a.tobytes())
        self.buf.extend(b"\0" * (-len(self.buf) % 4))
        view: dict[str, Any] = {"buffer": 0, "byteOffset": off, "byteLength": a.nbytes}
        if target:
            view["target"] = target
        self.views.append(view)
        acc: dict[str, Any] = {"bufferView": len(self.views) - 1, "componentType": comp, "count": int(a.shape[0]), "type": typ}
        if target == _ARRAY_BUFFER:
            acc["min"] = a.min(0).tolist()
            acc["max"] = a.max(0).tolist()
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def material(self, mat: Any) -> int:
        key = mat.name if mat is not None else "_none"
        if key not in self.mat_ix:
            bsdf = _principled(mat)
            col = list(_input_value(bsdf, "Base Color", (0.8, 0.8, 0.8, 1.0)))[:4]
            col = [min(1.0, max(0.0, float(c))) for c in col] + [1.0] * (4 - len(col))
            pbr = {"baseColorFactor": col,
                   "metallicFactor": min(1.0, max(0.0, float(_input_value(bsdf, "Metallic", 0.0)))),
                   "roughnessFactor": min(1.0, max(0.0, float(_input_value(bsdf, "Roughness", 0.5))))}
            self.mat_ix[key] = len(self.materials)
            self.materials.append({"name": key, "pbrMetallicRoughness": pbr})
        return self.mat_ix[key]

    def mesh(self, key: Any, me: Any, slots: list[Any]) -> int | None:
        """One glTF mesh per source mesh datablock (``key``); ``None`` for an empty one."""
        if key in self.mesh_ix:
            return self.mesh_ix[key]
        me.calc_loop_triangles()
        nv, nt = len(me.vertices), len(me.loop_triangles)
        if nt == 0 or nv == 0:
            self.mesh_ix[key] = None
            return None
        co = np.empty(nv * 3, np.float32)
        me.vertices.foreach_get("co", co)
        co = (co.reshape(-1, 3).astype(np.float64) @ self.b3.T).astype(np.float32)
        tri = np.empty(nt * 3, np.uint32)
        me.loop_triangles.foreach_get("vertices", tri)
        tri = tri.reshape(-1, 3)
        mi = np.empty(nt, np.int32)
        me.loop_triangles.foreach_get("material_index", mi)
        pos = self.add(co, target=_ARRAY_BUFFER)
        prims = []
        for m in np.unique(mi):
            idx = self.add(tri[mi == m].reshape(-1), target=_ELEMENT_ARRAY_BUFFER, comp=_UINT, typ="SCALAR")
            slot = slots[int(m)] if 0 <= int(m) < len(slots) else None
            prims.append({"attributes": {"POSITION": pos}, "indices": idx, "material": self.material(slot)})
        self.meshes.append({"name": me.name, "primitives": prims})
        ix = len(self.meshes) - 1
        self.mesh_ix[key] = ix
        self.mesh_tris[ix] = nt
        return ix

    # -- nodes
    def glb_matrix(self, mw: Any) -> np.ndarray:
        return self.b4 @ np.array(mw, np.float64) @ self.b4.T

    def node(self, node: dict[str, Any], path: tuple[str, ...]) -> int:
        """Append ``node`` under the group node of ``path`` (a root when ``path`` is empty)."""
        self.nodes.append(node)
        i = len(self.nodes) - 1
        if path:
            self.nodes[self.group(path)].setdefault("children", []).append(i)
        else:
            self.roots.append(i)
        return i

    def group(self, path: tuple[str, ...], extras: dict[str, Any] | None = None) -> int:
        if path not in self.group_ix:
            node: dict[str, Any] = {"name": path[-1]}
            if extras:
                node["extras"] = extras
            self.group_ix[path] = self.node(node, path[:-1])
        return self.group_ix[path]

    def glb(self, scene_extras: dict[str, Any]) -> bytes:
        g: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": "codeverse3d census_glb"},
            "scene": 0, "scenes": [{"nodes": self.roots, "extras": scene_extras}],
            "nodes": self.nodes, "meshes": self.meshes, "materials": self.materials,
            "accessors": self.accessors, "bufferViews": self.views, "buffers": [{"byteLength": len(self.buf)}],
        }
        if any("extensions" in n for n in self.nodes):
            g["extensionsUsed"] = ["EXT_mesh_gpu_instancing"]
        js = json.dumps(g, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        head = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.buf))
        return (head + struct.pack("<II", len(js), 0x4E4F534A) + js
                + struct.pack("<II", len(self.buf), 0x004E4942) + bytes(self.buf))


def extras_of(obj: Any) -> dict[str, Any]:
    """Custom properties a gate can read (``placement = 'free'``); IDProperty groups / arrays dropped."""
    out: dict[str, Any] = {}
    for k in obj.keys():  # noqa: SIM118 — a bpy ID is not iterable over its custom properties
        v = obj[k]
        if isinstance(v, (str, int, float, bool)) and not k.startswith("_"):
            out[k] = v
    return out


def _chain(obj: Any) -> list[Any]:
    out = []
    while obj is not None:
        out.append(obj)
        obj = obj.parent
    return out[::-1]


def write_census_glb(bpy: Any, path: str, *, basis: list[list[float]], zones: dict[str, str],
                     cameras: list[dict[str, Any]]) -> dict[str, Any]:
    """Write the census GLB of the current (t = 0) state to ``path``; return its stats.

    ``zones`` maps a zone COLLECTION name to itself for every zone the wrapper built (the node
    name); every object in one of them (recursively) hangs under that zone's node."""
    t0 = time.perf_counter()
    w = _Writer(np.array(basis, np.float64))
    zone_of: dict[str, str] = {}
    for cname in zones:
        coll = bpy.data.collections.get(cname)
        if coll is not None:
            for o in coll.all_objects:
                zone_of.setdefault(o.name, cname)

    def path_of(obj: Any) -> tuple[str, ...]:
        chain = _chain(obj)
        zone = zone_of.get(chain[0].name)
        return ((zone,) if zone else ()) + tuple(o.name for o in chain)

    def ensure_groups(obj: Any, *, own: bool = False) -> tuple[str, ...]:
        """Group nodes for the ancestors of ``obj`` (+ ``obj`` itself when ``own``), top down so each
        carries its object's custom properties; returns the path ``obj``'s own node hangs under."""
        chain = _chain(obj)
        zone = zone_of.get(chain[0].name)
        base = (zone,) if zone else ()
        upto = chain if own else chain[:-1]
        for i, o in enumerate(upto):
            w.group(base + tuple(x.name for x in chain[: i + 1]), extras_of(o))
        return path_of(obj) if own else path_of(obj)[:-1]

    stats: dict[str, Any] = {"unique_tris": 0, "instanced_tris": 0, "nodes": 0, "meshes": 0, "instances": 0,
                             "clouds": 0, "volumes_skipped": []}
    dg = bpy.context.evaluated_depsgraph_get()
    clouds: dict[tuple[tuple[str, ...], Any], tuple[int | None, str, list[np.ndarray]]] = {}
    for zone in zones:   # an empty zone is still a group the census can see
        w.group((zone,), extras_of(bpy.data.collections[zone]) if zone in bpy.data.collections else None)
    # an object that hosts an instance cloud is its own group (its mesh + the cloud = one row)
    hosts = {inst.parent.original.name for inst in dg.object_instances
             if inst.is_instance and inst.parent is not None and inst.parent.original.instance_type != "COLLECTION"}
    for inst in dg.object_instances:
        ob = inst.object
        if ob.type != "MESH":
            continue
        if inst.is_instance:
            host = inst.parent.original if inst.parent is not None else None
            if host is None or host.hide_render or is_volume_only(ob):
                continue
            ix = w.mesh(ob.data.as_pointer(), ob.data, [s.material for s in ob.material_slots])
            if ix is None:
                continue
            if host.instance_type == "COLLECTION":   # a placed asset: one clone node per instanced mesh
                m = w.glb_matrix(inst.matrix_world)
                w.node({"name": ob.name, "mesh": ix, "matrix": m.T.reshape(-1).tolist()}, ensure_groups(host, own=True))
                stats["instanced_tris"] += w.mesh_tris[ix]
                continue
            key = (path_of(host), ob.data.as_pointer())   # Blender-frame matrices, converted in one batch below
            clouds.setdefault(key, (ix, ob.name, []))[2].append(np.array(inst.matrix_world, np.float32))
            continue
        orig = ob.original
        if orig.hide_render or is_volume_only(orig):
            if is_volume_only(orig):
                stats["volumes_skipped"].append(orig.name)
            continue
        ix = w.mesh(("obj", orig.name), ob.data, [s.material for s in ob.material_slots])
        if ix is None:
            continue
        # a mesh with children is its own group (one placement row), its geometry a node inside it
        under = ensure_groups(orig, own=bool(orig.children) or orig.name in hosts)
        node = {"name": orig.name, "mesh": ix, "matrix": w.glb_matrix(ob.matrix_world).T.reshape(-1).tolist()}
        if ex := extras_of(orig):
            node["extras"] = ex
        w.node(node, under)
        stats["instanced_tris"] += w.mesh_tris[ix]
    for (host_path, _), (ix, oname, mats) in clouds.items():
        if ix is None:
            continue
        t, q, s = _trs(w.b4 @ np.stack(mats).astype(np.float64) @ w.b4.T)
        ext = {"TRANSLATION": w.add(t.astype(np.float32)), "ROTATION": w.add(q.astype(np.float32), typ="VEC4"),
               "SCALE": w.add(s.astype(np.float32))}
        w.group(host_path)
        w.node({"name": f"{oname}_cloud", "mesh": ix, "extensions": {"EXT_mesh_gpu_instancing": {"attributes": ext}}},
               host_path)
        stats["instances"] += len(mats)
        stats["clouds"] += 1
        stats["instanced_tris"] += w.mesh_tris[ix] * len(mats)
    _write_other_types(dg, w, stats, ensure_groups)
    stats["unique_tris"] = sum(w.mesh_tris.values())
    stats["nodes"], stats["meshes"] = len(w.nodes), len(w.meshes)
    data = w.glb({"c3d_cameras": [_glb_camera(c, w.b3) for c in cameras]})
    with open(path, "wb") as fh:
        fh.write(data)
    stats["bytes"] = len(data)
    stats["ms"] = int((time.perf_counter() - t0) * 1000)
    return stats


def _write_other_types(dg: Any, w: _Writer, stats: dict[str, Any], ensure_groups: Any) -> None:
    """Curves, text, surfaces and metaballs that stayed their own type after evaluation."""
    for inst in dg.object_instances:
        ob = inst.object
        if inst.is_instance or ob.type == "MESH" or ob.type not in MEASURABLE_TYPES:
            continue
        orig = ob.original
        if orig.hide_render or is_volume_only(orig):
            continue
        try:
            me = ob.to_mesh()
        except RuntimeError:
            continue
        try:
            ix = w.mesh(("obj", orig.name), me, [s.material for s in ob.material_slots])
        finally:
            ob.to_mesh_clear()
        if ix is None:
            continue
        node = {"name": orig.name, "mesh": ix, "matrix": w.glb_matrix(ob.matrix_world).T.reshape(-1).tolist()}
        w.node(node, ensure_groups(orig, own=bool(orig.children)))
        stats["instanced_tris"] += w.mesh_tris[ix]


def _glb_camera(c: dict[str, Any], b3: np.ndarray) -> dict[str, Any]:
    """A harness camera (Blender frame: ``location`` / ``look_at``, vertical ``fov``) as the host's spec."""
    pos = (b3 @ np.array(c["location"], np.float64)).tolist()
    look = (b3 @ np.array(c["look_at"], np.float64)).tolist()
    return {"name": str(c["name"]), "position": [round(v, 5) for v in pos], "lookAt": [round(v, 5) for v in look],
            "fov": float(c.get("fov", 50.0))}
