"""Non-finite (NaN / inf) transforms and vertices — executed BY Blender, never imported by the harness.

A model's bpy code that divides by zero, normalizes a zero vector or takes ``math.acos`` of 1.0000001
leaves a NaN in an object's location / rotation / scale or in its vertices.  Blender keeps it
silently; what reads the scene afterwards does not: the census GLB's JSON carried
``"matrix":[NaN,…]`` and the probe host's ``JSON.parse`` failed — read as a HARNESS failure, so the
model was never told (D114, 2026-10-17); the official glTF exporter drops a NaN translation (the
object exports at the origin) and writes NaN vertices into the buffer.  So every wrapper checks
what it is about to write, and a non-finite value is the MODEL's build error, naming the object,
the channel and — when the wrapper knows it — the file that built it.

Standalone: numpy + the duck-typed bpy objects it is handed (a unit test hands it plain objects).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import numpy as np

TRANSFORM_ERROR, GEOMETRY_ERROR = "NonFiniteTransform", "NonFiniteGeometry"
#: an object's own channel is finite, its world matrix is not: something evaluated produced it
EVALUATED_NOTE = "matrix_world (its own location/rotation/scale are finite: a constraint, driver or parent produced it)"
_SHOWN = 8
_CAUSES = ("usual causes: a division by zero, normalized() of a zero-length vector, math.acos/asin/sqrt "
           "outside their domain, a scale of 0 inverted; guard the computation that sets it")


def finite(values: Any) -> bool:
    """Every number in ``values`` (a vector, a matrix, an array) is finite; ``True`` when it holds none."""
    try:
        arr = np.asarray([list(r) if _iterable(r) else r for r in values] if _iterable(values) else values,
                         dtype=np.float64)
    except (TypeError, ValueError):
        return True
    return bool(np.isfinite(arr).all())


def _iterable(v: Any) -> bool:
    return hasattr(v, "__iter__") and not isinstance(v, (str, bytes))


def _fmt(values: Any) -> str:
    return "(" + ", ".join(f"{float(v):.4g}" for v in values) + ")"


def own_channels(obj: Any) -> list[str]:
    """``obj``'s own transform channels holding a non-finite value, as ``"location=(nan, 0, 1.2)"``."""
    mode = str(getattr(obj, "rotation_mode", "XYZ"))
    rot = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(mode, "rotation_euler")
    drot = "delta_rotation_quaternion" if mode == "QUATERNION" else "delta_rotation_euler"
    out = []
    for ch in ("location", rot, "scale", "delta_location", drot, "delta_scale"):
        v = getattr(obj, ch, None)
        if v is not None and not finite(v):
            out.append(f"{ch}={_fmt(v)}")
    m = getattr(obj, "matrix_parent_inverse", None)
    if m is not None and not finite(m):
        out.append("matrix_parent_inverse")
    return out


def transform_culprit(*objs: Any) -> tuple[Any, list[str]]:
    """The first of ``objs`` — each walked up its parent chain — whose own channels are non-finite,
    with those channels; ``(objs[0], [EVALUATED_NOTE])`` when every channel on the way is finite."""
    for obj in objs:
        o = obj
        while o is not None:
            if ch := own_channels(o):
                return o, ch
            o = getattr(o, "parent", None)
    return objs[0], [EVALUATED_NOTE]


def bad_rows(co: np.ndarray) -> int:
    """How many rows of an (n, k) array hold a non-finite value."""
    return int((~np.isfinite(co.reshape(co.shape[0], -1))).any(axis=1).sum()) if co.size else 0


class NonFiniteError(ValueError):
    """Raised by a writer that found offenders, BEFORE writing anything: the build's error is
    ``offenders.report()``, the model's to fix."""

    def __init__(self, offenders: Offenders) -> None:
        self.offenders = offenders
        super().__init__(offenders.report()["error_message"])


class Offenders:
    """What a wrapper found non-finite, one row per (object, kind); ``origin(obj)`` names the file that
    built ``obj`` (``""`` when unknown).  Falsy while empty; :meth:`report` is the build error."""

    def __init__(self, origin: Callable[[Any], str] | None = None) -> None:
        self.origin = origin
        self.rows: dict[tuple[str, str], dict[str, str]] = {}

    def __bool__(self) -> bool:
        return bool(self.rows)

    def transform(self, *objs: Any, via: str = "") -> None:
        """A non-finite world matrix of ``objs[0]`` (the others: who else could carry it — an
        instance's source object); ``via`` says how it reached the scene."""
        culprit, channels = transform_culprit(*objs)
        detail = "; ".join(channels)
        if culprit is not objs[0]:   # ``via`` says how the culprit reached objs[0] — nothing when it IS objs[0]
            detail = f"{detail} (makes {getattr(objs[0], 'name', '?')!r} non-finite)" + (f" {via}" if via else "")
        self._add(culprit, "transform", detail)

    def instances(self, host: Any, source: str, bad: int, total: int) -> None:
        """``bad`` of the ``total`` instances of ``source`` (as shown: ``"'Blade'"``, ``"mesh 'BladeMesh'"``)
        that ``host`` scatters (geometry nodes, particles, dupli-verts) have a non-finite transform."""
        self._add(host, "instances", f"{bad} of {total} instances of {source} it scatters have a non-finite "
                  "transform (geometry nodes / particles: a NaN position, rotation or scale)")

    def vertices(self, obj: Any, bad: int, total: int) -> None:
        self._add(obj, "vertices", f"{bad} of {total} evaluated vertices (after modifiers) are non-finite")

    def _add(self, obj: Any, kind: str, detail: str) -> None:
        name = str(getattr(obj, "name", "?"))
        if (name, kind) not in self.rows:
            file = ""
            if self.origin is not None:
                try:
                    file = self.origin(obj) or ""
                except Exception:  # noqa: BLE001 — a lookup must never cost the report
                    file = ""
            self.rows[(name, kind)] = {"object": name, "kind": kind, "detail": detail, "file": file}

    def report(self) -> dict[str, Any]:
        """The build-report fields (``error_type`` / ``error_message`` / ``error_file`` / ``error_line``)."""
        rows = list(self.rows.values())
        kind = GEOMETRY_ERROR if all(r["kind"] == "vertices" for r in rows) else TRANSFORM_ERROR
        shown = [f"{r['object']!r}" + (f" [{r['file']}]" if r["file"] else "") + f": {r['detail']}" for r in rows[:_SHOWN]]
        more = f"; +{len(rows) - _SHOWN} more" if len(rows) > _SHOWN else ""
        msg = (f"{len(rows)} object(s) carry non-finite (NaN/inf) values the scene cannot be measured or drawn with — "
               + "; ".join(shown) + more + f". {_CAUSES}")
        return {"error_type": kind, "error_message": msg, "error_file": next((r["file"] for r in rows if r["file"]), ""),
                "error_line": None, "nonfinite": rows}


def scan_objects(objs: Iterable[Any], dg: Any, offenders: Offenders) -> Offenders:
    """World matrix + evaluated vertices of each of ``objs`` (bpy objects) into ``offenders``."""
    for obj in objs:
        if not finite(obj.matrix_world):
            offenders.transform(obj)
        if obj.type not in ("MESH", "CURVE", "SURFACE", "FONT", "META"):
            continue
        ob = obj.evaluated_get(dg)
        try:
            me = ob.to_mesh()
        except RuntimeError:
            continue
        try:
            co = np.empty(len(me.vertices) * 3, np.float32)
            me.vertices.foreach_get("co", co)
            if bad := bad_rows(co.reshape(-1, 3)):
                offenders.vertices(obj, bad, len(me.vertices))
        finally:
            ob.to_mesh_clear()
    return offenders
