"""Render every turntable the teaser page uses.

  python bench/teaser/render_turntables.py            # all
  python bench/teaser/render_turntables.py penny_farthing

Writes bench/out/teaser/turntables/<slug>.gif for the shipped object deliverables and
<TEASER_ASSET_TURNTABLES>/<snake>.gif for the four Blender heroes of the scene lane.
mp4 is requested first; on a box without ffmpeg `assemble_turntable` falls back to GIF.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from codeverse.spatial.render import render_turntable

HARNESS = Path(__file__).resolve().parents[2]
RUNS = HARNESS / "bench/out/teaser/runs"
OUT = HARNESS / "bench/out/teaser/turntables"
ASSET_OUT = Path(os.environ.get("TEASER_ASSET_TURNTABLES", str(OUT / "assets")))

# shipped object deliverables (slug -> nothing; the GLB is resolved below)
OBJECTS = [
    "tsr_obj_penny_farthing", "tsr_obj_lighthouse", "tsr_obj_gramophone",
    "tsr_cad_radial_engine_v2", "tsr_tjs_arcade_cabinet", "tsr_tjs_deco_chandelier_v2",
]
# Blender heroes authored inside a scene run's per-asset sub-workspace
ASSETS = [
    ("tsr_scn_temple_night_v2", "stone_lantern"),
    ("tsr_scn_temple_night_v2", "bronze_censer"),
    ("tsr_scn_boat_workshop_v2", "clinker_skiff"),
    ("tsr_scn_boat_workshop_v2", "potbelly_stove"),
]


def shipped_glb(artifacts: Path) -> Path:
    """object_textured.glb ONLY when the texture ship gate accepted it.

    The texture pass writes object_textured.glb whether or not it ships, so resolving by
    filename alone renders the build the judge measured as worse (DEFECTS.md S-OBJ-3).
    """
    tj = artifacts / "textures" / "texturing.json"
    if tj.is_file():
        try:
            if json.loads(tj.read_text()).get("shipped") and (artifacts / "object_textured.glb").is_file():
                return artifacts / "object_textured.glb"
        except (OSError, ValueError):
            pass
    return artifacts / "object.glb"


def clip(glb: Path, out: Path) -> None:
    if not glb.is_file():
        print(f"  SKIP {out.stem}: no {glb}")
        return
    p = render_turntable(glb, out, n=36, elevation_deg=18.0, width=640, height=640, fps=15)
    print(f"  {p}  ({p.stat().st_size // 1024} KB)  from {glb.name}")


def main(only: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ASSET_OUT.mkdir(parents=True, exist_ok=True)
    for slug in OBJECTS:
        if only and not any(o in slug for o in only):
            continue
        clip(shipped_glb(RUNS / slug / "artifacts"), OUT / f"{slug}.mp4")
    for slug, snake in ASSETS:
        if only and not any(o in snake for o in only):
            continue
        clip(RUNS / slug / "_assets" / snake / "artifacts" / "object.glb", ASSET_OUT / f"{snake}.mp4")


if __name__ == "__main__":
    main(sys.argv[1:])
