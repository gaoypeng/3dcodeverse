"""Copy + downscale the curated teaser stills into bench/out/teaser/site/media."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from PIL import Image

HARNESS = Path(__file__).resolve().parents[2]
R = HARNESS / "bench/out/teaser/runs"
TT = HARNESS / "bench/out/teaser/turntables"
# Turntables for the four Blender heroes (bench/teaser/render_turntables.py writes them here).
ASSET_TT = Path(os.environ.get("TEASER_ASSET_TURNTABLES", str(TT / "assets")))
OUT = HARNESS / "bench/out/teaser/site/media"
OUT.mkdir(parents=True, exist_ok=True)

def jpg(src: Path, name: str, maxw: int = 1280, q: int = 88) -> None:
    im = Image.open(src).convert("RGB")
    if im.width > maxw:
        im = im.resize((maxw, round(im.height * maxw / im.width)), Image.LANCZOS)
    im.save(OUT / f"{name}.jpg", quality=q, optimize=True, progressive=True)

def gif(src: Path, name: str) -> None:
    shutil.copy2(src, OUT / f"{name}.gif")

# ---- static objects -------------------------------------------------------
OBJ = {
    "penny_farthing":   ("tsr_obj_penny_farthing",      "r01", "view_left.png"),
    "arcade":           ("tsr_tjs_arcade_cabinet",      "r00", "view_low_front_left.png"),
    "lighthouse":       ("tsr_obj_lighthouse",          "r02", "view_low_front_left.png"),
    "gramophone":       ("tsr_obj_gramophone",          "r00", "view_front_right_34.png"),
    "radial":           ("tsr_cad_radial_engine_v2",    "r02", "view_front_right_34.png"),
    "chandelier":       ("tsr_tjs_deco_chandelier_v2",  "r02", "view_front_right_34.png"),
}
for name, (slug, rnd, view) in OBJ.items():
    d = R / slug / "artifacts" / "renders" / rnd
    jpg(d / view, name, 900)
    jpg(d / "sheet.png", f"{name}_sheet", 1400)

for name, slug in [("penny_farthing", "tsr_obj_penny_farthing"), ("arcade", "tsr_tjs_arcade_cabinet"),
                   ("lighthouse", "tsr_obj_lighthouse"), ("gramophone", "tsr_obj_gramophone"),
                   ("radial", "tsr_cad_radial_engine_v2"), ("chandelier", "tsr_tjs_deco_chandelier_v2")]:
    src = TT / f"{slug}.gif"
    if src.is_file():
        gif(src, f"tt_{name}")

# ---- articulated ----------------------------------------------------------
rc = R / "tsr_art_roll_cabinet"
jpg(rc / "deliverable/hero_cascade.png", "rollcab", 900)
jpg(rc / "artifacts/renders/r00/sheet.png", "rollcab_sheet", 1400)
jpg(rc / "deliverable/articulation_sheet.png", "rollcab_art", 1400)
gif(rc / "deliverable/joint_sweep.gif", "tt_rollcab")

ck = R / "tsr_art_grandfather_clock_v2"
jpg(ck / "deliverable/hero_door_open.png", "clock", 900)
jpg(ck / "artifacts/renders/r01/sheet.png", "clock_sheet", 1400)
jpg(ck / "deliverable/motion_strip.png", "clock_strip", 1400)
gif(ck / "deliverable/joint_motion.gif", "tt_clock")

# ---- scenes ---------------------------------------------------------------
SCN = {
    "temple": ("tsr_scn_temple_night_v2", "r01",
               [("Establishing_t0.png", "a"), ("Establishing_t1p5.png", "b"),
                ("CenserDetail_t0.png", "censer"), ("BridgeEye_t0.png", "bridge")]),
    "boat":   ("tsr_scn_boat_workshop_v2", "r03",
               [("Establishing_t0.png", "a"), ("Establishing_t1p5.png", "b"),
                ("WorkbenchEye_t0.png", "bench"), ("SkiffDetail_t0.png", "skiff"),
                ("StoveCornerDetail_t0.png", "stove")]),
    "alley":  ("tsr_scn_neon_alley_v2", "r04",
               [("Establishing_t0.png", "a"), ("Establishing_t1p5.png", "b"),
                ("LowAngleReflections_t0.png", "low"), ("SignDetail_t0.png", "sign")]),
}
for key, (slug, rnd, shots) in SCN.items():
    d = R / slug / "artifacts" / "renders" / rnd
    for fn, tag in shots:
        jpg(d / fn, f"{key}_{tag}", 1024)
    jpg(d / "sheet.png", f"{key}_sheet", 1400)

# ---- graphics -------------------------------------------------------------
GFX = {
    "aurora":    ("tsr_gfx_aurora_ridge_v3", "r00", "frame_t02.50.png"),
    "murmur":    ("tsr_ogl_murmuration",     "r03", "frame_t01.00.png"),
    "accretion": ("tsr_gfx_accretion_disc",  "r01", "frame_t02.50.png"),
    "rain":      ("tsr_gfx_rain_window",     "r00", "frame_t02.50.png"),
}
for name, (slug, rnd, frame) in GFX.items():
    d = R / slug / "artifacts" / "renders" / rnd
    jpg(d / frame, name, 1100)
    jpg(d / "sheet.png", f"{name}_strip", 1400)
    gif(R / slug / "deliverable" / "preview.gif", f"tt_{name}")

# ---- blender heroes (multi-language panel) --------------------------------
ASSETS = {
    "lantern": ("tsr_scn_temple_night_v2",  "stone_lantern"),
    "censer":  ("tsr_scn_temple_night_v2",  "bronze_censer"),
    "skiff":   ("tsr_scn_boat_workshop_v2", "clinker_skiff"),
    "stove":   ("tsr_scn_boat_workshop_v2", "potbelly_stove"),
}
sizes = {}
for name, (slug, snake) in ASSETS.items():
    d = R / slug / "_assets" / snake / "artifacts" / "renders" / "r00" / "assets" / snake
    jpg(d / "view_front_right_34.png", f"asset_{name}", 700)
    jpg(d / "sheet.png", f"asset_{name}_sheet", 1400)
    g = ASSET_TT / f"{snake}.gif"
    if g.is_file():
        gif(g, f"tt_asset_{name}")
    sizes[name] = (R / slug / "public" / "assets" / f"{snake}.glb").stat().st_size

(OUT.parent / "asset_glb_sizes.json").write_text(json.dumps(sizes, indent=1))
tot = sum(p.stat().st_size for p in OUT.iterdir())
print(f"{len(list(OUT.iterdir()))} files, {tot/1e6:.1f} MB")
print(json.dumps(sizes))
