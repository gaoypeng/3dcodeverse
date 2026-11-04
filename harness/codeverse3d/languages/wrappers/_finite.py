"""Non-finite (NaN / inf) transforms and vertices; runs inside Blender, never imported by the harness.

Blender keeps NaNs silently but exporters and JSON readers fail on them, so each wrapper
checks what it is about to write and reports a non-finite value as the model's build error.
Standalone: numpy plus duck-typed bpy objects."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import numpy as np

TRANSFORM_ERROR, GEOMETRY_ERROR = "NonFiniteTransform", "NonFiniteGeometry"
#: the object's own channels are finite but its world matrix is not
EVALUATED_NOTE = "matrix_world (its own location/rotation/scale are finite: a constraint, driver or parent produced it)"
_SHOWN = 8
_CAUSES = ("usual causes: a division by zero, normalized() of a zero-length vector, math.acos/asin/sqrt "
           "outside their domain, a scale of 0 inverted; guard the computation that sets it")


def finite(values: Any) -> bool:
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
    """First of ``objs`` (walking up parents) whose own channels are non-finite, with those channels."""
    for obj in objs:
        o = obj
        while o is not None:
            if ch := own_channels(o):
                return o, ch
            o = getattr(o, "parent", None)
    return objs[0], [EVALUATED_NOTE]


def bad_rows(co: np.ndarray) -> int:
    return int((~np.isfinite(co.reshape(co.shape[0], -1))).any(axis=1).sum()) if co.size else 0


class NonFiniteError(ValueError):
    """Raised before anything is written; ``offenders.report()`` is the build error."""

    def __init__(self, offenders: Offenders) -> None:
        self.offenders = offenders
        super().__init__(offenders.report()["error_message"])


class Offenders:
    """What a wrapper found non-finite, one row per (object, kind).  Falsy while empty."""

    def __init__(self, origin: Callable[[Any], str] | None = None) -> None:
        self.origin = origin
        self.rows: dict[tuple[str, str], dict[str, str]] = {}

    def __bool__(self) -> bool:
        return bool(self.rows)

    def transform(self, *objs: Any, via: str = "") -> None:
        culprit, channels = transform_culprit(*objs)
        detail = "; ".join(channels)
        if culprit is not objs[0]:
            detail = f"{detail} (makes {getattr(objs[0], 'name', '?')!r} non-finite)" + (f" {via}" if via else "")
        self._add(culprit, "transform", detail)

    def instances(self, host: Any, source: str, bad: int, total: int) -> None:
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
                except Exception:  # noqa: BLE001
                    file = ""
            self.rows[(name, kind)] = {"object": name, "kind": kind, "detail": detail, "file": file}

    def report(self) -> dict[str, Any]:
        rows = list(self.rows.values())
        kind = GEOMETRY_ERROR if all(r["kind"] == "vertices" for r in rows) else TRANSFORM_ERROR
        shown = [f"{r['object']!r}" + (f" [{r['file']}]" if r["file"] else "") + f": {r['detail']}" for r in rows[:_SHOWN]]
        more = f"; +{len(rows) - _SHOWN} more" if len(rows) > _SHOWN else ""
        msg = (f"{len(rows)} object(s) carry non-finite (NaN/inf) values the scene cannot be measured or drawn with — "
               + "; ".join(shown) + more + f". {_CAUSES}")
        return {"error_type": kind, "error_message": msg, "error_file": next((r["file"] for r in rows if r["file"]), ""),
                "error_line": None, "nonfinite": rows}


def scan_objects(objs: Iterable[Any], dg: Any, offenders: Offenders) -> Offenders:
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
