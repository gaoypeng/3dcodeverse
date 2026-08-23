"""Judge montages: ≤ 2×2 labelled grids built from a ``RenderSet``.

Research finding applied here: VLM judges get position-biased and saturate when
shown many tiles, so every judge image carries **at most four views**.  A judge
call sees, in priority order and capped at ``max_montages``:

1. ``shaded`` primary — the 4 most informative shaded views (3/4 views, top,
   underside for objects; authored cameras for scenes);
2. ``poses`` — the articulation pose sheet (harness-made) or a 2×2 of ``pose_*``
   views, when the render set has them;
3. ``geometry`` — a 2×2 of clay / normals renders (from ``geometry_views`` or from
   non-shaded views inside the set) so holes, inverted faces and intersections
   are visible without shading/material noise;
4. ``shaded`` secondary — the remaining shaded views (then a 2×2 of ``pose_*``
   views when a pose sheet was already shown);

plus 0–2 ``detail`` crops (centre of the best 3/4 view at 2×; the ground-contact
band of the lowest view).  ``shuffle_montages`` permutes montage order *and* tile
order per n-sample so judge noise from position bias averages out.
"""

from __future__ import annotations

import hashlib
import os
import random
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from codeverse.contracts.artifacts import RenderSet, RenderView
from codeverse.judges.images import JudgeImageError, default_cache_dir, view_az_el
from codeverse.spatial.sheet import crop_region, montage_2x2

MontageKind = Literal["shaded", "geometry", "poses", "pose_sheet", "detail"]

#: modes that show geometry without material/lighting noise
GEOMETRY_MODES = ("clay", "normals", "wire", "silhouette", "depth")
#: most-informative-first order for the object rig (names from conventions.OBJECT_VIEWS)
OBJECT_RANK = ("front_right_34", "back_left_34", "top", "low_front_left", "front", "right", "back", "left")
#: scene rig: authored cameras first (graded for composition), then overview rig
SCENE_OVERVIEW_RANK = ("overview_front_right", "overview_back_left", "eye_front", "overview_top", "eye_right", "eye_back_left")
POSE_PREFIX = "pose_"
POSE_SHEET_NAMES = ("articulation_sheet", "pose_sheet")
_POSITIONS = {
    1: ("single",),
    2: ("left", "right"),
    3: ("top-left", "top-right", "bottom-left"),
    4: ("top-left", "top-right", "bottom-left", "bottom-right"),
}
MAX_TILES = 4


@dataclass(frozen=True)
class Montage:
    """One judge image: 1–4 tiles (or a pass-through sheet / a detail crop)."""

    kind: MontageKind
    tiles: tuple[RenderView, ...]
    title: str
    passthrough: bool = False
    crop: tuple[float, float, float, float] | None = None
    hint: str = ""

    @property
    def is_detail(self) -> bool:
        return self.kind == "detail"


# --------------------------------------------------------------------------- view grouping / ranking
def is_pose_view(v: RenderView) -> bool:
    return v.name.startswith(POSE_PREFIX)


def is_pose_sheet(v: RenderView) -> bool:
    return v.name in POSE_SHEET_NAMES


def is_geometry_view(v: RenderView) -> bool:
    return v.mode in GEOMETRY_MODES


def rank_views(views: list[RenderView], *, scene: bool) -> list[RenderView]:
    """Most informative first; unknown names keep their given order after the known ones."""
    if scene:
        def key(iv: tuple[int, RenderView]) -> tuple[int, int, int]:
            i, v = iv
            if v.name.startswith("cam_"):
                return (0, 0, i)
            if v.name in SCENE_OVERVIEW_RANK:
                return (1, SCENE_OVERVIEW_RANK.index(v.name), i)
            return (2, 0, i)
    else:
        def key(iv: tuple[int, RenderView]) -> tuple[int, int, int]:
            i, v = iv
            return (0, OBJECT_RANK.index(v.name), i) if v.name in OBJECT_RANK else (1, 0, i)
    return [v for _, v in sorted(enumerate(views), key=key)]


def _chunks(views: list[RenderView], n: int) -> list[list[RenderView]]:
    return [views[i : i + n] for i in range(0, len(views), n)]


def plan_montages(
    renders: RenderSet,
    *,
    geometry_views: RenderSet | None = None,
    scene: bool = False,
    max_montages: int = 3,
    detail_crops: int = 2,
) -> list[Montage]:
    """Decide which montages a judge call gets (see module docstring for the priority)."""
    shaded = [v for v in renders.views if not (is_pose_view(v) or is_pose_sheet(v) or is_geometry_view(v))]
    poses = [v for v in renders.views if is_pose_view(v)]
    sheets = [v for v in renders.views if is_pose_sheet(v)]
    geometry = [v for v in renders.views if is_geometry_view(v)]
    if geometry_views is not None:
        geometry = list(geometry_views.views) + geometry
    shaded = rank_views(shaded, scene=scene)
    shaded_groups = _chunks(shaded, MAX_TILES)

    candidates: list[Montage] = []
    if shaded_groups:
        candidates.append(Montage("shaded", tuple(shaded_groups[0]), "SHADED views"))
    if sheets:
        candidates.append(Montage("pose_sheet", (sheets[0],), "POSE SHEET (rows = poses, columns = views; tile labels name the joint and its value)", passthrough=True))
    elif poses:
        candidates.append(Montage("poses", tuple(poses[:MAX_TILES]), "POSED views (joints moved by the harness; tile label = pose)"))
    if geometry:
        geo = _match_geometry(geometry, candidates[0].tiles if candidates else ())
        candidates.append(Montage("geometry", tuple(geo), f"GEOMETRY-ONLY views ({geo[0].mode}: no materials — look for holes, black/inverted faces, intersections, gaps)"))
    for extra in shaded_groups[1:]:
        candidates.append(Montage("shaded", tuple(extra), "SHADED views (remaining)"))
    if sheets and poses:  # the sheet already shows the poses; the 2×2 is a larger-tile duplicate, lowest priority
        candidates.append(Montage("poses", tuple(poses[:MAX_TILES]), "POSED views (joints moved by the harness; tile label = pose)"))
    chosen = candidates[: max(0, max_montages)]
    chosen.extend(plan_detail_crops(shaded, scene=scene)[: max(0, detail_crops)])
    return chosen


def _match_geometry(geometry: list[RenderView], primary: tuple[RenderView, ...]) -> list[RenderView]:
    """Prefer geometry views whose names match the primary shaded montage, then the rest."""
    names = [v.name for v in primary]
    by_name = {v.name: v for v in geometry}
    out = [by_name[n] for n in names if n in by_name]
    out += [v for v in geometry if v not in out]
    return out[:MAX_TILES]


def plan_detail_crops(shaded: list[RenderView], *, scene: bool) -> list[Montage]:
    """Centre crop of the best view at 2× (detail legibility) + ground band of the lowest view (objects only)."""
    if not shaded:
        return []
    out = [Montage("detail", (shaded[0],), f"DETAIL CROP — centre of {shaded[0].name} at 2×",
                   crop=(0.25, 0.25, 0.75, 0.75),
                   hint="look for bevels, seams, gaps and surface artefacts; judge detail at this scale")]
    if scene:
        return out
    lowest = min(shaded, key=lambda v: (view_az_el(v) or (0.0, 90.0))[1])
    if lowest is not shaded[0] or len(shaded) == 1:
        out.append(Montage("detail", (lowest,), f"DETAIL CROP — ground-contact band of {lowest.name}",
                           crop=(0.0, 0.55, 1.0, 1.0),
                           hint="look for feet/legs that do not reach the ground and parts hovering above their support"))
    return out


# --------------------------------------------------------------------------- shuffling
def shuffle_montages(montages: list[Montage], seed: int | None) -> list[Montage]:
    """Permute montage order and the tile order inside each grid; detail crops stay last."""
    if seed is None:
        return list(montages)
    rng = random.Random(seed)
    grids = [m for m in montages if not m.is_detail]
    details = [m for m in montages if m.is_detail]
    rng.shuffle(grids)
    out = []
    for m in grids:
        if m.passthrough or len(m.tiles) < 2:
            out.append(m)
            continue
        tiles = list(m.tiles)
        rng.shuffle(tiles)
        out.append(replace(m, tiles=tuple(tiles)))
    return out + details


# --------------------------------------------------------------------------- labels
def tile_label(v: RenderView) -> str:
    bits = [v.name]
    azel = view_az_el(v)
    if azel is not None:
        bits.append(f"az {azel[0]:.0f}° el {azel[1]:.0f}°")
    if v.mode and v.mode != "shaded":
        bits.append(v.mode)
    if v.time_s is not None:
        bits.append(f"t={v.time_s:g}s")
    return " · ".join(bits)


def montage_strip(m: Montage, index: int, total: int) -> str:
    """Short label burnt into the image's top strip (``MONTAGE 2/3 — SHADED views``)."""
    if m.is_detail:
        return f"{m.title} [{index}/{total}]"
    title = m.title.split(" (")[0]
    return f"MONTAGE {index}/{total} — {title}"


def montage_label(m: Montage, index: int, total: int) -> str:
    """Full label for the text part: strip + tile layout (``…: top-left = front_right_34 · az 35° el 22°, …``)."""
    head = montage_strip(m, index, total)
    if m.is_detail:
        return head + (f" — {m.hint}" if m.hint else "")
    if m.passthrough:
        return f"MONTAGE {index}/{total} — {m.title}"
    pos = _POSITIONS[len(m.tiles)]
    return f"MONTAGE {index}/{total} — {m.title}: " + ", ".join(f"{p} = {tile_label(v)}" for p, v in zip(pos, m.tiles, strict=True))


# --------------------------------------------------------------------------- rendering
def _key(parts: list[str]) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _stat(p: str) -> str:
    path = Path(p)
    if not path.is_file():
        raise JudgeImageError(f"judge image missing: {path}")
    st = path.stat()
    return f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}"


def _tmp(out: Path) -> Path:
    return out.with_name(f"{out.stem}.{os.getpid()}-{threading.get_ident()}.tmp.png")


def render_montage(m: Montage, *, cache_dir: Path | None = None, tile_px: int = 512) -> Path:
    """Build (or fetch from cache) the PNG for one montage; pass-through sheets return their source.

    Writes go through a per-writer temp file + atomic replace so concurrent
    judges sharing a cache directory never read a half-written PNG.
    """
    if m.passthrough:
        _stat(m.tiles[0].path)
        return Path(m.tiles[0].path)
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    if m.is_detail:
        v = m.tiles[0]
        out = cache / f"crop_{_key([_stat(v.path), str(m.crop), str(tile_px)])}.png"
        if not out.is_file():
            tmp = crop_region(v.path, _tmp(out), m.crop or (0.0, 0.0, 1.0, 1.0), min_px=tile_px)
            tmp.replace(out)
        return out
    images = [(tile_label(v), v.path) for v in m.tiles]
    out = cache / f"montage_{_key([_stat(p) + '|' + lbl for lbl, p in images] + [str(tile_px)])}.png"
    if not out.is_file():
        tmp = montage_2x2(images, _tmp(out), tile=tile_px)
        tmp.replace(out)
    return out


def describe_montages(montages: list[Montage]) -> str:
    """One line per image for the VIEW RIG paragraph (what each image is, in send order)."""
    grids = [m for m in montages if not m.is_detail]
    details = [m for m in montages if m.is_detail]
    lines = [f"- image {i}: {m.title}" for i, m in enumerate(grids, 1)]
    lines += [f"- detail crop {i}: {m.title.replace('DETAIL CROP — ', '')}" for i, m in enumerate(details, 1)]
    return "\n".join(lines)
