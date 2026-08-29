"""Judge image preparation: downscale, label strips, caching, view geometry.

Every image sent to a judge goes through ``prepare_image`` so the token cost is
bounded (≤ ``max_px`` on the long side) and every view carries a burnt-in label
(``VIEW 3/8 — front · az 0° el 8°``) that the model can cite as evidence.
Prepared files are cached under ``<cache_dir>/<sha>.png`` keyed by source path,
mtime, size, max_px and label text.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PIL import Image, ImageDraw

from codeverse.config import get_settings
from codeverse.contracts.artifacts import (
    GateReport,
    Judgment,
    Measurement,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse.contracts.chat import ChatMessage, ImagePart, TextPart
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.spec import Spec
from codeverse.conventions import OBJECT_VIEWS, SCENE_VIEWS, ViewPreset
from codeverse.judges.rubrics import Rubric
from codeverse.spatial.measure import measure_summary_table
from codeverse.spatial.sheet import crop_region, load_font, montage_2x2

_PRESETS: dict[str, ViewPreset] = {v.name: v for v in (*OBJECT_VIEWS, *SCENE_VIEWS)}


class JudgeImageError(FileNotFoundError):
    """A render referenced by a RenderSet is missing or unreadable."""


def default_cache_dir() -> Path:
    return get_settings().cache_dir / "judge_images"


def view_az_el(view: RenderView) -> tuple[float, float] | None:
    """Azimuth/elevation (deg) of a view: from the preset name, else from camera geometry (Y-up)."""
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
        az = math.degrees(math.atan2(dx, dz)) % 360.0  # 0 = +Z front, CCW from above
        el = math.degrees(math.atan2(dy, horiz))
        return round(az, 1), round(el, 1)
    return None


def _cache_key(src: Path, max_px: int, label: str) -> str:
    st = src.stat()
    h = hashlib.sha1(f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}|{max_px}|{label}".encode())
    return h.hexdigest()[:20]


def prepare_image(
    src: str | Path, *, label: str = "", max_px: int = 768, cache_dir: Path | None = None
) -> Path:
    """Downscale ``src`` to ≤ ``max_px`` on its long side and burn ``label`` into a top strip.

    Returns the cached PNG path.  Raises ``JudgeImageError`` if the source is missing.
    """
    src = Path(src)
    if not src.is_file():
        raise JudgeImageError(f"judge image missing: {src}")
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{_cache_key(src, max_px, label)}.png"
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
        tmp = out.with_name(f"{out.stem}.{os.getpid()}-{threading.get_ident()}.tmp.png")  # unique per writer: concurrent judges share the cache
        im.save(tmp, format="PNG", optimize=True)
        tmp.replace(out)  # atomic; a concurrent identical write simply wins last
    return out


def _with_label_strip(im: Image.Image, label: str) -> Image.Image:
    w, h = im.size
    strip_h = max(22, int(h * 0.055))
    font = load_font(max(12, int(strip_h * 0.62)), bold=True)
    canvas = Image.new("RGB", (w, h + strip_h), (18, 18, 22))
    canvas.paste(im, (0, strip_h))
    draw = ImageDraw.Draw(canvas)
    text = label
    # truncate to fit
    while text and draw.textlength(text, font=font) > w - 12 and len(text) > 8:
        text = text[:-2]
    font_px = getattr(font, "size", 11)
    draw.text((6, max(2, (strip_h - font_px) // 2)), text, fill=(245, 245, 240), font=font)
    return canvas


def image_part(path: Path, label: str) -> ImagePart:
    return ImagePart(path=str(path), mime="image/png", label=label)


# ===================================================================== montage
MontageKind = Literal["shaded", "geometry", "poses", "pose_sheet", "detail"]

#: modes that show geometry without material/lighting noise
GEOMETRY_MODES = ("clay", "normals", "wire", "silhouette", "depth")
#: most-informative-first order for the object rig (names from conventions.OBJECT_VIEWS)
OBJECT_RANK = ("front_right_34", "back_left_34", "top", "low_front_left", "front", "right", "back", "left")
#: scene rig: authored cameras first (graded for composition), then the overview rig.
#: The rig names come from ``conventions.SCENE_VIEWS`` — anything else in a scene render
#: set is a camera the SCENE authored, whatever it is called (they are PascalCase plan
#: names such as ``Establishing``, never a fixed prefix).
SCENE_OVERVIEW_RANK = ("overview_front_right", "overview_back_left", "overview_top", "eye_front", "eye_right", "eye_back_left")
SCENE_RIG_NAMES = frozenset(v.name for v in SCENE_VIEWS) | frozenset(SCENE_OVERVIEW_RANK)
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
            if v.name not in SCENE_RIG_NAMES:
                return (0, 0, i)          # the scene's OWN cameras: the pictures being graded
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


# ===================================================================== prompt_builder
if TYPE_CHECKING:  # pragma: no cover
    from codeverse.judges.base import JudgeInput

TEXT_BUDGET_CHARS = 24_000  # ≈ 6k tokens
MAX_MONTAGES = 3
MAX_DETAIL_CROPS = 2
MAX_PX = 1024  # montages are 2×2 grids: keep them legible
MONTAGE_TILE_PX = 512

_ROLE = """You are the BLIND JUDGE of a 3D-code harness: exacting but fair.
You see only the brief, a plan digest, measured numbers, deterministic gate findings and labelled renders of the result — never the builder's code or reasoning. Judge what is visible and measured; do not invent faults and do not credit what you cannot see.
Gate findings describe the exported FILE (names, node hierarchy, contacts); the renders show the actual geometry. When they seem to disagree about something VISIBLE (a part moves in a posed tile, a part is present), trust the images for the visual criteria and report the file-level fact as an issue — never infer "nothing moves" or "part missing" from text alone when the tiles show otherwise.
Work in this order: observe (summary, strengths, issues), answer the defect checklist and the acceptance items, and only THEN score the criteria against their anchors.

SCORING RULES
- Score each criterion 0..1 against its anchors (interpolate between anchors). Use the WHOLE range: competent work sits at 0.8+, one clearly visible major defect pulls the affected criterion to ~0.4, broken work sits at 0.1-0.3. Do not compress everything into 0.5-0.7 — a primitive box-stack and a crafted product must be 0.4 apart, not 0.1.
- Every score needs evidence that cites the image and tile (e.g. "MONTAGE 1 top-left (front_right_34): rear leg ends 3 cm above ground; measurement ground_gap 0.03").
- DEFECT CHECKLIST: answer EVERY item with present=true/false. true ONLY when the defect is visible in an image, or a gate finding of severity ERROR / a measurement states it; gate WARNINGS (e.g. a few-mm weld overlap) are informational and never make a defect present. Cite where. These answers drive penalties and caps computed by the harness, so be literal: do not mark a defect to "be safe", and do not hide one to be kind.
- Do NOT compute an overall or decide pass/fail; the harness computes the weighted overall, subtracts defect penalties, applies floors and caps.
- Issues: observable defects, most severe first, with target = the part / zone / joint / asset name from the plan digest (or "overall"), a kind, a severity and evidence.
- Improvement plan: at most 6 concrete, imperative instructions for the builder ("taper the four legs from 45 mm at the seat to 30 mm at the foot and extend them to touch y=0"), priority 1 first, each with a target name from the plan digest and an expected_gain estimate. Give items even for passing work if a named change would raise the score; leave empty only when nothing would.
- Acceptance items: answer verified=true ONLY when the renders or measurements prove the item; otherwise false with what is missing.
- Reply with ONE JSON object matching the requested schema; no prose outside it."""


def _rubric_block(rubric: Rubric) -> str:
    lines = [f"RUBRIC {rubric.name} v{rubric.version} (pass threshold {rubric.pass_threshold:.2f}, for information only)"]
    for i, c in enumerate(rubric.criteria, 1):
        if c.kind == "measured":
            lines.append(f"{i}. {c.id} (weight {c.weight:.2f}) — MEASURED BY THE HARNESS, shown for calibration; do not score it.")
            continue
        floor = f", hard floor {c.floor:.2f}" if c.floor is not None else ""
        lines.append(f"{i}. {c.id} (weight {c.weight:.2f}{floor}) — {c.label}: {c.description.strip()}")
        # every anchor the rubric declares, richest scale first (rubrics may add levels
        # between the four required ones — e.g. scene_v1's 0.5 "diorama on a flat plane")
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


def build_system_prompt(rubric: Rubric) -> str:
    return _ROLE + "\n\n" + _rubric_block(rubric)


# --------------------------------------------------------------------------- text sections
def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 12].rstrip() + " …[clipped]"


def brief_section(spec: Spec) -> str:
    lines = [f"BRIEF (track {spec.track.value}, language {spec.language.value}):", _clip(spec.prompt, 3000)]
    c = spec.constraints
    cons = []
    if c.dimensions_m:
        cons.append("dimensions_m: " + ", ".join(f"{k}={v:g}" for k, v in c.dimensions_m.items()))
    if c.max_triangles:
        cons.append(f"max_triangles: {c.max_triangles}")
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
    return "MEASUREMENTS (harness, Y-up meters):\n" + _clip(str(measure_summary_table(m)), 6000)


def gates_section(gates: list[GateReport], *, max_errors: int = 12, max_warns: int = 8) -> str:
    if not gates:
        return "GATE FINDINGS: (no gates run)"
    errs, warns = [], []
    for g in gates:
        for f in g.findings:
            tgt = f" [{f.target}]" if f.target else ""
            line = f"- {g.gate}{tgt}: {_clip(f.message, 220)}"
            (errs if f.severity == Severity.ERROR else warns if f.severity == Severity.WARN else []).append(line)
    status = ", ".join(f"{g.gate}={'pass' if g.passed else 'FAIL'}" for g in gates)
    lines = [f"GATE FINDINGS (deterministic; treat as facts): {status}"]
    if errs:
        lines.append(f"errors ({len(errs)}):")
        lines.extend(errs[:max_errors])
        if len(errs) > max_errors:
            lines.append(f"- … {len(errs) - max_errors} more errors")
    if warns:
        lines.append(f"warnings ({len(warns)}):")
        lines.extend(warns[:max_warns])
        if len(warns) > max_warns:
            lines.append(f"- … {len(warns) - max_warns} more warnings")
    if not errs and not warns:
        lines.append("no errors or warnings — parts are connected and contracts hold.")
    if any(g.gate == "connectivity" and g.passed for g in gates):
        # The connectivity gate MEASURES mesh-to-mesh contact; a VLM reads shading.  Measured
        # 2026-08-26 (fancy_v1 gas_street_lamp, plan-pinned pair): on a lamp whose gate said
        # "all 9 parts connected, gap <= 2 mm", the judge called the dark seam under the
        # pedestal "floating in mid-air, a clear daylight gap" — CRITICAL — and scored
        # structure_plausibility 0.4 against 1.0 for the near-identical sibling.  A measured
        # contact outranks a shadow (CLAUDE.md law 3); the seam is at most a craftsmanship note.
        lines.append(
            "CONNECTIVITY PASSED: every part is in measured contact with its neighbour (gap <= 2 mm). "
            "A dark seam or shadow line where two parts meet is contact, not daylight. Do NOT report any "
            "part as floating, hovering or disconnected, and do not mark the floating-part defect present; "
            "if a joint looks visually ugly, say so under craftsmanship."
        )
    return "\n".join(lines)


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


#: The fixed sentences of the view-rig paragraph (everything that is not a count or a
#: per-run probe line).  Kept as constants so ``judge_prompt_hash`` covers them.
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
    "poses": "POSE tiles show the SAME object with joints moved by the harness (tile label = joint@value or rest). Judge articulation only from them and the joint table.",
    "scene_cams": "Views named overview_* are harness cameras fitted to the scene bounds (layout X-ray); views named cam_* are the scene's own authored cameras (grade composition/lighting on those); 't=' is the animation time.",
    "scene_craft": "On the authored cameras also read the CRAFT of the picture, not only its contents: depth layering (is there anything within a few metres framing the shot, and anything on the horizon), ground variation (blended materials, paths, dressed edges vs one flat colour), variety among repeated natural elements, small-prop dressing, and aerial perspective (distant things hazier than near ones). The same camera at two times appears as separate tiles — compare them pixel-for-pixel before answering nothing_moves.",
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
    else:
        bits.append(RIG_RULES["object"])
    if renders.console_errors:
        bits.append(f"PROBE: {len(renders.console_errors)} console error(s) during rendering, first: {_clip(renders.console_errors[0], 200)}")
    if renders.fps is not None:
        bits.append(f"PROBE: measured {renders.fps:.0f} fps.")
    bits.append("Images in send order:\n" + describe_montages(montages))
    return "\n".join(bits)


def judge_prompt_hash(rubric: Rubric) -> str:
    """Hash of everything the judge is told that is constant for a rubric.

    Covers the system prompt (``_ROLE`` + the rubric block as rendered), the fixed
    view-rig rules and the wire schema's structure (field order is the
    observe-then-score protocol; ``EVAL.md`` §6 records that changing it moved the
    flash judge's σ from 0.01 to 0.08).  Per-run content (brief, plan digest,
    measurements, acceptance ids, images) is deliberately excluded, so two runs judged
    under the same protocol share the hash and a prompt edit is visible in every
    ``ScoreBreakdown.judge_prompt_hash`` / ``record.prompt_hashes["judge"]`` it touched.
    ``rubric_hash`` (the YAML alone) stays alongside for the narrower question.
    """
    import json

    from codeverse.judges.rubrics import wire_schema
    from codeverse.prompts import prompt_hash

    payload = "\n".join([
        build_system_prompt(rubric),
        *(RIG_RULES[k] for k in sorted(RIG_RULES)),
        json.dumps(wire_schema(rubric, []), sort_keys=True),
    ])
    return prompt_hash(payload)


# --------------------------------------------------------------------------- images
def montage_image_parts(
    renders: RenderSet,
    *,
    geometry_views: RenderSet | None = None,
    scene: bool = False,
    shuffle_seed: int | None = None,
    max_montages: int = MAX_MONTAGES,
    detail_crops: int = MAX_DETAIL_CROPS,
    max_px: int = MAX_PX,
    cache_dir: Path | None = None,
) -> tuple[list[tuple[str, ImagePart]], list[Montage]]:
    """(label, ImagePart) pairs for the montages of a render set, plus the montage plan (in send order)."""
    montages = shuffle_montages(
        plan_montages(renders, geometry_views=geometry_views, scene=scene, max_montages=max_montages, detail_crops=detail_crops),
        shuffle_seed,
    )
    grids = [m for m in montages if not m.is_detail]
    details = [m for m in montages if m.is_detail]
    out: list[tuple[str, ImagePart]] = []
    for i, m in enumerate(grids, 1):
        lbl, strip = montage_label(m, i, len(grids)), montage_strip(m, i, len(grids))
        png = render_montage(m, cache_dir=cache_dir, tile_px=MONTAGE_TILE_PX)
        out.append((lbl, image_part(prepare_image(png, label=strip, max_px=max_px, cache_dir=cache_dir), lbl)))
    for i, m in enumerate(details, 1):
        lbl, strip = montage_label(m, i, len(details)), montage_strip(m, i, len(details))
        png = render_montage(m, cache_dir=cache_dir, tile_px=MONTAGE_TILE_PX)
        out.append((lbl, image_part(prepare_image(png, label=strip, max_px=min(max_px, 768), cache_dir=cache_dir), lbl)))
    return out, montages


# --------------------------------------------------------------------------- assembly
def build_judge_messages(
    inp: JudgeInput,
    rubric: Rubric,
    *,
    shuffle_seed: int | None = None,
    geometry_views: RenderSet | None = None,
    max_montages: int = MAX_MONTAGES,
    detail_crops: int = MAX_DETAIL_CROPS,
    max_px: int = MAX_PX,
    cache_dir: Path | None = None,
    extra_images: list[tuple[str, str | Path]] | None = None,
    extra_text: str = "",
) -> tuple[str, list[ChatMessage]]:
    """Return ``(system, [user_message])`` for a rubric judge call.

    ``geometry_views`` is an optional second RenderSet rendered with
    ``mode='clay'`` / ``'normals'`` (tracks may pass it); when absent, geometry
    views embedded in ``inp.renders`` (by ``RenderView.mode``) are used, else the
    geometry montage is skipped.  ``shuffle_seed`` permutes montage and tile
    order (n-sample noise control).  ``extra_images`` (label, path) are placed
    BEFORE the montages (e.g. reference images); ``extra_text`` is appended to
    the text block (e.g. measured silhouette).
    """
    system = build_system_prompt(rubric)
    is_scene = inp.spec.track.value == "scene"
    sections = [
        brief_section(inp.spec),
        ("PLAN DIGEST:\n" + _clip(inp.plan_summary, 5000)) if inp.plan_summary.strip() else "PLAN DIGEST: (none)",
        acceptance_section(inp.acceptance),
        measurement_section(inp.measurement),
        gates_section(inp.gates),
        previous_section(inp.previous, inp.round_index),
        _clip(inp.extra_context, 2500) if inp.extra_context.strip() else "",
        _clip(extra_text, 2500) if extra_text.strip() else "",
    ]
    extras = extra_images or []
    images, montages = montage_image_parts(
        inp.renders, geometry_views=geometry_views, scene=is_scene, shuffle_seed=shuffle_seed,
        max_montages=max_montages, detail_crops=detail_crops, max_px=max_px, cache_dir=cache_dir,
    )
    sections.append(view_rig_section(inp.renders, montages, scene=is_scene))
    text = "\n\n".join(s for s in sections if s)
    if len(text) > TEXT_BUDGET_CHARS:
        text = _clip(text, TEXT_BUDGET_CHARS)
    parts: list[TextPart | ImagePart] = [TextPart(text=text)]
    for lbl, p in extras:
        parts.append(TextPart(text=lbl))
        parts.append(image_part(prepare_image(p, label=lbl, max_px=min(max_px, 768), cache_dir=cache_dir), lbl))
    for lbl, ip in images:
        parts.append(TextPart(text=lbl))
        parts.append(ip)
    parts.append(TextPart(text="Now score every criterion with evidence, answer every defect-checklist item and every acceptance item, and return the JSON object."))
    return system, [ChatMessage(role="user", parts=parts)]
