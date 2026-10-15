"""src/scene.py — ASSEMBLED BY THE HARNESS (codeverse3d assembler).  Edit env / zones / assets
instead; re-assembly overwrites this file.
EXAMPLE: a lakeside hamlet at golden hour — the plan-less starter scene (every zone and asset is
built from src/lib; read them as worked examples of the library).

The harness builds the scene in one Blender process: env.build_env(ctx); every hero GLB and asset
module (build_<snake>(ctx)); then every zone's build(ctx) — each on its own (a zone that raises is
dropped and reported with its file:line); then the CAMERAS below (Z up, -Y front, metres; fov is
the VERTICAL field of view in degrees).  The census is the t = 0 pose, frame 1.
"""

ZONES = {
    "hamlet": "Hamlet",
    "lakeside": "Lakeside",
    "orchard": "Orchard",
}
ASSETS = {
    "crate": "Crate",
    "lantern_post": "LanternPost",
}
HEROES = {}
CAMERAS = [
    {"name": "village_street", "location": (1.0, -22.0, 1.8), "look_at": (3.0, 10.0, 1.6), "fov": 42},
    {"name": "lake_shore", "location": (-4.0, -24.0, 2.4), "look_at": (-24.0, 4.0, 0.2), "fov": 48},
    {"name": "orchard_walk", "location": (36.0, -30.0, 2.2), "look_at": (20.0, -10.0, 1.2), "fov": 45},
]
