"""What the judge sees: image prep, montage planning and the brief.

``prepare_image`` downscales and labels every image; ``plan_montages`` / ``render_montage`` pack ranked views
into ≤2×2 sheets; ``build_brief`` assembles the judge's text (the role, the rubric as a checklist, the measured
facts, the view rig, the exact JSON to return) for ``judge/rNN/brief.md``.
"""

from __future__ import annotations

import hashlib
import json
import math
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
from codeverse3d.judges.rubrics import VETO_PENETRATION_DEPTH_M, Rubric, wire_schema
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


def describe_montages(montages: list[Montage]) -> str:
    grids = [m for m in montages if not m.is_detail]
    details = [m for m in montages if m.is_detail]
    lines = [f"- image {i}: {m.title}" for i, m in enumerate(grids, 1)]
    lines += [f"- detail crop {i}: {m.title.replace('DETAIL CROP — ', '')}" for i, m in enumerate(details, 1)]
    return "\n".join(lines)


#: tracks whose rounds have one canonical GLB the judge slice channel may cut
SLICE_TRACKS = ("static_object", "articulated_object")
#: rubric for a static object judged against reference images (adds the measured silhouette criterion)
REFERENCE_RUBRIC = "reference"

TEXT_BUDGET_CHARS = 24_000
MAX_PX = 1024
MONTAGE_TILE_PX = 512
SLICE_MAX_PX = 1024

_ROLE = """You are the BLIND JUDGE of a 3D-code project: exacting but fair.
You see only the brief, a plan digest, measured numbers, deterministic gate findings and labelled renders of the result — never the builder's code or reasoning (you read ONLY this brief and the images it lists). Judge what is visible and measured; do not invent faults and do not credit what you cannot see.
Gate findings describe the exported FILE (names, node hierarchy, contacts); the renders show the actual geometry. When they seem to disagree about something VISIBLE (a part moves in a posed tile, a part is present), trust the images for the visual criteria and report the file-level fact as an issue — never infer "nothing moves" or "part missing" from text alone when the tiles show otherwise.
Work in this order: observe (summary, strengths, issues), answer the defect checklist and the acceptance items, and only THEN score the criteria against their anchors.

SCORING RULES
- Score each criterion 0..1 against its anchors (interpolate between anchors). Use the WHOLE range: competent work sits at 0.8+, one clearly visible major defect pulls the affected criterion to ~0.4, broken work sits at 0.1-0.3. Do not compress everything into 0.5-0.7 — a primitive box-stack and a crafted product must be 0.4 apart, not 0.1.
- Every score needs evidence that cites the image and tile (e.g. "MONTAGE 1 top-left (front_right_high): rear leg ends 3 cm above ground; measurement ground_gap 0.03").
- DEFECT CHECKLIST: answer EVERY item with present=true/false. true ONLY when the defect is visible in an image, or a gate finding of severity ERROR / a measurement states it; gate WARNINGS (e.g. a few-mm weld overlap) are informational and never make a defect present. Cite where. These answers drive penalties and caps computed in code, so be literal: do not mark a defect to "be safe", and do not hide one to be kind.
- Do NOT compute an overall or decide pass/fail; `3dcode score` computes the weighted overall, subtracts defect penalties, applies floors and caps.
- Issues: observable defects, most severe first, with target = the part / zone / joint / asset name from the plan digest (or "overall"), a kind, a severity and evidence.
- Improvement plan: at most 6 concrete, imperative instructions for the builder ("rear_leg_left: ends 3 cm above y=0 in the ground-contact crop — extend it to touch the ground"; "seat: measured 62 mm thick against the brief's 25 mm — thin it to the stated size"), priority 1 first, each with a target name from the plan digest and an expected_gain estimate. Give items even for passing work if a named change would raise the score; leave empty only when nothing would.
- Acceptance items: answer verified=true ONLY when the renders or measurements prove the item; otherwise false with what is missing.
- Write ONE JSON object matching the schema at the end of this brief to the judgment file it names; no prose outside it."""


#: end of the DEFECT CHECKLIST scoring-rule bullet in ``_ROLE``; the provenance sentence is appended after it
_DEFECT_BULLET_END = 'do not mark a defect to "be safe", and do not hide one to be kind.'
#: one neutral, defect-agnostic sentence for slice (gate-ERROR) rounds
PROVENANCE_ELICITATION = (
    "For each defect you mark present, say where it is visible (MONTAGE n / "
    "DETAIL CROP n / slice n) or state that it rests on the measured text alone."
)


def _rubric_block(rubric: Rubric) -> str:
    lines = [f"RUBRIC {rubric.name} v{rubric.version} (pass threshold {rubric.pass_threshold:.2f}, for information only)"]
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


def build_system_prompt(rubric: Rubric, *, provenance_elicitation: bool = False) -> str:
    """The judge's system prompt; ``provenance_elicitation`` appends :data:`PROVENANCE_ELICITATION` once."""
    role = _ROLE
    if provenance_elicitation:
        if role.count(_DEFECT_BULLET_END) != 1:
            raise ValueError("prompt_builder._ROLE: the defect-checklist bullet anchor drifted")
        role = role.replace(_DEFECT_BULLET_END, _DEFECT_BULLET_END + " " + PROVENANCE_ELICITATION)
    return role + "\n\n" + _rubric_block(rubric)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 12].rstrip() + " …[clipped]"


def brief_section(spec: Spec) -> str:
    lines = [f"BRIEF (track {spec.track.value}, language {spec.language.value}):", _clip(spec.prompt, 3000)]
    c = spec.constraints
    cons = []
    if c.dimensions_m:
        cons.append("dimensions_m: " + ", ".join(f"{k}={v:g}" for k, v in c.dimensions_m.items()))
    if c.style:
        cons.append(f"style: {c.style}")
    if c.must_have:
        cons.append("must_have: " + "; ".join(c.must_have))
    if c.must_not:
        cons.append("must_not: " + "; ".join(c.must_not))
    if cons:
        lines.append("CONSTRAINTS: " + " | ".join(cons))
    return "\n".join(lines)


def acceptance_section(items: list[AcceptanceItem]) -> str:
    if not items:
        return "ACCEPTANCE CHECKLIST: (none)"
    lines = ["ACCEPTANCE CHECKLIST (answer every id):"]
    for a in items[:40]:
        lines.append(f"- {a.id} [{a.priority}, via {a.how}]: {_clip(a.text, 240)}")
    if len(items) > 40:
        lines.append(f"- … {len(items) - 40} more items omitted")
    return "\n".join(lines)


def measurement_section(m: Measurement | None) -> str:
    if m is None:
        return "MEASUREMENTS: (none available)"
    return "MEASUREMENTS (measured, Y-up meters):\n" + _clip(str(measure_summary_table(m)), 6000)


#: MEASURED STRUCTURE clips: contacts and planned joins are cut with an "… n more"
LEDGER_MAX_CONTACTS = 24
LEDGER_MAX_JOINS = 20
#: parts whose lowest vertex is within this of the object's lowest point are the ground-contact candidates
GROUND_BAND_MM = 10.0
#: a near-floor gap at or under this is a hairline: the number is printed instead of the adjective
GROUND_GAP_REPORT_MM = 0.5

_PASSED_FLOATING = (
    "CONNECTIVITY PASSED: every part is in measured contact with its neighbour (gap <= 2 mm). "
    "A dark seam or shadow line where two parts meet is contact, not daylight. Do NOT report any "
    "part as floating, hovering or disconnected, and do not mark the floating-part defect present; "
    "if a joint looks visually ugly, say so under craftsmanship."
)


def contact_ledger(gates: list[GateReport]) -> tuple[GateReport, GateFinding] | None:
    """The connectivity gate's per-pair table (one INFO finding) with its report; ``None`` if absent."""
    for g in gates:
        if g.gate != "connectivity":
            continue
        for f in g.findings:
            if f.severity == Severity.INFO and f.data.get("kind") == "ledger":
                return g, f
    return None


def gates_section(gates: list[GateReport], *, max_errors: int = 12, max_warns: int = 8, scene: bool = False) -> str:
    """The judge's gate facts.  ERRORs are listed as recorded; with a contact ledger the connectivity WARNs
    become one measured line plus a MEASURED STRUCTURE block, without one the plain text.  ``scene`` has
    its own branch (``_scene_gates_section``)."""
    if not gates:
        return "GATE FINDINGS: (no gates run)"
    if scene:
        return _scene_gates_section(gates, max_errors=max_errors, max_warns=max_warns)
    status = ", ".join(f"{g.gate}={'pass' if g.passed else 'FAIL'}" for g in gates)
    lines = [f"GATE FINDINGS (deterministic; treat as facts): {status}"]
    errs = [_finding_line(g, f) for g in gates for f in g.findings if f.severity == Severity.ERROR]
    found = contact_ledger(gates)
    if found is None:
        warns = [_finding_line(g, f) for g in gates for f in g.findings if f.severity == Severity.WARN]
        lines += _capped("errors", errs, max_errors) + _capped("warnings", warns, max_warns)
        if not errs and not warns:
            lines.append("no errors or warnings — parts are connected and contracts hold.")
        if any(g.gate == "connectivity" and g.passed for g in gates):
            # the connectivity gate measures contact; a VLM reads shading, so a measured contact outranks a shadow
            lines.append(_PASSED_FLOATING)
        return "\n".join(lines)
    conn, ledger = found
    d = ledger.data
    lines += _capped("errors", errs, max_errors)
    lines += _structure_block(conn, d)
    overlap = _overlap_line(conn, d)
    if overlap:
        lines.append(overlap)
    rest = [(g, f) for g in gates for f in g.findings
            if f.severity == Severity.WARN and not (g.gate == "connectivity" and f.data.get("kind") == "penetration")]
    lines += _grouped_warnings(rest, max_warns)
    if not errs and not any(f.severity == Severity.WARN for g in gates for f in g.findings):
        lines.append("no errors or warnings — parts are connected and contracts hold.")
    n_parts = len(d.get("parts") or [])
    if conn.passed and n_parts >= 2:
        lines.append(_passed_paragraph(n_parts, len(d.get("contacts") or []), float(d.get("contact_gap_mm") or 0.0),
                                       list(d.get("overlaps") or [])))
    return "\n".join(lines)


#: the order the scene gates are listed in; a gate not named lands in the tail
SCENE_GATE_ORDER: dict[str, int] = {PLACEMENT_GATE: 0, "scene_frames": 1}
#: the per-kind magnitude a MEASURED PLACEMENT line reports as "worst"
PLACEMENT_WORST: dict[str, tuple[str, str]] = {
    "floating": ("gap_m", "{:.2f} m"), "sunken": ("sunk_m", "{:.2f} m"), "unsupported": ("gap_m", "{:.2f} m"),
    "interpenetration": ("overlap", "{:.0%} of the smaller box"), "cantilevered": ("gap_max_m", "{:.2f} m"),
}


def _scene_gates_section(gates: list[GateReport], *, max_errors: int, max_warns: int) -> str:
    """The scene judge's gate facts: reports in ``SCENE_GATE_ORDER``, ERRORs as recorded, one MEASURED
    PLACEMENT line, and the WARNs grouped by (gate, kind)."""
    ordered = sorted(gates, key=lambda g: SCENE_GATE_ORDER.get(g.gate, len(SCENE_GATE_ORDER)))
    status = ", ".join(f"{g.gate}={'pass' if g.passed else 'FAIL'}" for g in ordered)
    lines = [f"GATE FINDINGS (deterministic; treat as facts): {status}"]
    errs = [_finding_line(g, f) for g in ordered for f in g.findings if f.severity == Severity.ERROR]
    lines += _capped("errors", errs, max_errors)
    block = placement_block(gates)
    if block:
        lines.append(block)
    warns = [(g, f) for g in ordered for f in g.findings if f.severity == Severity.WARN]
    lines += _grouped_warnings(warns, max_warns)
    if not errs and not warns:
        lines.append("no errors or warnings — every checked asset is seated, the frames are exposed and the contracts hold.")
    return "\n".join(lines)


def placement_report(gates: Sequence[GateReport]) -> GateReport | None:
    return next((g for g in gates if g.gate == PLACEMENT_GATE), None)


def placement_block(gates: Sequence[GateReport]) -> str:
    """MEASURED PLACEMENT: the scene_placement gate's summary with the worst magnitude per kind, its thresholds,
    and on a clean report the sentence naming the checklist items already answered.  Empty without the gate."""
    rep = placement_report(gates)
    if rep is None:
        return ""
    summary = next((f for f in rep.findings if f.severity == Severity.INFO and f.data.get("kind") == "summary"), None)
    if summary is None:
        return ""
    d = summary.data
    counts = {k: int(v) for k, v in (d.get("counts") or {}).items() if isinstance(v, (int, float))}
    exempt = {str(k): int(v) for k, v in (d.get("exempt") or {}).items() if isinstance(v, (int, float))}
    worst: dict[str, float] = {}
    for f in rep.findings:
        kind = str(f.data.get("kind") or "")
        if f.severity == Severity.INFO or kind not in PLACEMENT_WORST:
            continue
        v = f.data.get(PLACEMENT_WORST[kind][0])
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            worst[kind] = max(worst.get(kind, 0.0), float(v))
    free = sum(1 for g in gates if g.gate == PLACEMENT_GATE for f in g.findings if f.data.get("kind") == "free_exempt")
    head = (f"MEASURED PLACEMENT (scene_placement gate: every placed asset's foot columns against what is under it, "
            f"every pair's box overlap): {int(d.get('checked') or 0)} assets checked of {int(d.get('total') or 0)} placed, "
            f"{sum(exempt.values())} exempt ({exempt.get('free', 0)} tagged free)")
    kinds = [k for k in PLACEMENT_WORST if k in counts] + [k for k in worst if k not in counts]
    parts = []
    for kind in kinds:
        text = f"{counts.get(kind, 0) or sum(1 for f in rep.findings if f.data.get('kind') == kind)} "
        text += "interpenetrating" if kind == "interpenetration" else kind
        if kind in worst:
            text += f" (worst {PLACEMENT_WORST[kind][1].format(worst[kind])})"
        parts.append(text)
    head += ": " + ", ".join(parts) + "."
    if free:
        head += " The share of assets tagged placement='free' is over the line (see the free_exempt finding): an author hiding from the gate."
    thresholds = d.get("thresholds")
    if isinstance(thresholds, dict) and thresholds:
        head += " Thresholds: " + ", ".join(f"{k} {float(v):g}" for k, v in sorted(thresholds.items())
                                             if isinstance(v, (int, float)) and not isinstance(v, bool)) + "."
    if rep.passed and not any(counts.get(k, 0) for k in kinds) and not worst:
        head += (" PLACEMENT PASSED: no checked asset floats, sinks or overlaps another beyond the thresholds. A dark seam "
                 "under a base is contact, not daylight: mark floating_asset / sunken_asset / interpenetration present only "
                 "where a render VISIBLY shows it, and name the view.")
    return head


def connectivity_error_pairs(gates: list[GateReport]) -> list[tuple[str, str]]:
    """The connectivity gate's ERROR-level penetration part pairs (the slice channel's hatch targets): ``target``
    against ``data.other``, falling back to ``data.entering`` / ``data.container``."""
    pairs: set[frozenset[str]] = set()
    for g in gates:
        if g.gate != "connectivity":
            continue
        for f in g.findings:
            if f.severity != Severity.ERROR or f.data.get("kind") != "penetration":
                continue
            a = f.target or f.data.get("entering")
            b = f.data.get("other") or f.data.get("container")
            if a and b and a != b:
                pairs.add(frozenset((str(a), str(b))))
    return [tuple(sorted(p)) for p in sorted(pairs, key=sorted)]


def _finding_line(g: GateReport, f: GateFinding) -> str:
    tgt = f" [{f.target}]" if f.target else ""
    return f"- {g.gate}{tgt}: {_clip(f.message, 220)}"


def _capped(label: str, items: list[str], cap: int) -> list[str]:
    if not items:
        return []
    out = [f"{label} ({len(items)}):", *items[:cap]]
    if len(items) > cap:
        out.append(f"- … {len(items) - cap} more {label}")
    return out


def _structure_block(conn: GateReport, d: dict) -> list[str]:
    """N parts / E contacts / floating / overlaps, the contact graph, planned joins, and floor gaps."""
    parts = list(d.get("parts") or [])
    contacts = list(d.get("contacts") or [])
    gap = float(d.get("contact_gap_mm") or 0.0)
    floating = sum(1 for f in conn.findings if f.severity == Severity.ERROR and f.data.get("kind") == "floating")
    lines = [
        f"MEASURED STRUCTURE (connectivity gate, exact mesh-to-mesh distances): {len(parts)} part{'s' if len(parts) != 1 else ''}, "
        f"{len(contacts)} contacts (gap ≤ {gap:g} mm), {floating} floating, {len(d.get('overlaps') or [])} overlapping pairs."
    ]
    planned = list(d.get("planned") or [])
    if planned:
        lines.append("- " + _planned_line(planned, gap))
    planned_pairs = {frozenset((str(a), str(b))) for a, b, _g, st in planned if st == "contact"}
    other = [c for c in contacts if frozenset((str(c[0]), str(c[1]))) not in planned_pairs]
    if other:
        label = "other contacts, not in the plan" if planned_pairs else "contacts"
        lines.append(f"- {label} (gap in mm where not 0): " + _adjacency(other, LEDGER_MAX_CONTACTS))
    unresolved = list(d.get("planned_unresolved") or [])
    if unresolved:
        lines.append(f"- plan parts not found in the mesh: {', '.join(unresolved[:8])}" + (" …" if len(unresolved) > 8 else ""))
    ground = _ground_line(d.get("ground_gap_mm") or {})
    if ground:
        lines.append("- " + ground)
    return lines


def _gap(mm: float) -> str:
    """A contact's gap: nothing when it rounds to 0.0 mm, else the number."""
    return f" {mm:.1f}" if round(mm, 1) else ""


def _adjacency(contacts: list, cap: int) -> str:
    by_a: dict[str, list[str]] = {}
    for a, b, gap in contacts[:cap]:
        by_a.setdefault(str(a), []).append(f"{b}{_gap(float(gap))}")
    text = " · ".join(f"{a}: {', '.join(bs)}" for a, bs in by_a.items())
    if len(contacts) > cap:
        text += f" · … {len(contacts) - cap} more"
    return text


def _planned_line(planned: list, gap_mm: float) -> str:
    """OPEN joins first so the clip never hides one."""
    rows = [(str(a), str(b), float(g), str(st)) for a, b, g, st in planned]
    open_rows = [r for r in rows if r[3] != "contact"]
    contact_rows = [r for r in rows if r[3] == "contact"]
    shown = (open_rows + contact_rows)[:LEDGER_MAX_JOINS]
    bits = [f"PLANNED JOINS (the plan's attach_to pairs, measured; contact = gap ≤ {gap_mm:g} mm): "
            f"{len(contact_rows)}/{len(rows)} in contact."]
    if any(r[3] != "contact" for r in shown):
        bits.append("OPEN: " + "; ".join(f"{a}→{b} {g:.1f} mm" for a, b, g, st in shown if st != "contact") + ".")
    if any(r[3] == "contact" for r in shown):
        bits.append("CONTACT (gap in mm where not 0): " + ", ".join(f"{a}→{b}{_gap(g)}" for a, b, g, st in shown if st == "contact") + ".")
    if len(rows) > len(shown):
        bits.append(f"… {len(rows) - len(shown)} more.")
    return " ".join(bits)


def _ground_line(ground: dict) -> str:
    if not ground:
        return ""
    low_name, low = min(((str(n), float(g)) for n, g in ground.items()), key=lambda kv: kv[1])
    band = [(str(n), float(g)) for n, g in ground.items() if float(g) - low <= GROUND_BAND_MM]
    up = [(n, g) for n, g in band if g > GROUND_GAP_REPORT_MM]
    if low > GROUND_GAP_REPORT_MM:
        head = f"ground: lowest point {low:.1f} mm above the floor ({low_name}) — nothing touches it"
    elif low < -GROUND_GAP_REPORT_MM:
        head = f"ground: lowest point {-low:.1f} mm below the floor ({low_name})"
    else:
        head = f"ground: lowest point {low:.1f} mm ({low_name}) — floor contact"
    if up:
        head += (f"; parts within {GROUND_BAND_MM:g} mm of the floor: "
                 + ", ".join(f"{n} {g:.1f} mm" for n, g in up[:8]) + (" …" if len(up) > 8 else ""))
    else:
        head += f"; every other near-floor part is within {GROUND_GAP_REPORT_MM:g} mm of the floor"
    return head


def _overlap_line(conn: GateReport, d: dict) -> str:
    """The measured replacement for the interpenetration WARN prose: overlapping pairs are welds by the gate's
    rule; a pair reaching half-way through its partner is named as a measurement."""
    over = list(d.get("overlaps") or [])
    if not over:
        return ""
    error_pairs = {(f.target, f.data.get("other")) for f in conn.findings
                   if f.severity == Severity.ERROR and f.data.get("kind") == "penetration"}
    rest = [r for r in over if (str(r[0]), str(r[1])) not in error_pairs]
    # classified by measured depth, never by severity (a deep overlap can still be a WARN)
    deep = [r for r in rest if float(r[2]) > PENETRATION_ERROR_M * 1000]
    welds = [r for r in rest if float(r[2]) <= PENETRATION_ERROR_M * 1000]
    text = f"connectivity measured {len(over)} mating overlaps"
    if welds:
        a, b, depth, through = max(welds, key=lambda r: float(r[2]))
        text += f" (deepest weld {float(depth):.1f} mm {a}/{b}, through-ratio {float(through):.2f})"
    text += f"; weld allowance {PENETRATION_WARN_M * 1000:g} mm, ERROR line {PENETRATION_ERROR_M * 1000:g} mm; "
    if error_pairs:
        text += f"{len(error_pairs)} pair(s) above it are listed under errors; "
    text += f"{len(welds)} weld(s) under it are NOT the interpenetration defect."
    if deep:
        text += (f" {len(deep)} overlap(s) deeper than the ERROR line kept WARN because only a sliver of either "
                 "surface is inside — a continuous member through a slab reads like this; the montage decides: "
                 + "; ".join(f"{a}/{b} {float(dp):.1f} mm (through-ratio {float(t):.2f})" for a, b, dp, t in deep[:3])
                 + ("" if len(deep) <= 3 else f"; … {len(deep) - 3} more") + ".")
    mid = [r for r in welds if float(r[3]) >= 0.5]
    if mid:
        text += (" Measured, not a defect claim: "
                 + "; ".join(f"{a}/{b} reaches {min(float(t), 1.0):.0%} of the way to its partner's mid-plane ({float(dp):.1f} mm)"
                             for a, b, dp, t in mid[:3])
                 + " — the far side is for the GEOMETRY montage.")
    return text


def _grouped_warnings(rest: list[tuple[GateReport, GateFinding]], max_warns: int) -> list[str]:
    """One line per (gate, kind), ``max_warns`` findings each."""
    if not rest:
        return []
    groups: dict[str, list[GateFinding]] = {}
    for g, f in rest:
        kind = f.data.get("kind") if isinstance(f.data.get("kind"), str) else ""
        groups.setdefault(f"{g.gate}/{kind}" if kind else g.gate, []).append(f)
    lines = [f"warnings ({len(rest)}), grouped by kind:"]
    for key, fs in groups.items():
        items = [(f"[{f.target}] " if f.target else "") + _clip(f.message, 160) for f in fs[:max_warns]]
        line = f"- {key} ×{len(fs)}: " + "; ".join(items)
        if len(fs) > max_warns:
            line += f"; … {len(fs) - max_warns} more"
        lines.append(line)
    return lines


def _passed_paragraph(n_parts: int, n_contacts: int, gap_mm: float, overlaps: list) -> str:
    # the interpenetration injunction is made only when the veto honours it (every overlap under VETO_PENETRATION_DEPTH_M)
    head = (
        f"CONNECTIVITY PASSED: all {n_parts} parts are in measured contact ({n_contacts} contacts, gap <= {gap_mm:g} mm). "
        "A dark seam or shadow line where two parts meet is contact, not daylight. Do NOT report any "
        "part as floating, hovering or disconnected, and do not mark the floating-part defect present. "
    )
    deep = [r for r in overlaps if float(r[2]) >= VETO_PENETRATION_DEPTH_M * 1000]
    if not deep:
        tail = (f"All {len(overlaps)} overlaps measured, none over {VETO_PENETRATION_DEPTH_M * 1000:g} mm: do NOT mark "
                "the interpenetration defect present either — those overlaps are welds. ")
    else:
        tail = (f"All {len(overlaps)} overlaps measured; {len(deep)} reach {VETO_PENETRATION_DEPTH_M * 1000:g} mm or more ("
                + "; ".join(f"{a}/{b} {float(dp):.1f} mm" for a, b, dp, _t in deep[:3])
                + ("" if len(deep) <= 3 else f"; … {len(deep) - 3} more")
                + "): mark interpenetration only if a part VISIBLY passes through another in a render; "
                "a hidden overlap is a weld. ")
    return head + tail + "If a joint looks visually ugly, say so under craftsmanship."


def previous_section(prev: Judgment | None, round_index: int) -> str:
    if prev is None:
        return ""
    lines = [
        f"PREVIOUS VERDICT (round {max(round_index - 1, 0)}): overall {prev.overall:.2f}, "
        f"{'passed' if prev.passed else 'not passed'}. The builder was then asked to:"
    ]
    for it in prev.improvement_plan[:6]:
        lines.append(f"- [{it.target}/{it.kind}] {_clip(it.instruction, 200)}")
    if not prev.improvement_plan:
        lines.append("- (no plan items)")
    lines.append(
        "Judge THIS round on its own merits (do not anchor on the old numbers), but say in the summary which of "
        "those items are now fixed, which are STILL present, and whether anything regressed."
    )
    return "\n".join(lines)


#: the fixed sentences of the view-rig paragraph, constants so ``judge_prompt_hash`` covers them
RIG_RULES: dict[str, str] = {
    "grid": (
        "Each montage is a ≤2×2 grid; every tile carries a label under it ('name · az/el[· mode][· t=]') and the "
        "montage's own label strip lists which view sits top-left / top-right / bottom-left / bottom-right. "
        "Cite evidence as 'MONTAGE k <position> (<view name>)'. Detail crops are zoomed regions of a view, labelled with what to look for."
    ),
    "azimuth": (
        "Azimuth 0° = looking at the FRONT of the object, increasing counter-clockwise seen from above (90° = the object's right side, 180° = back); "
        "elevation is degrees above the horizon (negative = looking up from below the ground plane, which reveals undersides and ground contact)."
    ),
    "geometry": "The GEOMETRY-ONLY montage shows the same object without materials/lighting: use it for holes, inverted (black) faces, intersections and floating parts; use the SHADED montage for materials and detail.",
    "poses": "POSE tiles show the SAME object with joints moved by the instruments (tile label = joint@value or rest). Judge articulation only from them and the joint table.",
    "scene_cams": "Views named overview_* and eye_* are rig cameras fitted to the scene bounds (layout X-ray); every other view is one of the scene's own authored cameras (grade composition/lighting on those); 't=' is the animation time.",
    "scene_craft": "On the authored cameras also read the CRAFT of the picture, not only its contents: depth readable for what each shot frames, surfaces that read as the brief's materials rather than one flat colour, variety among repeated elements, the secondary detail the brief's place implies, and distance softened where the brief's weather would soften it — each judged against what THIS place would have, not a fixed recipe. The same camera at two times appears as separate tiles; nothing_moves is answered from the MEASURED MOTION line only, never by comparing tiles.",
    "scene_contact": "The high overview_* tiles (overview_front_right / overview_back_left / overview_top) are rendered with the fog removed and room ceilings lifted: a missing roof, lid or haze in them is never a defect. overview_low_front stands at ground level just outside the content, where daylight under a base shows. CONTACT CROP images are diagnostic close-ups cut from an overview tile around a placement finding the gate measured (the label names the asset, the kind and the number): judge the contact they show — base meets ground, part through neighbour — never their composition, lighting or dressing. Every floating / sunken / clipping / scale claim must name its view and its asset.",
    "object": "All views show the same object. Use top + low views for symmetry, footprint and ground contact.",
}


def view_rig_section(renders: RenderSet, montages: list[Montage], *, scene: bool) -> str:
    n_grids = sum(1 for m in montages if not m.is_detail)
    n_detail = len(montages) - n_grids
    bits = [
        f"VIEW RIG: {n_grids} MONTAGE image(s) follow" + (f" plus {n_detail} DETAIL CROP(s)" if n_detail else "") + ". "
        + RIG_RULES["grid"],
        RIG_RULES["azimuth"],
    ]
    if any(m.kind == "geometry" for m in montages):
        bits.append(RIG_RULES["geometry"])
    if any(m.kind in ("poses", "pose_sheet") for m in montages):
        bits.append(RIG_RULES["poses"])
    if scene:
        bits.append(RIG_RULES["scene_cams"])
        bits.append(RIG_RULES["scene_craft"])
        bits.append(RIG_RULES["scene_contact"])
    else:
        bits.append(RIG_RULES["object"])
    if renders.console_errors:
        bits.append(f"PROBE: {len(renders.console_errors)} console error(s) during rendering, first: {_clip(renders.console_errors[0], 200)}")
    bits.append("Images in send order:\n" + describe_montages(montages))
    return "\n".join(bits)


def slice_rig_section(labels: list[str]) -> str:
    """The slice block appended to the view rig on gate-ERROR rounds: what the slices are, the anti-over-read
    sentence, and the slice list in send order."""
    lines = "\n".join(f"- slice {i}: {lbl}" for i, lbl in enumerate(labels, 1))
    return (f"After the crops, {len(labels)} cross-section slice(s) show the interior: "
            "slices cut the object on two centre planes; each part keeps one color "
            "(legend on the image); regions the connectivity gate measured as ERROR-level "
            "overlap are hatched red. "
            "A gap between parts IN THE CUT PLANE is not evidence of disconnection — parts "
            "may join outside this plane; the measured structure block is authoritative for "
            "connectivity.  Judge floating_part and holes_or_inverted_faces from the shaded "
            "and geometry views, which see the whole surface; a slice shows one cut only.\n"
            + lines)


def plan_summary(plan: Plan | None, language: str) -> str:
    """The plan digest the judge reads.  An object's size is written W×H×D in the GLB frame (Y-up),
    so a Z-up plan's (W, D, H) is permuted first."""
    if isinstance(plan, GraphicsPlan):
        passes = ", ".join(f"{p.name} ({p.kind})" for p in plan.passes)
        return (f"{plan.title}: {plan.summary} Style: {plan.style}. Passes: {passes}. Key visuals: {'; '.join(plan.key_visuals)}. "
                f"Motion: {plan.motion or '(none planned)'}. {plan.resolution[0]}x{plan.resolution[1]}, loop {plan.duration_s:g}s.")
    if isinstance(plan, ScenePlan):
        return (f"{plan.title}: {plan.summary} Setting: {plan.setting}. Zones: {', '.join(z.name for z in plan.zones)}. "
                f"Assets: {', '.join(a.name for a in plan.assets)}. Cameras: {', '.join(c.name for c in plan.cameras)}.")
    if not isinstance(plan, StaticPlan):
        return ""
    parts = ", ".join(f"{p.name}×{p.instances}" if p.instances > 1 else p.name for p in plan.parts)
    e = to_authoring_frame(plan.overall_bbox.extents, language, extents=True)
    text = f"{plan.object_name}: {plan.summary} Overall {e[0]:.2f}×{e[1]:.2f}×{e[2]:.2f} m (W×H×D). Parts: {parts}."
    if isinstance(plan, ArticulatedPlan):
        joints = "; ".join(f"{j.name} ({j.type} {j.parent}→{j.child}, [{j.lower:.2f},{j.upper:.2f}])" for j in plan.joints)
        text += f" Root link {plan.root_link}. Joints: {joints}."
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
    geometry_views: RenderSet | None,
    images: list[tuple[str, Path]],
    montages: list[Montage],
    extra_images: Sequence[tuple[str, Path]] = (),
    slice_images: Sequence[tuple[str, Path]] = (),
    judgment_path: Path,
) -> str:
    """The whole judge brief as Markdown: role + rubric, the measured sections, the view rig, every image with its
    label and absolute path (in viewing order), and the exact JSON object to write to ``judgment_path``."""
    is_scene = spec.track.value == "scene"
    system = build_system_prompt(rubric, provenance_elicitation=bool(slice_images))
    sections = [
        brief_section(spec),
        ("PLAN DIGEST:\n" + _clip(plan_summary(plan, spec.language.value), 5000)) if plan is not None else "PLAN DIGEST: (none)",
        acceptance_section(list(getattr(plan, "acceptance", None) or [])),
        measurement_section(measurement),
        gates_section(gates, scene=is_scene),
        previous_section(previous, round_index),
        _clip(extra_context, 2500) if extra_context.strip() else "",
        _clip(extra_text, 2500) if extra_text.strip() else "",
    ]
    rig = view_rig_section(renders, montages, scene=is_scene)
    if slice_images:
        rig = rig + "\n" + slice_rig_section([lbl for lbl, _ in slice_images])
    sections.append(rig)
    text = "\n\n".join(s for s in sections if s)
    if len(text) > TEXT_BUDGET_CHARS:
        text = _clip(text, TEXT_BUDGET_CHARS)
    acceptance_ids = [a.id for a in (getattr(plan, "acceptance", None) or [])]
    schema = json.dumps(wire_schema(rubric, acceptance_ids), indent=1)
    listing = "\n".join(f"{i}. {lbl}\n   {p}" for i, (lbl, p) in enumerate([*extra_images, *images, *slice_images], 1))
    return (
        f"# Judge brief — round {round_index}\n\n"
        "## Your role\n\n" + system + "\n\n"
        "## The case\n\n" + text + "\n\n"
        "## Images (open each with your image viewer, in this order)\n\n" + listing + "\n\n"
        "## Return exactly this JSON\n\n"
        f"Write ONE JSON object matching this schema to `{judgment_path}` (nothing else in the file). Score every "
        "criterion with evidence, answer every defect-checklist item and every acceptance item.\n\n"
        "```json\n" + schema + "\n```\n"
    )
