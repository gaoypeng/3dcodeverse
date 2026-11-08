"""What the judge sees: image prep, montage planning and the brief.

``prepare_image`` downscales and labels every image; ``plan_montages`` / ``render_montage`` pack ranked views
into ≤2×2 sheets; ``build_brief`` assembles this round's data for ``judge/rNN/brief.md`` (the case: request, plan
digest, acceptance items, measured facts, gate findings, view rig; the rubric; the image list; the JSON shape).
The generic judge rules live in the plugin's ``agents/3d-judge.md``, not here.
"""

from __future__ import annotations

import hashlib
import json
import math
import typing
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import (
    RENDER_MODES,
    GateFinding,
    GateReport,
    ImprovementItem,
    JudgeIssue,
    Judgment,
    Measurement,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.plan import (
    AcceptanceItem,
    ArticulatedPlan,
    GraphicsPlan,
    Plan,
    ScenePlan,
    StaticPlan,
)
from codeverse3d.contracts.spec import Spec
from codeverse3d.conventions import (
    OBJECT_CLAY_VIEWS,
    OBJECT_VIEWS,
    SCENE_VIEWS,
    ViewPreset,
    to_authoring_frame,
    view_key,
)
from codeverse3d.judges.rubrics import VETO_PENETRATION_DEPTH_M, Rubric
from codeverse3d.proc import unique_tmp
from codeverse3d.spatial.connectivity import PENETRATION_ERROR_M, PENETRATION_WARN_M
from codeverse3d.spatial.measure import measure_summary_table
from codeverse3d.spatial.sheet import crop_region, load_font, montage_2x2

_PRESETS: dict[str, ViewPreset] = {v.name: v for v in (*OBJECT_VIEWS, *SCENE_VIEWS)}
#: clay tiles label their own cameras (``OBJECT_CLAY_VIEWS.top`` is el 88, the rig's is 90), so they resolve here first
_CLAY_PRESETS: dict[str, ViewPreset] = {v.name: v for v in OBJECT_CLAY_VIEWS}


class JudgeImageError(FileNotFoundError):
    """A render referenced by a RenderSet is missing or unreadable."""


def default_cache_dir() -> Path:
    return get_settings().cache_dir / "judge_images"


def view_az_el(view: RenderView) -> tuple[float, float] | None:
    """Azimuth/elevation (deg) of a view: from the preset name, else from camera geometry (Y-up)."""
    if view.mode != "shaded" and view.name in _CLAY_PRESETS:
        p = _CLAY_PRESETS[view.name]
        return p.azimuth_deg, p.elevation_deg
    if view.name in _PRESETS:
        p = _PRESETS[view.name]
        return p.azimuth_deg, p.elevation_deg
    if view.camera_position is not None:
        cx, cy, cz = view.camera_position
        lx, ly, lz = view.look_at or (0.0, 0.0, 0.0)
        dx, dy, dz = cx - lx, cy - ly, cz - lz
        horiz = math.hypot(dx, dz)
        if horiz < 1e-9 and abs(dy) < 1e-9:
            return None
        az = math.degrees(math.atan2(dx, dz)) % 360.0
        el = math.degrees(math.atan2(dy, horiz))
        return round(az, 1), round(el, 1)
    return None


def prepare_image(
    src: str | Path, *, label: str = "", max_px: int = 768, cache_dir: Path | None = None
) -> Path:
    """Downscale ``src`` to ≤ ``max_px`` and burn ``label`` into a top strip; returns the cached PNG path."""
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{_key([_stat(str(src)), str(max_px), label])}.png"
    if out.is_file():
        return out
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_px / max(w, h))
        if scale < 1.0:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        if label:
            im = _with_label_strip(im, label)
        tmp = unique_tmp(out)
        im.save(tmp, format="PNG", optimize=True)
        tmp.replace(out)
    return out


def _with_label_strip(im: Image.Image, label: str) -> Image.Image:
    w, h = im.size
    strip_h = max(22, int(h * 0.055))
    font = load_font(max(12, int(strip_h * 0.62)), bold=True)
    canvas = Image.new("RGB", (w, h + strip_h), (18, 18, 22))
    canvas.paste(im, (0, strip_h))
    draw = ImageDraw.Draw(canvas)
    text = label
    while text and draw.textlength(text, font=font) > w - 12 and len(text) > 8:
        text = text[:-2]
    font_px = getattr(font, "size", 11)
    draw.text((6, max(2, (strip_h - font_px) // 2)), text, fill=(245, 245, 240), font=font)
    return canvas


MontageKind = Literal["shaded", "geometry", "poses", "pose_sheet", "detail"]

GEOMETRY_MODES = tuple(m for m in RENDER_MODES if m != "shaded")
#: most-informative-first order for the object rig; montage 1 includes the underside
OBJECT_RANK = (
    "front_right_high", "back_left_high", "top", "bottom",
    "front", "right", "back", "left",
    "front_right_low", "back_left_low", "front_left_high", "back_right_high",
    "front_left_low", "back_right_low",
)
#: scene rig: authored cameras first, then the overview rig; any name not in ``conventions.SCENE_VIEWS`` is authored
SCENE_OVERVIEW_RANK = ("overview_front_right", "overview_back_left", "overview_top", "overview_low_front",
                       "eye_front", "eye_right", "eye_back_left")
SCENE_RIG_NAMES = frozenset(v.name for v in SCENE_VIEWS) | frozenset(SCENE_OVERVIEW_RANK)
#: the rig tiles a contact crop may be cut from (only the judged ``overview_*`` views)
SCENE_OVERVIEW_NAMES = frozenset(n for n in SCENE_OVERVIEW_RANK if n.startswith("overview"))
PLACEMENT_GATE = "scene_placement"
POSE_PREFIX = "pose_"
POSE_SHEET_NAMES = ("articulation_sheet", "pose_sheet")
_POSITIONS = {
    1: ("single",),
    2: ("left", "right"),
    3: ("top-left", "top-right", "bottom-left"),
    4: ("top-left", "top-right", "bottom-left", "bottom-right"),
}
MAX_TILES = 4
#: payload knobs (defaults; ``Settings.judge`` overrides per run)
MAX_MONTAGES = 5
MAX_DETAIL_CROPS = 2


@dataclass(frozen=True)
class Montage:

    kind: MontageKind
    tiles: tuple[RenderView, ...]
    title: str
    passthrough: bool = False
    crop: tuple[float, float, float, float] | None = None
    hint: str = ""

    @property
    def is_detail(self) -> bool:
        return self.kind == "detail"


def is_pose_view(v: RenderView) -> bool:
    return v.name.startswith(POSE_PREFIX)


def is_pose_sheet(v: RenderView) -> bool:
    return v.name in POSE_SHEET_NAMES


def is_geometry_view(v: RenderView) -> bool:
    return v.mode in GEOMETRY_MODES


def rank_views(views: list[RenderView], *, scene: bool) -> list[RenderView]:
    if scene:
        def key(iv: tuple[int, RenderView]) -> tuple[int, int, int]:
            i, v = iv
            if v.name not in SCENE_RIG_NAMES:
                return (0, 0, i)
            if v.name in SCENE_OVERVIEW_RANK:
                return (1, SCENE_OVERVIEW_RANK.index(v.name), i)
            return (2, 0, i)
    else:
        def key(iv: tuple[int, RenderView]) -> tuple[int, int, int]:
            i, v = iv
            name = view_key(v.name)
            return (0, OBJECT_RANK.index(name), i) if name in OBJECT_RANK else (1, 0, i)
    return [v for _, v in sorted(enumerate(views), key=key)]


def _chunks(views: list[RenderView], n: int) -> list[list[RenderView]]:
    return [views[i : i + n] for i in range(0, len(views), n)]


def plan_montages(
    renders: RenderSet,
    *,
    geometry_views: RenderSet | None = None,
    scene: bool = False,
    max_montages: int = MAX_MONTAGES,
    detail_crops: int = MAX_DETAIL_CROPS,
    gates: Sequence[GateReport] = (),
) -> list[Montage]:
    """Decide which montages a judge call gets; ``gates`` feed a scene's contact crops."""
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
        candidates.append(Montage("poses", tuple(poses[:MAX_TILES]), "POSED views (joints moved by the instruments; tile label = pose)"))
    if geometry:
        geo = _match_geometry(geometry, candidates[0].tiles if candidates else ())
        candidates.append(Montage("geometry", tuple(geo), f"GEOMETRY-ONLY views ({geo[0].mode}: no materials — look for holes, black/inverted faces, intersections, gaps)"))
    for extra in shaded_groups[1:]:
        candidates.append(Montage("shaded", tuple(extra), "SHADED views (remaining)"))
    if sheets and poses:
        candidates.append(Montage("poses", tuple(poses[:MAX_TILES]), "POSED views (joints moved by the instruments; tile label = pose)"))
    chosen = candidates[: max(0, max_montages)]
    chosen.extend(plan_detail_crops(shaded, scene=scene, gates=gates, limit=max(0, detail_crops)))
    return chosen


def _match_geometry(geometry: list[RenderView], primary: tuple[RenderView, ...]) -> list[RenderView]:
    names = [v.name for v in primary]
    by_name = {v.name: v for v in geometry}
    out = [by_name[n] for n in names if n in by_name]
    out += [v for v in geometry if v not in out]
    return out[:MAX_TILES]


def plan_detail_crops(shaded: list[RenderView], *, scene: bool, gates: Sequence[GateReport] = (),
                      limit: int = MAX_DETAIL_CROPS) -> list[Montage]:
    """The crops a verdict sends, ``limit`` = ``Settings.judge.detail_crops``: a centre crop plus a ground band
    for objects, plus contact crops around placement findings for scenes."""
    if not shaded or limit <= 0:
        return []
    centre = Montage("detail", (shaded[0],), f"DETAIL CROP — centre of {shaded[0].name} at 2×",
                     crop=(0.25, 0.25, 0.75, 0.75),
                     hint="look for edge treatment, seams, gaps and surface artefacts at this scale; judge detail here")
    if scene:
        return [centre, *plan_contact_crops(shaded, gates)[:limit]]
    out = [centre]
    lowest = min(shaded, key=lambda v: (view_az_el(v) or (0.0, 90.0))[1])
    if lowest is not shaded[0] or len(shaded) == 1:
        out.append(Montage("detail", (lowest,), f"DETAIL CROP — ground-contact band of {lowest.name}",
                           crop=(0.0, 0.55, 1.0, 1.0),
                           hint="look for feet/legs that do not reach the ground and parts hovering above their support"))
    return out[:limit]


#: a crop smaller than this fraction of the frame (per axis) would over-upscale a tile
CONTACT_CROP_MIN_FRAC = 0.22
#: padding around the projected asset box, as a fraction of the box's size
CONTACT_CROP_PAD = 0.6
CONTACT_CROP_MAX_FRAC = 0.8
#: which rig tile a crop prefers, by elevation: ``(upper bound, weight)`` rows; the first bound above the elevation applies
CONTACT_TILE_WEIGHT: tuple[tuple[float, float], ...] = ((10.0, 1.5), (60.0, 1.0), (90.1, 0.25))
CONTACT_KINDS: dict[str, tuple[str, str]] = {
    "floating": ("gap_m", "{:.2f} m"), "unsupported": ("gap_m", "{:.2f} m"), "cantilevered": ("gap_max_m", "{:.2f} m"),
    "sunken": ("sunk_m", "{:.2f} m"), "interpenetration": ("overlap", "{:.0%}"), "scale": ("factor", "{:g}×"),
}


def _camera_frame(view: RenderView) -> tuple[tuple[float, float, float], ...] | None:
    """``(eye, forward, right, up)`` of a view's camera in the GLB frame, with three.js lookAt roll."""
    if view.camera_position is None or view.look_at is None:
        return None
    eye = tuple(float(c) for c in view.camera_position)
    f = tuple(float(target) - e for target, e in zip(view.look_at, eye, strict=True))
    fl = math.hypot(*f)
    if fl < 1e-9:
        return None
    f = (f[0] / fl, f[1] / fl, f[2] / fl)
    up0 = (0.0, 0.0, -1.0) if abs(f[1]) > 0.9999 else (0.0, 1.0, 0.0)
    r = (f[1] * up0[2] - f[2] * up0[1], f[2] * up0[0] - f[0] * up0[2], f[0] * up0[1] - f[1] * up0[0])
    rl = math.hypot(*r)
    if rl < 1e-9:
        return None
    r = (r[0] / rl, r[1] / rl, r[2] / rl)
    u = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])
    return eye, f, r, u


def _view_aspect(view: RenderView) -> float:
    return view.width / view.height if view.width > 0 and view.height > 0 else 16 / 9


def project_box(view: RenderView, center: Sequence[float], size: Sequence[float]) -> tuple[float, float, float, float] | None:
    """The frame-fraction box ``(left, top, right, bottom)`` a GLB-frame AABB covers in ``view``, or None if
    the camera is unknown, a corner is behind the lens, or the centre is outside the frame."""
    frame = _camera_frame(view)
    if frame is None:
        return None
    eye, f, r, u = frame
    tan_v = math.tan(math.radians(float(view.fov or 50.0)) / 2)
    tan_h = tan_v * _view_aspect(view)
    half = [abs(float(s)) / 2 for s in size]
    pts = [(float(center[0]) + sx * half[0], float(center[1]) + sy * half[1], float(center[2]) + sz * half[2])
           for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    us: list[float] = []
    vs: list[float] = []
    for p in [tuple(float(c) for c in center), *pts]:
        rel = (p[0] - eye[0], p[1] - eye[1], p[2] - eye[2])
        depth = rel[0] * f[0] + rel[1] * f[1] + rel[2] * f[2]
        if depth <= 1e-6:
            return None
        x = (rel[0] * r[0] + rel[1] * r[1] + rel[2] * r[2]) / (depth * tan_h)
        y = (rel[0] * u[0] + rel[1] * u[1] + rel[2] * u[2]) / (depth * tan_v)
        us.append((x + 1) / 2)
        vs.append((1 - y) / 2)
    if not (0.0 <= us[0] <= 1.0 and 0.0 <= vs[0] <= 1.0):
        return None
    return min(us[1:]), min(vs[1:]), max(us[1:]), max(vs[1:])


def _finding_box(f: GateFinding) -> tuple[list[float], list[float]] | None:
    """``(center, size)`` from a placement finding's ``data.bbox``, or None."""
    box = f.data.get("bbox") if isinstance(f.data, dict) else None
    if not isinstance(box, dict):
        return None
    try:
        center = [float(c) for c in box.get("center") or ()]
        size = [float(s) for s in box.get("size") or ()]
    except (TypeError, ValueError):
        return None
    if len(center) != 3 or len(size) != 3 or max(size) <= 0:
        return None
    return center, size


def _contact_label(f: GateFinding) -> str:
    kind = str(f.data.get("kind") or "")
    key, fmt = CONTACT_KINDS.get(kind, ("", ""))
    v = f.data.get(key) if key else None
    mag = fmt.format(float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else ""
    return f"CONTACT CROP — {f.target or 'asset'} (gate: {kind}{' ' + mag if mag else ''})"


def _crop_around(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """A crop window around a projected box: padded, never under the minimum fraction, shifted inside the frame."""
    left, top, right, bottom = box
    cx, cy = (left + right) / 2, (top + bottom) / 2
    hw = max((right - left) * (1 + CONTACT_CROP_PAD) / 2, CONTACT_CROP_MIN_FRAC / 2)
    hh = max((bottom - top) * (1 + CONTACT_CROP_PAD) / 2, CONTACT_CROP_MIN_FRAC / 2)

    def span(c: float, h: float) -> tuple[float, float]:
        lo, hi = c - h, c + h
        if lo < 0:
            hi, lo = min(1.0, hi - lo), 0.0
        if hi > 1:
            lo, hi = max(0.0, lo - (hi - 1)), 1.0
        return round(lo, 4), round(hi, 4)

    x0, x1 = span(cx, hw)
    y0, y1 = span(cy, hh)
    return x0, y0, x1, y1


def placement_findings_for_crops(gates: Sequence[GateReport]) -> list[GateFinding]:
    """The placement findings a contact crop can be cut for: ERROR before WARN, larger first, one per asset."""
    rank = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}
    found: list[GateFinding] = [f for g in gates if g.gate == PLACEMENT_GATE for f in g.findings
                                if f.severity != Severity.INFO and str(f.data.get("kind") or "") in CONTACT_KINDS
                                and _finding_box(f) is not None]

    def magnitude(f: GateFinding) -> float:
        key = CONTACT_KINDS[str(f.data.get("kind"))][0]
        v = f.data.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    out: list[GateFinding] = []
    seen: set[str] = set()
    for f in sorted(found, key=lambda f: (rank[f.severity], -magnitude(f))):
        if (f.target or "") in seen:
            continue
        seen.add(f.target or "")
        out.append(f)
    return out


def _tile_weight(elevation_deg: float) -> float:
    return next(w for bound, w in CONTACT_TILE_WEIGHT if elevation_deg < bound)


def plan_contact_crops(shaded: list[RenderView], gates: Sequence[GateReport]) -> list[Montage]:
    """One crop per placement finding, cut from the judged overview tile that shows the asset largest.
    No box, camera or tile that sees it means no crop."""
    overviews = [v for v in shaded if v.name in SCENE_OVERVIEW_NAMES]
    if not overviews:
        return []
    t0 = min((v.time_s if v.time_s is not None else 0.0) for v in overviews)
    tiles = [v for v in overviews if (v.time_s if v.time_s is not None else 0.0) == t0]
    out: list[Montage] = []
    for f in placement_findings_for_crops(gates):
        box = _finding_box(f)
        if box is None:
            continue
        best: tuple[float, RenderView, tuple[float, float, float, float]] | None = None
        for i, v in enumerate(tiles):
            proj = project_box(v, *box)
            if proj is None:
                continue
            w, h = proj[2] - proj[0], proj[3] - proj[1]
            if max(w, h) > CONTACT_CROP_MAX_FRAC:
                continue
            el = (view_az_el(v) or (0.0, 0.0))[1]
            score = max(w, h) * _tile_weight(el) - i * 1e-6
            if best is None or score > best[0]:
                best = (score, v, proj)
        if best is None:
            continue
        _, view, proj = best
        out.append(Montage("detail", (view,), _contact_label(f), crop=_crop_around(proj),
                           hint=f"diagnostic close-up cut from {view.name} at a placement finding the gate measured: "
                                "judge the CONTACT — does the base meet what is under it, does the part pass through "
                                "its neighbour — not the composition, lighting or dressing"))
    return out


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
    """Full label for the text part: strip + tile layout (``…: top-left = front_right_high · az 45° el 30°, …``)."""
    head = montage_strip(m, index, total)
    if m.is_detail:
        return head + (f" — {m.hint}" if m.hint else "")
    if m.passthrough:
        return f"MONTAGE {index}/{total} — {m.title}"
    pos = _POSITIONS[len(m.tiles)]
    return f"MONTAGE {index}/{total} — {m.title}: " + ", ".join(f"{p} = {tile_label(v)}" for p, v in zip(pos, m.tiles, strict=True))


def _key(parts: list[str]) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _stat(p: str) -> str:
    path = Path(p)
    if not path.is_file():
        raise JudgeImageError(f"judge image missing: {path}")
    st = path.stat()
    return f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}"


def render_montage(m: Montage, *, cache_dir: Path | None = None, tile_px: int = 512) -> Path:
    """Build (or fetch from cache) the PNG for one montage; writes are atomic so concurrent judges share the cache."""
    if m.passthrough:
        _stat(m.tiles[0].path)
        return Path(m.tiles[0].path)
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    if m.is_detail:
        v = m.tiles[0]
        out = cache / f"crop_{_key([_stat(v.path), str(m.crop), str(tile_px)])}.png"
        if not out.is_file():
            tmp = crop_region(v.path, unique_tmp(out), m.crop or (0.0, 0.0, 1.0, 1.0), min_px=tile_px)
            tmp.replace(out)
        return out
    images = [(tile_label(v), v.path) for v in m.tiles]
    out = cache / f"montage_{_key([_stat(p) + '|' + lbl for lbl, p in images] + [str(tile_px)])}.png"
    if not out.is_file():
        tmp = montage_2x2(images, unique_tmp(out), tile=tile_px)
        tmp.replace(out)
    return out


#: tracks whose rounds have one canonical GLB the judge slice channel may cut
SLICE_TRACKS = ("static_object", "articulated_object")
#: rubric for a static object judged against reference images (adds the measured silhouette criterion)
REFERENCE_RUBRIC = "reference"

#: agy truncates a file read at 46 080 bytes: a brief is one read
BRIEF_MAX_BYTES = 40_000
#: the gate facts alone never exceed this (long finding lists end in a count)
GATES_MAX_CHARS = 5_000
MAX_PX = 1024
MONTAGE_TILE_PX = 512
#: STRUCTURE clips: contacts and planned joins are cut with an "… n more"
LEDGER_MAX_CONTACTS = 24
LEDGER_MAX_JOINS = 20
#: parts whose lowest vertex is within this of the object's lowest point are the ground-contact candidates
GROUND_BAND_MM = 10.0
#: a near-floor gap at or under this is a hairline
GROUND_GAP_REPORT_MM = 0.5
#: the order the scene gates are listed in; a gate not named lands in the tail
SCENE_GATE_ORDER: dict[str, int] = {PLACEMENT_GATE: 0, "scene_frames": 1}
#: the per-kind magnitude a PLACEMENT line reports as "worst"
PLACEMENT_WORST: dict[str, tuple[str, str]] = {
    "floating": ("gap_m", "{:.2f} m"), "sunken": ("sunk_m", "{:.2f} m"), "unsupported": ("gap_m", "{:.2f} m"),
    "interpenetration": ("overlap", "{:.0%} of the smaller box"), "cantilevered": ("gap_max_m", "{:.2f} m"),
}


def _rubric_block(rubric: Rubric) -> str:
    lines = [f"RUBRIC {rubric.name} v{rubric.version} (pass threshold {rubric.pass_threshold:.2f}; the overall is computed in code "
             "from these weights, penalties, floors and caps — score each criterion 0..1 against its anchors)"]
    for i, c in enumerate(rubric.criteria, 1):
        if c.kind == "measured":
            lines.append(f"{i}. {c.id} (weight {c.weight:.2f}) — MEASURED IN CODE, shown for calibration; do not score it.")
            continue
        floor = f", hard floor {c.floor:.2f}" if c.floor is not None else ""
        lines.append(f"{i}. {c.id} (weight {c.weight:.2f}{floor}) — {c.label}: {c.description.strip()}")
        # every anchor the rubric declares, richest scale first
        for lvl in sorted(c.anchors, key=lambda k: -float(k)):
            lines.append(f"     {lvl}: {c.anchors[lvl]}")
    if rubric.defects:
        lines.append("")
        lines.append("DEFECT CHECKLIST (binary; answer every id):")
        for d in rubric.defects:
            cost = []
            if d.penalty:
                cost.append(f"-{d.penalty:.2f}")
            if d.cap is not None:
                cost.append(f"cap {d.cap:.2f}")
            lines.append(f"- {d.id}: {d.text.strip()}" + (f"  [{', '.join(cost)}]" if cost else ""))
    if rubric.extra_instructions.strip():
        lines.append("")
        lines.append("RUBRIC NOTES: " + rubric.extra_instructions.strip())
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 12].rstrip() + " …[clipped]"


def _more(n: int, shown: int, sep: str = ";") -> str:
    return f"{sep} … {n - shown} more" if n > shown else ""


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def brief_section(spec: Spec) -> str:
    c = spec.constraints
    rows = [("dimensions_m", ", ".join(f"{k}={v:g}" for k, v in c.dimensions_m.items())), ("style", c.style),
            ("must_have", "; ".join(c.must_have)), ("must_not", "; ".join(c.must_not))]
    return "\n".join([f"BRIEF (track {spec.track.value}, language {spec.language.value}): {_clip(spec.prompt, 3000)}",
                      *(f"- {k}: {v}" for k, v in rows if v)])


def acceptance_section(items: list[AcceptanceItem]) -> str:
    if not items:
        return "ACCEPTANCE ITEMS: (none)"
    return "\n".join(["ACCEPTANCE ITEMS:", *(f"- {a.id} [{a.priority}, via {a.how}]: {_clip(a.text, 240)}" for a in items[:40])]) \
        + _more(len(items), 40, "\n-")


def measurement_section(m: Measurement | None) -> str:
    return "MEASUREMENTS (Y-up, metres):\n" + _clip(str(measure_summary_table(m)), 6000) if m is not None else "MEASUREMENTS: (none)"


def contact_ledger(gates: list[GateReport]) -> tuple[GateReport, GateFinding] | None:
    """The connectivity gate's per-pair table (one INFO finding) with its report; ``None`` if absent."""
    return next(((g, f) for g in gates if g.gate == "connectivity" for f in g.findings
                 if f.severity == Severity.INFO and f.data.get("kind") == "ledger"), None)


def gates_section(gates: list[GateReport], *, max_errors: int = 12, max_warns: int = 8, scene: bool = False) -> str:
    """The judge's gate facts: the pass/FAIL roll, ERRORs as recorded, the STRUCTURE (object) or PLACEMENT (scene)
    fact list, and the WARNs grouped by (gate, kind) — with a contact ledger its overlap line replaces the
    connectivity penetration WARNs."""
    if not gates:
        return "GATES: (none run)"
    ordered = sorted(gates, key=lambda g: SCENE_GATE_ORDER.get(g.gate, len(SCENE_GATE_ORDER))) if scene else gates
    lines = ["GATES (measured facts): " + ", ".join(f"{g.gate} {'pass' if g.passed else 'FAIL'}" for g in ordered)]
    errs = [_finding_line(g, f) for g in ordered for f in g.findings if f.severity == Severity.ERROR]
    lines += _capped("errors", errs, max_errors)
    found = None if scene else contact_ledger(gates)
    lines += placement_block(gates) if scene else _structure_block(*found) if found else []
    warns = [(g, f) for g in ordered for f in g.findings if f.severity == Severity.WARN
             and not (found and g.gate == "connectivity" and f.data.get("kind") == "penetration")]
    lines += _grouped_warnings(warns, max_warns)
    if not errs and not any(f.severity == Severity.WARN for g in gates for f in g.findings):
        lines.append("no errors, no warnings")
    return "\n".join(lines)


def placement_block(gates: Sequence[GateReport]) -> list[str]:
    """PLACEMENT: the scene_placement gate's counts with the worst magnitude per kind, its thresholds, the verdict."""
    rep = next((g for g in gates if g.gate == PLACEMENT_GATE), None)
    summary = next((f for f in rep.findings if f.severity == Severity.INFO and f.data.get("kind") == "summary"), None) if rep else None
    if rep is None or summary is None:
        return []
    d = summary.data
    counts = {k: int(v) for k, v in (d.get("counts") or {}).items() if _num(v) is not None}
    exempt = {str(k): int(v) for k, v in (d.get("exempt") or {}).items() if _num(v) is not None}
    worst: dict[str, float] = {}
    for f in rep.findings:
        kind = str(f.data.get("kind") or "")
        v = _num(f.data.get(PLACEMENT_WORST[kind][0])) if f.severity != Severity.INFO and kind in PLACEMENT_WORST else None
        if v is not None:
            worst[kind] = max(worst.get(kind, 0.0), v)
    kinds = [k for k in PLACEMENT_WORST if k in counts] + [k for k in worst if k not in counts]
    tally = [f"{counts.get(k, 0) or sum(1 for f in rep.findings if f.data.get('kind') == k)} "
             f"{'interpenetrating' if k == 'interpenetration' else k}" + (f" (worst {PLACEMENT_WORST[k][1].format(worst[k])})" if k in worst else "")
             for k in kinds]
    lines = [f"PLACEMENT (scene_placement: foot columns against what is under each asset, box overlap per pair): "
             f"{int(d.get('checked') or 0)} of {int(d.get('total') or 0)} placed assets checked, {sum(exempt.values())} exempt "
             f"({exempt.get('free', 0)} tagged free): {', '.join(tally)}"]
    if any(f.data.get("kind") == "free_exempt" for f in rep.findings):
        lines.append("- free_exempt: the share of assets tagged placement='free' is over the line (see the finding)")
    thresholds = d.get("thresholds")
    if isinstance(thresholds, dict) and thresholds:
        lines.append("- thresholds: " + ", ".join(f"{k} {t:g}" for k, v in sorted(thresholds.items()) if (t := _num(v)) is not None))
    if rep.passed and not any(counts.get(k, 0) for k in kinds) and not worst:
        lines.append("- verdict: PLACEMENT PASSED — no checked asset floats, sinks or overlaps beyond the thresholds; a dark seam "
                     "under a base is contact: floating_asset / sunken_asset / interpenetration only where a render shows it")
    return lines


def connectivity_error_pairs(gates: list[GateReport]) -> list[tuple[str, str]]:
    """The connectivity gate's ERROR-level penetration part pairs (the slice channel's hatch targets): ``target``
    against ``data.other``, falling back to ``data.entering`` / ``data.container``."""
    pairs = {frozenset((str(a), str(b))) for g in gates if g.gate == "connectivity" for f in g.findings
             if f.severity == Severity.ERROR and f.data.get("kind") == "penetration"
             for a, b in [(f.target or f.data.get("entering"), f.data.get("other") or f.data.get("container"))] if a and b and a != b}
    return [tuple(sorted(p)) for p in sorted(pairs, key=sorted)]


def _finding_line(g: GateReport, f: GateFinding) -> str:
    return f"- {g.gate}{f' [{f.target}]' if f.target else ''}: {_clip(f.message, 220)}"


def _capped(label: str, items: list[str], cap: int) -> list[str]:
    return [f"{label} ({len(items)}):", *items[:cap], *([f"- … {len(items) - cap} more {label}"] if len(items) > cap else [])] if items else []


def _gap(mm: float) -> str:
    """A contact's gap: nothing when it rounds to 0.0 mm, else the number."""
    return f" {mm:.1f}" if round(mm, 1) else ""


def _structure_block(conn: GateReport, ledger: GateFinding) -> list[str]:
    """STRUCTURE: parts / contacts / floating / overlaps, planned joins, other contacts, floor gaps, overlaps, verdict."""
    d = ledger.data
    parts, contacts, over = list(d.get("parts") or []), list(d.get("contacts") or []), list(d.get("overlaps") or [])
    gap = float(d.get("contact_gap_mm") or 0.0)
    floating = sum(1 for f in conn.findings if f.severity == Severity.ERROR and f.data.get("kind") == "floating")
    lines = [f"STRUCTURE (connectivity gate, mesh-to-mesh distances): {len(parts)} part{'s' if len(parts) != 1 else ''}, "
             f"{len(contacts)} contacts (gap ≤ {gap:g} mm), {floating} floating, {len(over)} overlapping pairs"]
    planned = [(str(a), str(b), float(g), str(st)) for a, b, g, st in (d.get("planned") or [])]
    open_rows = [r for r in planned if r[3] != "contact"][:LEDGER_MAX_JOINS]
    joined = [r for r in planned if r[3] == "contact"][: LEDGER_MAX_JOINS - len(open_rows)]
    if planned:  # OPEN joins first so the clip never hides one
        lines.append(f"- planned joins: {len(planned) - sum(r[3] != 'contact' for r in planned)}/{len(planned)} in contact")
        if open_rows:
            lines.append("- OPEN joins: " + "; ".join(f"{a}→{b} {g:.1f} mm" for a, b, g, _st in open_rows))
        if joined:
            lines.append("- joined (gap mm where not 0): " + ", ".join(f"{a}→{b}{_gap(g)}" for a, b, g, _st in joined))
        lines[-1] += _more(len(planned), len(open_rows) + len(joined))
    planned_pairs = {frozenset((a, b)) for a, b, _g, st in planned if st == "contact"}
    other = [c for c in contacts if frozenset((str(c[0]), str(c[1]))) not in planned_pairs]
    if other:
        by_a: dict[str, list[str]] = {}
        for a, b, g in other[:LEDGER_MAX_CONTACTS]:
            by_a.setdefault(str(a), []).append(f"{b}{_gap(float(g))}")
        lines.append(f"- {'other contacts, not in the plan' if planned_pairs else 'contacts'} (gap mm where not 0): "
                     + " · ".join(f"{a}: {', '.join(bs)}" for a, bs in by_a.items()) + _more(len(other), LEDGER_MAX_CONTACTS, " ·"))
    unresolved = list(d.get("planned_unresolved") or [])
    if unresolved:
        lines.append(f"- plan parts not found in the mesh: {', '.join(unresolved[:8])}" + (" …" if len(unresolved) > 8 else ""))
    if ground := {str(n): float(g) for n, g in (d.get("ground_gap_mm") or {}).items()}:
        low_name, low = min(ground.items(), key=lambda kv: kv[1])
        verdict = "nothing touches the floor" if low > GROUND_GAP_REPORT_MM else "below the floor" if low < -GROUND_GAP_REPORT_MM else "floor contact"
        up = [(n, g) for n, g in sorted(ground.items(), key=lambda kv: kv[1]) if g - low <= GROUND_BAND_MM and g > GROUND_GAP_REPORT_MM]
        lines.append(f"- ground: lowest point {low:.1f} mm ({low_name}) — {verdict}; " + (
            f"near-floor parts (within {GROUND_BAND_MM:g} mm of it) above {GROUND_GAP_REPORT_MM:g} mm: "
            + ", ".join(f"{n} {g:.1f} mm" for n, g in up[:8]) + (" …" if len(up) > 8 else "")
            if up else f"every other near-floor part ≤ {GROUND_GAP_REPORT_MM:g} mm"))
    lines += _overlap_lines(conn, over)
    if conn.passed and len(parts) >= 2:
        lines.append(_passed_line(len(parts), gap, over))
    return lines


def _overlap_lines(conn: GateReport, over: list) -> list[str]:
    """The measured overlaps: welds under the ERROR line, pairs over it kept WARN (a sliver), welds reaching the
    partner's mid-plane.  Classified by measured depth, never by severity."""
    if not over:
        return []
    error_pairs = {(f.target, f.data.get("other")) for f in conn.findings
                   if f.severity == Severity.ERROR and f.data.get("kind") == "penetration"}
    rest = [r for r in over if (str(r[0]), str(r[1])) not in error_pairs]
    err_mm, warn_mm = PENETRATION_ERROR_M * 1000, PENETRATION_WARN_M * 1000
    deep = [r for r in rest if float(r[2]) > err_mm]
    welds = [r for r in rest if float(r[2]) <= err_mm]
    text = (f"- overlaps: {len(over)} measured; weld allowance {warn_mm:g} mm, ERROR line {err_mm:g} mm; "
            f"{len(error_pairs)} pair(s) above it (under errors); {len(welds)} weld(s) under it (not the interpenetration defect)")
    if welds:
        a, b, depth, through = max(welds, key=lambda r: float(r[2]))
        text += f"; deepest weld {a}/{b} {float(depth):.1f} mm (through-ratio {float(through):.2f})"
    lines = [text]
    if deep:
        lines.append(f"- {len(deep)} overlap(s) over the ERROR line kept WARN (only a sliver of either surface inside; the GEOMETRY "
                     "montage decides): " + "; ".join(f"{a}/{b} {float(dp):.1f} mm (through-ratio {float(t):.2f})" for a, b, dp, t in deep[:3])
                     + _more(len(deep), 3))
    if mid := [r for r in welds if float(r[3]) >= 0.5]:
        lines.append("- welds reaching ≥ 50% of the way to the partner's mid-plane (measured, not a defect claim; the far side is on "
                     "the GEOMETRY montage): " + "; ".join(f"{a}/{b} {min(float(t), 1.0):.0%} ({float(dp):.1f} mm)" for a, b, dp, t in mid[:3])
                     + _more(len(mid), 3))
    return lines


def _passed_line(n_parts: int, gap_mm: float, overlaps: list) -> str:
    # the interpenetration verdict is given only when the veto honours it (every overlap under VETO_PENETRATION_DEPTH_M)
    veto_mm = VETO_PENETRATION_DEPTH_M * 1000
    deep = [r for r in overlaps if float(r[2]) >= veto_mm]
    text = (f"- verdict: CONNECTIVITY PASSED — all {n_parts} parts in measured contact (gap ≤ {gap_mm:g} mm); "
            "a dark seam at a join is contact, not a floating part")
    if not deep:
        return text + f"; all {len(overlaps)} overlaps under {veto_mm:g} mm are welds, not interpenetration"
    return text + (f"; {len(deep)} of {len(overlaps)} overlaps reach {veto_mm:g} mm or more ("
                   + "; ".join(f"{a}/{b} {float(dp):.1f} mm" for a, b, dp, _t in deep[:3]) + _more(len(deep), 3)
                   + "): interpenetration only where a render shows a part passing through another")


def _grouped_warnings(rest: list[tuple[GateReport, GateFinding]], max_warns: int) -> list[str]:
    """One line per (gate, kind), ``max_warns`` findings each."""
    groups: dict[str, list[GateFinding]] = {}
    for g, f in rest:
        kind = f.data.get("kind") if isinstance(f.data.get("kind"), str) else ""
        groups.setdefault(f"{g.gate}/{kind}" if kind else g.gate, []).append(f)
    return [f"warnings ({len(rest)}) by kind:", *(
        f"- {key} ×{len(fs)}: " + "; ".join((f"[{f.target}] " if f.target else "") + _clip(f.message, 220) for f in fs[:max_warns])
        + _more(len(fs), max_warns) for key, fs in groups.items())] if rest else []


def previous_section(prev: Judgment | None, round_index: int) -> str:
    if prev is None:
        return ""
    return "\n".join([
        f"PREVIOUS VERDICT (round {max(round_index - 1, 0)}): overall {prev.overall:.2f}, "
        f"{'passed' if prev.passed else 'not passed'}; the builder was asked to:",
        *([f"- [{it.target}/{it.kind}] {_clip(it.instruction, 200)}" for it in prev.improvement_plan[:6]] or ["- (no plan items)"]),
        "Score this round on its own; say in the summary which of those items are fixed, still present or regressed.",
    ])


def view_rig_section(renders: RenderSet, montages: list[Montage], *, scene: bool) -> str:
    n_grids = sum(1 for m in montages if not m.is_detail)
    kinds = {m.kind for m in montages}
    lines = [f"VIEW RIG: {n_grids} montage(s)" + (f" + {len(montages) - n_grids} detail crop(s)" if len(montages) > n_grids else "")
             + " — a montage is a ≤2×2 grid; its label strip names the view at top-left / top-right / bottom-left / bottom-right; "
             "tile label = 'name · az/el[· mode][· t=]'",
             "- az 0° = the FRONT of the object, counter-clockwise seen from above (90° = its right side, 180° = back); "
             "el = degrees above the horizon (negative = from below the ground plane: undersides, ground contact)"]
    if "geometry" in kinds:
        lines.append("- GEOMETRY-ONLY montage: the same object without materials or lighting (holes, inverted black faces, "
                     "intersections, floating parts); SHADED montage: materials and detail")
    if kinds & {"poses", "pose_sheet"}:
        lines.append("- POSE tiles: the same object with joints moved by the instruments (label = joint@value or rest); "
                     "judge articulation only from them and the joint table")
    if not scene:
        lines.append("- every view shows the same object; top + low views: symmetry, footprint, ground contact")
    if renders.console_errors:
        lines.append(f"- PROBE: {len(renders.console_errors)} console error(s) during rendering, first: {_clip(renders.console_errors[0], 200)}")
    return "\n".join(lines)


def slice_rig_section(labels: list[str]) -> str:
    """The slice block appended to the view rig on gate-ERROR rounds: what a slice shows, what it cannot show, the list."""
    return "\n".join([
        f"SLICES ({len(labels)}, after the crops): cuts on two centre planes; one colour per part (legend on the image); "
        "red hatch = overlap the connectivity gate measured at ERROR level",
        "- a gap IN THE CUT PLANE is not disconnection (parts may join outside it; STRUCTURE above is authoritative); "
        "floating_part and holes_or_inverted_faces are judged on the shaded and geometry views, which see the whole surface",
        *(f"- slice {i}: {lbl}" for i, lbl in enumerate(labels, 1)),
    ])


def plan_summary(plan: Plan | None, language: str) -> str:
    """The plan digest the judge reads.  An object's size is written W×H×D in the GLB frame (Y-up),
    so a Z-up plan's (W, D, H) is permuted first."""
    if isinstance(plan, GraphicsPlan):
        return (f"{plan.title}: {plan.summary} Style: {plan.style}. Passes: {', '.join(f'{p.name} ({p.kind})' for p in plan.passes)}. "
                f"Key visuals: {'; '.join(plan.key_visuals)}. Motion: {plan.motion or '(none planned)'}. "
                f"{plan.resolution[0]}x{plan.resolution[1]}, loop {plan.duration_s:g}s.")
    if isinstance(plan, ScenePlan):
        return (f"{plan.title}: {plan.summary} Setting: {plan.setting}. Zones: {', '.join(z.name for z in plan.zones)}. "
                f"Assets: {', '.join(a.name for a in plan.assets)}. Cameras: {', '.join(c.name for c in plan.cameras)}.")
    if not isinstance(plan, StaticPlan):
        return ""
    e = to_authoring_frame(plan.overall_bbox.extents, language, extents=True)
    text = (f"{plan.object_name}: {plan.summary} Overall {e[0]:.2f}×{e[1]:.2f}×{e[2]:.2f} m (W×H×D). "
            f"Parts: {', '.join(f'{p.name}×{p.instances}' if p.instances > 1 else p.name for p in plan.parts)}.")
    if isinstance(plan, ArticulatedPlan):
        text += (f" Root link {plan.root_link}. Joints: "
                 + "; ".join(f"{j.name} ({j.type} {j.parent}→{j.child}, [{j.lower:.2f},{j.upper:.2f}])" for j in plan.joints) + ".")
    return text


def montage_images(
    renders: RenderSet,
    *,
    geometry_views: RenderSet | None = None,
    scene: bool = False,
    max_montages: int = MAX_MONTAGES,
    detail_crops: int = MAX_DETAIL_CROPS,
    max_px: int = MAX_PX,
    cache_dir: Path | None = None,
    gates: Sequence[GateReport] = (),
) -> tuple[list[tuple[str, Path]], list[Montage]]:
    """``(label, labelled PNG)`` pairs for the montages of a render set, plus the montage plan in send order."""
    montages = plan_montages(renders, geometry_views=geometry_views, scene=scene, max_montages=max_montages,
                             detail_crops=detail_crops, gates=gates)
    grids = [m for m in montages if not m.is_detail]
    details = [m for m in montages if m.is_detail]
    out: list[tuple[str, Path]] = []
    for i, m in enumerate(grids, 1):
        lbl, strip = montage_label(m, i, len(grids)), montage_strip(m, i, len(grids))
        png = render_montage(m, cache_dir=cache_dir, tile_px=MONTAGE_TILE_PX)
        out.append((lbl, prepare_image(png, label=strip, max_px=max_px, cache_dir=cache_dir)))
    for i, m in enumerate(details, 1):
        lbl, strip = montage_label(m, i, len(details)), montage_strip(m, i, len(details))
        png = render_montage(m, cache_dir=cache_dir, tile_px=MONTAGE_TILE_PX)
        out.append((lbl, prepare_image(png, label=strip, max_px=min(max_px, 768), cache_dir=cache_dir)))
    return out, montages


def _literal_values(model: type, field: str) -> tuple[str, ...]:
    return tuple(str(v) for v in typing.get_args(model.model_fields[field].annotation))


def _bound(model: type, field: str, attr: str) -> float:
    return next(float(getattr(m, attr)) for m in model.model_fields[field].metadata if hasattr(m, attr))


EVIDENCE = "<MONTAGE k <position> (<view>) / DETAIL CROP k / the measurement that shows it>"


def judgment_shape(rubric: Rubric, acceptance_ids: list[str], judgment_path: Path) -> str:
    """The JSON the judge writes: a filled example with every criterion / defect / acceptance id, then the enum
    lists and ranges the parser holds it to (``rubrics.repair_judgment`` mends anything smaller)."""
    example = {
        "summary": "<2-4 sentences: what it is, what is right, what is most wrong>",
        "issues": [{"target": "<name from the plan digest, or overall>", "kind": "geometry", "severity": "major",
                    "detail": "<what is wrong, observable>", "evidence": EVIDENCE}],
        "defects": {d.id: {"present": False, "evidence": "<evidence>"} for d in rubric.defects},
        "acceptance": {a: {"verified": False, "evidence": "<evidence>"} for a in acceptance_ids},
        "criteria": {c.id: {"score": 0.0, "evidence": "<evidence>"} for c in rubric.visual_criteria()},
        "improvement_plan": [{"target": "<name from the plan digest>", "kind": "geometry",
                              "instruction": "<one imperative change and its expected effect>", "priority": 1, "expected_gain": 0.05}],
    }
    if not rubric.defects:
        example.pop("defects")
    if not acceptance_ids:
        example.pop("acceptance")
    lo, hi = int(_bound(ImprovementItem, "priority", "ge")), int(_bound(ImprovementItem, "priority", "le"))
    rules = [
        f"Write ONE JSON object of this shape to `{judgment_path}` (nothing else in the file). Every id shown under "
        "criteria, defects and acceptance must be answered; a criterion the rubric marks MEASURED is not listed and not yours.",
        "- criteria[].score: 0..1 against the anchors; evidence names the image and tile or the measurement.",
        "- defects[].present: true only when an image, a gate ERROR or a measurement shows it (a WARN never does).",
        "- acceptance[].verified: true only when a render or a measurement proves the item.",
        f"- issues[].kind ∈ {', '.join(_literal_values(JudgeIssue, 'kind'))}; severity ∈ {', '.join(_literal_values(JudgeIssue, 'severity'))}; most severe first.",
        f"- improvement_plan: at most {hi} items, priority {lo} first ({lo}..{hi}); kind ∈ {', '.join(_literal_values(ImprovementItem, 'kind'))}; expected_gain 0..1.",
        "- Do not compute an overall or a pass/fail: code does that from the rubric.",
    ]
    return "\n".join(rules) + "\n\n```json\n" + _shape_json(example) + "\n```\n"


def _shape_json(example: dict) -> str:
    """``json.dumps(indent=1)`` with every id entry of a keyed object on one line (a filled example, not a dump)."""
    lines = ["{"]
    keys = list(example)
    for i, k in enumerate(keys):
        v, comma = example[k], ("," if i < len(keys) - 1 else "")
        if isinstance(v, dict):
            lines.append(f' "{k}": {{')
            items = list(v.items())
            lines += [f'  "{id_}": {json.dumps(body, ensure_ascii=False)}' + ("," if j < len(items) - 1 else "")
                      for j, (id_, body) in enumerate(items)]
            lines.append(" }" + comma)
        else:
            lines.append(f' "{k}": ' + json.dumps(v, indent=1, ensure_ascii=False).replace("\n", "\n ") + comma)
    lines.append("}")
    return "\n".join(lines)


def _clip_bytes(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` UTF-8 bytes, ending in the clip mark when cut."""
    if len(text.encode()) <= limit:
        return text
    mark = " …[clipped]"
    body = text.encode()[: max(0, limit - len(mark.encode()))].decode(errors="ignore").rstrip()
    return body + mark


def build_brief(
    *,
    spec: Spec,
    plan: Plan | None,
    rubric: Rubric,
    renders: RenderSet,
    measurement: Measurement | None,
    gates: list[GateReport],
    round_index: int,
    previous: Judgment | None,
    extra_context: str,
    extra_text: str,
    images: list[tuple[str, Path]],
    montages: list[Montage],
    extra_images: Sequence[tuple[str, Path]] = (),
    slice_images: Sequence[tuple[str, Path]] = (),
    judgment_path: Path,
) -> str:
    """The whole judge brief as Markdown, under :data:`BRIEF_MAX_BYTES`: the case (request, plan digest, acceptance
    items, measurements, gate facts, previous verdict, view rig), the rubric, every image with its label and absolute
    path in viewing order, and the JSON shape to write to ``judgment_path``.  The case is what gives way when the
    budget is tight; the rubric, the image list and the shape never do.  Every case section is a fact list: one line
    per fact, numbers and verdicts; the judge's rules are the plugin's ``agents/3d-judge.md`` and the rubric block."""
    is_scene = spec.track.value == "scene"
    sections = [
        brief_section(spec),
        "PLAN DIGEST: " + (_clip(plan_summary(plan, spec.language.value), 5000) if plan is not None else "(none)"),
        acceptance_section(list(getattr(plan, "acceptance", None) or [])),
        measurement_section(measurement),
        _clip(gates_section(gates, scene=is_scene), GATES_MAX_CHARS),
        previous_section(previous, round_index),
        _clip(extra_context, 2500) if extra_context.strip() else "",
        _clip(extra_text, 2500) if extra_text.strip() else "",
    ]
    rig = view_rig_section(renders, montages, scene=is_scene)
    if slice_images:
        rig = rig + "\n" + slice_rig_section([lbl for lbl, _ in slice_images])
    sections.append(rig)
    case = "\n\n".join(s for s in sections if s)
    acceptance_ids = [a.id for a in (getattr(plan, "acceptance", None) or [])]
    listing = "\n".join(f"{i}. {lbl}\n   {p}" for i, (lbl, p) in enumerate([*extra_images, *images, *slice_images], 1))
    head = f"# Judge brief — round {round_index}\n\n## The case\n\n"
    tail = ("\n\n## Rubric\n\n" + _rubric_block(rubric)
            + "\n\n## Images (open each with your image viewer, in this order)\n\n" + listing
            + "\n\n## Return exactly this JSON\n\n" + judgment_shape(rubric, acceptance_ids, judgment_path))
    room = BRIEF_MAX_BYTES - len(head.encode()) - len(tail.encode())
    return head + _clip_bytes(case, room) + tail
