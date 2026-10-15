"""src/env.py — the environment of the EXAMPLE scene (a lakeside hamlet): a low warm sun, rolling
meadow beyond a level pad, a lake basin, haze.  The landform is this example's, not a rule.

Frame: Z up, -Y front, metres.
CONTRACT: height_at(x, y) -> z is THE ground: the terrain mesh and every zone's seat use it.
  build_env(ctx) -> dict builds the world, lamps, ground and atmosphere into ctx.collection and
  returns what zones may share (materials, water level, sun direction ...) — they read it as
  ctx.env.  Everything here comes from src/lib (lib.terrain / lib.sky / lib.lighting /
  lib.materials); keep build_env and height_at when you rewrite it.
"""
import math

from lib import lighting, materials, sky, terrain

GROUND_SIZE = 160.0
FLAT_RADIUS = 48.0          # THIS example's landform: level under its hamlet, hills beyond — one landform among many; yours is the brief's
SUN_AZIMUTH_DEG = 215.0     # compass angle of the sun: 0 = +X, 90 = +Y (the sky and the lamp share it)
SUN_ELEVATION_DEG = 22.0
BASIN = (-24.0, 2.0, 15.0, 1.2)   # the lake's (x, y, radius, depth): zones/lakeside.py fills it to WATER_LEVEL
WATER_LEVEL = -0.3
SEED = 5
_RELIEF = terrain.make_height_at(seed=SEED, relief=2.5, scale=60.0, flat_radius=FLAT_RADIUS)


def height_at(x, y):
    """z of the ground at (x, y): the relief minus a smooth-rimmed bowl under the lake."""
    bx, by, br, bd = BASIN
    d = math.hypot(x - bx, y - by) / br
    t = max(0.0, min(1.0, (d - 0.3) / 0.7))        # a wide rim: banks under 15 % so props seat on them
    bowl = bd * (1.0 - t * t * (3.0 - 2.0 * t))
    return _RELIEF(x, y) - bowl


def build_env(ctx):
    sky.sun_world(ctx.scene, elevation_deg=SUN_ELEVATION_DEG, azimuth_deg=SUN_AZIMUTH_DEG, strength=0.35, dust=2.5)
    _, towards_sun = lighting.sun(ctx.collection, azimuth_deg=SUN_AZIMUTH_DEG, elevation_deg=SUN_ELEVATION_DEG,
                                  color="FFC98A", energy=3.5, angle_deg=1.5)
    ground_mat = materials.material("GroundMat", "4F5E39", roughness=0.92, noise_scale=5.0)
    terrain.ground("Ground", ctx.collection, size=GROUND_SIZE, segments=112, height_at=height_at, material=ground_mat)
    sky.haze(ctx.collection, (0.0, 0.0, 12.0), (GROUND_SIZE, GROUND_SIZE, 24.0), density=0.0025, color="D9C7A8")
    return {"ground_material": ground_mat, "sun_direction": tuple(towards_sun), "water_level": WATER_LEVEL}
