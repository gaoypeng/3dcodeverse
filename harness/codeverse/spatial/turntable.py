"""Assemble rendered frames into a turntable video (ffmpeg) or animated GIF (PIL)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from codeverse.config import get_settings
from codeverse.spatial.sheet import write_gif


def assemble_turntable(frames: list[Path], out: Path, *, fps: int = 12) -> Path:
    """Write ``out`` from ``frames``; ``.mp4`` needs ffmpeg (else falls back to ``.gif``)."""
    if not frames:
        raise ValueError("assemble_turntable: no frames")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_settings().binaries.ffmpeg
    if out.suffix.lower() == ".mp4" and shutil.which(ffmpeg):
        return _mp4(frames, out, fps, ffmpeg)
    if out.suffix.lower() == ".mp4":
        out = out.with_suffix(".gif")
    return _gif(frames, out, fps)


def _mp4(frames: list[Path], out: Path, fps: int, ffmpeg: str) -> Path:
    listing = out.parent / f".{out.stem}_frames.txt"
    listing.write_text("".join(f"file '{f.resolve()}'\nduration {1 / fps:.4f}\n" for f in frames))
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p", "-r", str(fps), "-c:v", "libx264", "-crf", "22", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    listing.unlink(missing_ok=True)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-1500:]}")
    return out


def _gif(frames: list[Path], out: Path, fps: int) -> Path:
    """Animated GIF via the shared writer (``spatial.sheet.write_gif``)."""
    return write_gif(frames, out, fps=fps)
