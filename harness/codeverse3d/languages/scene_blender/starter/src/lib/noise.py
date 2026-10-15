"""lib/noise.py — seeded noise on the CPU: hills, scatter masks, jitter.  Pure python.

Deterministic in (coordinates, seed) on every machine, so a zone can seat on the env's
``height_at`` without reading the terrain mesh back.  Gradient (Perlin) noise on a hashed
lattice: ``fbm2`` / ``fbm3`` return about -1..1, ``ridged`` 0..1.  Cost: a 96 x 96 height
field with four octaves is ~60 ms — fine for the env's ground, too slow for a per-blade
query (use ``hash01`` for that).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence

_MASK = 0xFFFFFFFF
#: the twelve Perlin gradient directions (edge midpoints of a cube)
_GRAD3: tuple[tuple[float, float, float], ...] = (
    (1, 1, 0), (-1, 1, 0), (1, -1, 0), (-1, -1, 0), (1, 0, 1), (-1, 0, 1),
    (1, 0, -1), (-1, 0, -1), (0, 1, 1), (0, -1, 1), (0, 1, -1), (0, -1, -1),
)


def rng(seed: int = 0) -> random.Random:
    """THE seeded generator (``ctx.rng(seed)`` is the same object): never module-level random."""
    return random.Random(seed)


def hash01(i: int, seed: int = 0) -> float:
    """A uniform [0, 1) value from an integer and a seed — the lattice hash, also the cheapest
    per-item jitter (``hash01(index, seed)`` costs nothing and never drifts between runs)."""
    x = (int(i) * 0x27D4EB2D + int(seed) * 0x165667B1 + 0x9E3779B9) & _MASK
    x = ((x ^ (x >> 15)) * 0x2C1B3C6D) & _MASK
    x = ((x ^ (x >> 12)) * 0x297A2D39) & _MASK
    return (x ^ (x >> 15)) / 4294967296.0


def _lattice(ix: int, iy: int, iz: int = 0) -> int:
    return (ix * 1619 + iy * 31337 + iz * 6971) & _MASK


def _fade(t: float) -> float:
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _grad2(ix: int, iy: int, seed: int, fx: float, fy: float) -> float:
    a = hash01(_lattice(ix, iy), seed) * math.tau
    return math.cos(a) * fx + math.sin(a) * fy


def _grad3(ix: int, iy: int, iz: int, seed: int, fx: float, fy: float, fz: float) -> float:
    g = _GRAD3[int(hash01(_lattice(ix, iy, iz), seed) * 12.0) % 12]
    return g[0] * fx + g[1] * fy + g[2] * fz


def noise2(x: float, y: float, seed: int = 0) -> float:
    """One octave of 2-D gradient noise, about -1..1, period-free."""
    x0, y0 = math.floor(x), math.floor(y)
    fx, fy = x - x0, y - y0
    u, v = _fade(fx), _fade(fy)
    n00 = _grad2(x0, y0, seed, fx, fy)
    n10 = _grad2(x0 + 1, y0, seed, fx - 1.0, fy)
    n01 = _grad2(x0, y0 + 1, seed, fx, fy - 1.0)
    n11 = _grad2(x0 + 1, y0 + 1, seed, fx - 1.0, fy - 1.0)
    nx0 = n00 + u * (n10 - n00)
    nx1 = n01 + u * (n11 - n01)
    return max(-1.0, min(1.0, (nx0 + v * (nx1 - nx0)) * 1.4142))


def noise3(x: float, y: float, z: float, seed: int = 0) -> float:
    """One octave of 3-D gradient noise, about -1..1 (boulder displacement, cloud density)."""
    x0, y0, z0 = math.floor(x), math.floor(y), math.floor(z)
    fx, fy, fz = x - x0, y - y0, z - z0
    u, v, w = _fade(fx), _fade(fy), _fade(fz)

    def corner(dx: int, dy: int, dz: int) -> float:
        return _grad3(x0 + dx, y0 + dy, z0 + dz, seed, fx - dx, fy - dy, fz - dz)

    x00 = corner(0, 0, 0) + u * (corner(1, 0, 0) - corner(0, 0, 0))
    x10 = corner(0, 1, 0) + u * (corner(1, 1, 0) - corner(0, 1, 0))
    x01 = corner(0, 0, 1) + u * (corner(1, 0, 1) - corner(0, 0, 1))
    x11 = corner(0, 1, 1) + u * (corner(1, 1, 1) - corner(0, 1, 1))
    y0v = x00 + v * (x10 - x00)
    y1v = x01 + v * (x11 - x01)
    return max(-1.0, min(1.0, (y0v + w * (y1v - y0v)) * 1.15))


def fbm2(x: float, y: float, *, octaves: int = 4, lacunarity: float = 2.0, gain: float = 0.5,
         seed: int = 0) -> float:
    """Fractal sum of ``octaves`` gradient octaves, normalised to about -1..1.  Feature size is
    1 unit: call with ``x / scale_m``."""
    total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(max(1, int(octaves))):
        total += amp * noise2(x * freq + o * 17.31, y * freq - o * 11.07, seed + o)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


def fbm3(x: float, y: float, z: float, *, octaves: int = 4, lacunarity: float = 2.0, gain: float = 0.5,
         seed: int = 0) -> float:
    """``fbm2`` in three dimensions (about -1..1)."""
    total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(max(1, int(octaves))):
        total += amp * noise3(x * freq + o * 5.17, y * freq - o * 3.71, z * freq + o * 7.03, seed + o)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


def ridged(x: float, y: float, *, seed: int = 0, octaves: int = 5) -> float:
    """Ridged multifractal, 0..1: sharp crests and rounded valleys (mountains, dune crests)."""
    total, amp, freq, norm, weight = 0.0, 1.0, 1.0, 0.0, 1.0
    for o in range(max(1, int(octaves))):
        n = 1.0 - abs(noise2(x * freq + o * 13.7, y * freq + o * 9.1, seed + o))
        n = n * n * weight
        weight = max(0.0, min(1.0, n * 2.0))
        total += n * amp
        norm += amp
        amp *= 0.5
        freq *= 2.1
    return max(0.0, min(1.0, total / norm))


def mountain_height(x: float, y: float, *, seed: int = 0, amplitude: float = 45.0, frequency: float = 0.013) -> float:
    """A horizon ridge's height (m) at (x, y): ``amplitude`` x ridged noise of wavelength
    ``1 / frequency`` m (the defaults give 45 m peaks about 80 m apart) — for a backdrop range
    BEYOND the play area, never under the cameras."""
    r = ridged(x * frequency, y * frequency, seed=seed)
    return amplitude * r * r


def height_field(size: float | Sequence[float], segments: int, fn: Callable[[float, float], float]) -> list[float]:
    """``fn(x, y)`` sampled on a ``(segments + 1)²`` grid centred at the origin, row-major
    (y outer, x inner) — the order ``terrain.ground`` and ``mesh.mesh_object`` grids use."""
    sx, sy = (float(size), float(size)) if isinstance(size, (int, float)) else (float(size[0]), float(size[1]))
    n = max(1, int(segments))
    out: list[float] = []
    for iy in range(n + 1):
        y = -sy / 2 + sy * iy / n
        for ix in range(n + 1):
            out.append(float(fn(-sx / 2 + sx * ix / n, y)))
    return out
