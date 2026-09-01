"""Visual + temporal metrics for a rendered frame sequence (numpy + PIL only).

Per frame: mean luminance, luminance std, % near-black, % blown-out pixels,
colourfulness (Hasler–Süsstrunk), edge density (gradient magnitude above a
threshold).  Temporal: mean absolute difference between consecutive frames
(0 → static), duplicate frames, flicker (very large diffs), plus NaN/Inf counts
reported by the GL runner (float readback).  ``frame_gate`` turns the sequence
into the ``gl_frames`` GateReport the graphics track + rubric caps consume
(``data["kind"]`` ∈ nan · black · blown · static · flicker · duplicate · low_detail).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity

GATE = "gl_frames"
BLACK_LUM = 0.03
BLOWN_LUM = 0.98
STATIC_DIFF = 0.002
FLICKER_DIFF = 0.35
DUPLICATE_DIFF = 1e-4
LOW_DETAIL_EDGE = 0.002
ANALYSIS_MAX_W = 512


class FrameStat(BaseModel):
    time: float
    path: str
    mean_lum: float
    std_lum: float
    pct_black: float
    pct_blown: float
    colourfulness: float
    edge_density: float
    nan: int = 0
    inf: int = 0


class SequenceStats(BaseModel):
    frames: list[FrameStat] = Field(default_factory=list)
    diffs: list[float] = Field(default_factory=list, description="mean |Δ| between consecutive frames (0..1)")
    mean_diff: float = 0.0
    max_diff: float = 0.0
    n_duplicates: int = 0
    static: bool = False
    flicker: bool = False
    any_nan: bool = False
    mean_lum: float = 0.0
    mean_colourfulness: float = 0.0
    mean_edge_density: float = 0.0
    all_black: bool = False
    all_blown: bool = False

    def summary_lines(self) -> list[str]:
        out = [f"frames={len(self.frames)} mean_lum={self.mean_lum:.3f} colourfulness={self.mean_colourfulness:.3f} "
               f"edge_density={self.mean_edge_density:.4f} motion(mean|Δ|)={self.mean_diff:.4f} max|Δ|={self.max_diff:.4f} "
               f"duplicates={self.n_duplicates}" + (" NaN/Inf!" if self.any_nan else "")]
        for f in self.frames:
            out.append(f"  t={f.time:g}s lum={f.mean_lum:.3f}±{f.std_lum:.3f} black={f.pct_black:.0%} blown={f.pct_blown:.0%} "
                       f"colour={f.colourfulness:.3f} edges={f.edge_density:.4f}" + (f" nan={f.nan} inf={f.inf}" if f.nan or f.inf else ""))
        return out


# --------------------------------------------------------------------------- per frame
def load_rgb(path: str | Path, max_w: int = ANALYSIS_MAX_W) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, max(1, round(im.height * max_w / im.width))), Image.BILINEAR)
    return np.asarray(im, dtype=np.float32) / 255.0


def luminance(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def colourfulness(rgb: np.ndarray) -> float:
    """Hasler & Süsstrunk (2003) metric on 0..1 floats (≈0 grey, ~0.3+ vivid)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    rg = r - g
    yb = 0.5 * (r + g) - b
    std = float(np.hypot(rg.std(), yb.std()))
    mean = float(np.hypot(rg.mean(), yb.mean()))
    return std + 0.3 * mean


def edge_density(lum: np.ndarray, threshold: float = 0.02) -> float:
    gy, gx = np.gradient(lum)
    mag = np.hypot(gx, gy)
    return float((mag > threshold).mean())


def frame_stat(path: str | Path, *, time: float, nan: int = 0, inf: int = 0, rgb: np.ndarray | None = None) -> FrameStat:
    rgb = load_rgb(path) if rgb is None else rgb
    lum = luminance(rgb)
    return FrameStat(time=time, path=str(path), mean_lum=float(lum.mean()), std_lum=float(lum.std()),
                     pct_black=float((lum < BLACK_LUM).mean()), pct_blown=float((lum > BLOWN_LUM).mean()),
                     colourfulness=colourfulness(rgb), edge_density=edge_density(lum), nan=nan, inf=inf)


# --------------------------------------------------------------------------- sequence
def sequence_stats(frames: Sequence[tuple[float, str | Path]], *, nan_counts: Sequence[tuple[int, int]] | None = None) -> SequenceStats:
    """``frames`` = [(time, png_path)] sorted by time; ``nan_counts`` aligned [(nan, inf)]."""
    ordered = sorted(enumerate(frames), key=lambda it: it[1][0])
    stats: list[FrameStat] = []
    arrays: list[np.ndarray] = []
    for orig_i, (t, p) in ordered:
        rgb = load_rgb(p)
        nan, inf = (nan_counts[orig_i] if nan_counts and orig_i < len(nan_counts) else (0, 0))
        stats.append(frame_stat(p, time=t, nan=nan, inf=inf, rgb=rgb))
        arrays.append(rgb)
    diffs: list[float] = []
    for a, b in zip(arrays, arrays[1:], strict=False):
        if a.shape != b.shape:
            diffs.append(1.0)
            continue
        diffs.append(float(np.abs(a - b).mean()))
    seq = SequenceStats(frames=stats, diffs=diffs)
    if stats:
        seq.mean_lum = float(np.mean([f.mean_lum for f in stats]))
        seq.mean_colourfulness = float(np.mean([f.colourfulness for f in stats]))
        seq.mean_edge_density = float(np.mean([f.edge_density for f in stats]))
        seq.all_black = all(f.pct_black > 0.97 for f in stats)
        seq.all_blown = all(f.pct_blown > 0.97 for f in stats)
        seq.any_nan = any(f.nan or f.inf for f in stats)
    if diffs:
        seq.mean_diff = float(np.mean(diffs))
        seq.max_diff = float(np.max(diffs))
        seq.n_duplicates = sum(1 for d in diffs if d < DUPLICATE_DIFF)
        # MAX, not mean: 'static' is a scoring cap (shader_v2.yaml static_frames), so it must
        # require that NO sampled pair changed.  With the mean, an effect whose motion is
        # concentrated between two of the five sampled times (0/1/2.5/4/6 s) is averaged down
        # by the three quiet pairs and convicted while animating.
        seq.static = seq.max_diff < STATIC_DIFF
        seq.flicker = seq.max_diff > FLICKER_DIFF and len(diffs) >= 2
    return seq


# --------------------------------------------------------------------------- gate
def frame_gate(seq: SequenceStats, *, motion_expected: bool = True, gate: str = GATE) -> GateReport:
    """Deterministic findings the judge/refiner/caps consume (``data["kind"]`` set)."""
    f: list[GateFinding] = []

    def add(sev: Severity, kind: str, msg: str, hint: str, target: str = "overall", **data: object) -> None:
        f.append(GateFinding(gate=gate, severity=sev, target=target, message=msg, fix_hint=hint, data={"kind": kind, **data}))

    if not seq.frames:
        add(Severity.ERROR, "no_frames", "no frames were rendered", "the program must produce at least one frame")
        return GateReport(gate=gate, passed=False, findings=f)
    if seq.any_nan:
        bad = [fr for fr in seq.frames if fr.nan or fr.inf]
        add(Severity.ERROR, "nan", f"NaN/Inf pixels in {len(bad)} frame(s) (first at t={bad[0].time:g}s: nan={bad[0].nan} inf={bad[0].inf})",
            "guard divisions (x / max(d, 1e-4)), sqrt/pow/log of negatives (max(x, 0.0)), normalize(vec) of zero vectors, "
            "and acos/asin arguments (clamp(x, -1.0, 1.0)); never let a colour be NaN",
            n_frames=len(bad))
    if seq.all_black:
        add(Severity.ERROR, "black", f"frames are essentially black (mean luminance {seq.mean_lum:.3f})",
            "something must be visible: check the final fragColor assignment, the raymarch hit test, camera direction and "
            "that colours are not multiplied to zero; aim for mean luminance 0.2-0.6")
    elif seq.all_blown:
        add(Severity.ERROR, "blown", f"frames are blown out white (mean luminance {seq.mean_lum:.3f})",
            "tone-map and clamp: col = col / (1.0 + col); keep accumulated glow sums bounded")
    if seq.static and motion_expected:
        add(Severity.WARN, "static", f"frames do not change over time (largest |Δ| between any two sampled frames {seq.max_diff:.4f}); the shader looks static",
            "use u_time for motion: scroll uv, rotate, animate noise offsets, move lights — the judge compares frames at t=0..6s")
    elif seq.n_duplicates and motion_expected:
        add(Severity.INFO, "duplicate", f"{seq.n_duplicates} consecutive frame pair(s) are identical",
            "make sure every sampled time differs visibly (continuous motion rather than rare jumps)")
    if seq.flicker:
        add(Severity.WARN, "flicker", f"very large frame-to-frame change (max |Δ| {seq.max_diff:.3f}); flicker or hard cuts",
            "avoid discontinuous functions of u_time (fract/mod jumps, random per-frame seeds); motion should be continuous")
    if seq.mean_edge_density < LOW_DETAIL_EDGE and not seq.all_black and not seq.all_blown:
        add(Severity.WARN, "low_detail", f"very low visual detail (edge density {seq.mean_edge_density:.4f}); the image is a near-flat gradient",
            "add structure: fbm layers, shapes (sdf), stars/particles, lines — the rubric rewards visual richness")
    passed = not any(x.severity == Severity.ERROR for x in f)
    return GateReport(gate=gate, passed=passed, findings=f)
