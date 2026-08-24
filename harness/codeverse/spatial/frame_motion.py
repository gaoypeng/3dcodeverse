"""Deterministic inter-frame MOTION for scene renders.

``render_scene`` photographs every camera at ``times`` (default t = 0 s and
t = 1.5 s).  Whether anything actually moved between those frames is a
*measurement*, not a perception task: a VLM comparing two tiles that live in
different montage images cannot see a 2 % pixel change, and every scene judged
so far was told "nothing moves" while the water and the foliage were in fact
animating.  So the harness measures it here, in code, and hands the judge the
number (law 3: deterministic gates/measurements run by the harness, the VLM only
perceives).

``motion_rows(metrics, out_dir)`` diffs the first and last rendered frame of
each camera and returns one ``MotionRow`` per camera;
``motion_summary_text(rows)`` renders the table that goes into the judge's
context and into the ``scene_frames`` gate.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

#: per-channel 0..255 difference above which a pixel counts as changed
PIXEL_DELTA = 8
#: share of changed pixels at (or above) which a camera counts as showing motion
MOVING_FRAC = 0.004
#: a change this strong on even a few pixels (a lit window, a spark) is motion too
STRONG_DELTA = 60
STRONG_FRAC = 0.0008


@dataclass(frozen=True)
class MotionRow:
    """Measured change between the first and last frame of ONE camera."""

    name: str
    kind: str
    t0: float
    t1: float
    changed_frac: float
    strong_frac: float
    max_delta: int
    mean_delta: float

    @property
    def moving(self) -> bool:
        return self.changed_frac >= MOVING_FRAC or self.strong_frac >= STRONG_FRAC

    @property
    def authored(self) -> bool:
        return self.kind == "authored"

    def as_line(self) -> str:
        return (f"{self.name} [{self.kind}]: {self.changed_frac:.1%} of pixels changed between "
                f"t={self.t0:g}s and t={self.t1:g}s (max Δ {self.max_delta}/255) — "
                + ("MOVING" if self.moving else "STATIC"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "t0": self.t0, "t1": self.t1,
            "changed_frac": round(self.changed_frac, 5), "strong_frac": round(self.strong_frac, 5),
            "max_delta": self.max_delta, "mean_delta": round(self.mean_delta, 3), "moving": self.moving,
        }


def _load(path: Path) -> np.ndarray | None:
    try:
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.int16)
    except (OSError, ValueError):
        return None


def _pair_rows(entries: Sequence[dict[str, Any]], out_dir: Path) -> list[MotionRow]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for v in entries:
        if not isinstance(v, dict) or v.get("kind") == "counterfactual" or "_nocustom" in str(v.get("name", "")):
            continue
        by_name.setdefault(str(v.get("name", "?")), []).append(v)
    rows: list[MotionRow] = []
    for name, views in by_name.items():
        timed = sorted(views, key=lambda v: float(v.get("time_s") or 0.0))
        if len(timed) < 2:
            continue
        first, last = timed[0], timed[-1]
        t0, t1 = float(first.get("time_s") or 0.0), float(last.get("time_s") or 0.0)
        if t1 <= t0:
            continue
        a, b = _load(out_dir / str(first.get("path", ""))), _load(out_dir / str(last.get("path", "")))
        if a is None or b is None or a.shape != b.shape:
            continue
        diff = np.abs(a - b).max(axis=2)
        rows.append(MotionRow(
            name=name, kind=str(first.get("kind") or "authored"), t0=t0, t1=t1,
            changed_frac=float((diff > PIXEL_DELTA).mean()),
            strong_frac=float((diff > STRONG_DELTA).mean()),
            max_delta=int(diff.max()), mean_delta=float(diff.mean()),
        ))
    order = {str(v.get("name")): i for i, v in enumerate(entries)}
    rows.sort(key=lambda r: (r.kind != "authored", order.get(r.name, 999)))
    return rows


def motion_rows(metrics: dict[str, Any], out_dir: Path | str) -> list[MotionRow]:
    """One row per camera rendered at ≥ 2 times (authored cameras first).

    ``metrics`` is a ``render_scene`` metrics payload; the view paths inside it
    are relative to ``out_dir``.  Unreadable or single-time views are skipped —
    a missing row means "not measured", never "static"."""
    entries = metrics.get("views") or []
    if not isinstance(entries, list):
        return []
    return _pair_rows(entries, Path(out_dir))


def scene_moves(rows: Sequence[MotionRow]) -> bool | None:
    """Did anything move on an AUTHORED camera?  ``None`` = not measured."""
    authored = [r for r in rows if r.authored]
    if not authored:
        return None
    return any(r.moving for r in authored)


def motion_summary_text(rows: Sequence[MotionRow], *, max_rows: int = 10) -> str:
    """The table the judge and the refine prompts read (empty string = nothing measured)."""
    if not rows:
        return ""
    moves = scene_moves(rows)
    shown = [r for r in rows if r.authored] or list(rows)
    head = ("MEASURED MOTION (harness pixel diff of the authored cameras between the first and last animation "
            "time; deterministic, so treat it as a FACT — the two times sit in different montage images and are "
            "not reliably comparable by eye):")
    lines = [head, *[f"- {r.as_line()}" for r in shown[:max_rows]]]
    if moves is True:
        movers = [r.name for r in shown if r.moving]
        lines.append(f"→ the scene IS animating ({', '.join(movers[:5])} change between the samples): the defect "
                     "`nothing_moves` is FALSE, and an acceptance item that asks only for visible movement between "
                     "t0 and t1 is verified by these numbers.")
    elif moves is False:
        lines.append("→ no authored camera changed by more than a rounding error: the scene is frozen, "
                     "`nothing_moves` is TRUE and no animation acceptance item is verified.")
    return "\n".join(lines)


def motion_from_dir(out_dir: Path | str) -> list[MotionRow]:
    """Convenience: read ``<out_dir>/metrics.json`` and measure it."""
    d = Path(out_dir)
    try:
        metrics = json.loads((d / "metrics.json").read_text())
    except (OSError, ValueError):
        return []
    return motion_rows(metrics, d)
