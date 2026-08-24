"""Worked plan examples — one compact, valid plan per track for the planner's system
prompt.  Concrete beats abstract (design law 2): the example is what actually teaches
the model the shape of ``children`` and ``detail_hint``, so the chair carries a nested
Backrest and the drawer unit a four-sub-part Drawer.
"""

from __future__ import annotations

from typing import Any

from codeverse.contracts.common import Track


def plan_example(track: Track) -> dict[str, Any]:
    """Compact, valid worked example per track (concrete beats abstract)."""
    if track is Track.SCENE:
        return {
            "title": "Harbour at dusk", "summary": "Small fishing harbour: quay, two boats, lighthouse, calm water.",
            "setting": "Breton coast, 1960s, clear dusk", "mood": "calm, warm low sun",
            "bounds": {"center": [0, 5, 0], "extents": [120, 30, 120]},
            "environment": "Sky gradient orange→indigo, sun at azimuth 250° elevation 8°, light fog density 0.004, flat sea plane y=0, stone quay at y=1.2.",
            "zones": [
                {"name": "Quay", "description": "L-shaped granite quay with bollards and crates", "bbox": {"center": [-20, 1.2, 0], "extents": [40, 3, 60]}, "contents": ["Bollard", "Crate"]},
                {"name": "Water", "description": "calm harbour basin with two moored boats", "bbox": {"center": [20, 0, 0], "extents": [60, 1, 80]}, "contents": ["FishingBoat"]},
            ],
            "assets": [
                {"name": "FishingBoat", "kind": "threejs", "description": "8 m wooden boat, wheelhouse aft, red hull", "approx_size_m": [8, 3.5, 3], "instances_hint": 2},
                {"name": "Bollard", "kind": "threejs", "description": "cast-iron mooring bollard", "approx_size_m": [0.3, 0.5, 0.3], "instances_hint": 6},
                {"name": "Crate", "kind": "blender_glb", "description": "wooden fish crate, slatted", "approx_size_m": [0.6, 0.3, 0.4], "instances_hint": 8},
            ],
            "effects": [{"name": "WaterRipple", "kind": "glsl_material", "description": "animated normal ripples on the sea plane", "target": "Water"}],
            "animation": ["boats bob ±0.05 m at 0.3 Hz", "water ripples scroll"],
            "cameras": [{"name": "Overview", "position": [45, 18, 55], "look_at": [0, 1, 0], "fov": 50, "purpose": "establishing shot"},
                        {"name": "QuayEye", "position": [-5, 2.8, 25], "look_at": [15, 1, -10], "fov": 60, "purpose": "eye level along the quay"}],
            "acceptance": [{"id": "a1", "text": "Two boats float on the water inside the basin", "how": "visual", "priority": "must"},
                           {"id": "a2", "text": "Quay top at y≈1.2 m, boats' waterline at y≈0", "how": "probe", "priority": "must"}],
        }
    parts = [
        {"name": "Seat", "role": "horizontal seat board", "description": "solid oak board, 40 mm thick, rounded front edge (r=10 mm)",
         "bbox": {"center": [0, 0, 0.43], "extents": [0.42, 0.40, 0.04]}, "material": "oiled oak", "attach_to": None, "symmetry": "mirror_x", "instances": 1,
         "children": [], "detail_hint": "the eye lands here first — keep the 10 mm bullnose and the 3 mm chamfer under the front edge"},
        {"name": "FrontLeg", "role": "front leg", "description": "tapered round leg 35→25 mm, splayed 6° outward",
         "bbox": {"center": [0.17, -0.16, 0.205], "extents": [0.035, 0.035, 0.41]}, "material": "oiled oak", "attach_to": "Seat", "symmetry": "mirror_x", "instances": 2,
         "children": [], "detail_hint": ""},
        {"name": "Backrest", "role": "slatted back assembly", "description": "three vertical slats in a curved top rail and a lower rail",
         "bbox": {"center": [0, 0.19, 0.66], "extents": [0.40, 0.03, 0.42]}, "material": "oiled oak", "attach_to": "Seat", "symmetry": "none", "instances": 1,
         "detail_hint": "an assembly, not a panel: the gaps between the slats must be visible from the front and the side",
         "children": [
             {"name": "TopRail", "role": "curved crest rail", "description": "60 mm deep rail bowed 18 mm back on a 0.9 m radius, ends rounded r=8 mm",
              "bbox": {"center": [0, 0.19, 0.85], "extents": [0.40, 0.03, 0.06]}, "material": "oiled oak", "instances": 1},
             {"name": "Slat", "role": "vertical back slat", "description": "12 mm thick slat, 55 mm wide, 4 mm chamfer both faces, 30 mm gap to its neighbour",
              "bbox": {"center": [0, 0.19, 0.66], "extents": [0.055, 0.012, 0.34]}, "material": "oiled oak", "instances": 3},
             {"name": "LowerRail", "role": "rail the slats seat into", "description": "40 mm rail with three 12 × 55 mm mortises on 85 mm centres",
              "bbox": {"center": [0, 0.19, 0.48], "extents": [0.40, 0.03, 0.04]}, "material": "oiled oak", "instances": 1},
         ]},
    ]
    base: dict[str, Any] = {
        "object_name": "DiningChair", "summary": "Mid-century oak dining chair, 0.45 × 0.50 × 0.82 m.",
        "overall_bbox": {"center": [0, 0, 0.41], "extents": [0.45, 0.50, 0.82]},
        "style_notes": "Danish mid-century: tapered legs, thin spindles, warm oak, no ornament.",
        "parts": parts,
        "acceptance": [{"id": "a1", "text": "Seat top at 0.45 m ± 0.02 m above the ground", "how": "measure", "priority": "must"},
                       {"id": "a2", "text": "Four legs, all touching the ground, splayed outward", "how": "visual", "priority": "must"}],
    }
    if track is Track.ARTICULATED_OBJECT:
        base["object_name"] = "DeskDrawerUnit"
        base["parts"] = [
            {"name": "Cabinet", "role": "fixed carcass", "description": "18 mm carcass, 6 mm rebated back, 3 mm shadow gap round the drawer opening",
             "bbox": {"center": [0, 0, 0.3], "extents": [0.4, 0.5, 0.6]}, "material": "painted MDF", "attach_to": None, "symmetry": "none", "instances": 1,
             "children": [], "detail_hint": "the opening's shadow gap is what makes the drawer read as a drawer"},
            {"name": "Drawer", "role": "sliding drawer (ONE link, four sub-parts)", "description": "drawer box running on side runners",
             "bbox": {"center": [0, -0.02, 0.45], "extents": [0.36, 0.46, 0.2]}, "material": "painted MDF", "attach_to": "Cabinet", "symmetry": "none", "instances": 1,
             "detail_hint": "one rigid body, but never one box",
             "children": [
                 {"name": "Front", "role": "drawer face", "description": "18 mm face, 4 mm bullnose, overhangs the box 8 mm on every side",
                  "bbox": {"center": [0, -0.24, 0.45], "extents": [0.36, 0.018, 0.19]}, "material": "painted MDF", "instances": 1},
                 {"name": "Box", "role": "drawer body", "description": "12 mm sides, 8 mm base set into a 6 mm groove, open top",
                  "bbox": {"center": [0, 0.0, 0.45], "extents": [0.33, 0.42, 0.17]}, "material": "birch ply", "instances": 1},
                 {"name": "Knob", "role": "pull", "description": "turned knob 32 mm dia, 22 mm proud, on a 10 mm neck",
                  "bbox": {"center": [0, -0.26, 0.45], "extents": [0.032, 0.032, 0.032]}, "material": "brushed steel", "instances": 1},
                 {"name": "Runner", "role": "side runner", "description": "10 × 10 mm strip along each side, full depth",
                  "bbox": {"center": [0.17, 0.0, 0.40], "extents": [0.01, 0.42, 0.01]}, "material": "birch ply", "instances": 2},
             ]},
        ]
        base["root_link"] = "Cabinet"
        base["joints"] = [{"name": "DrawerSlide", "type": "prismatic", "parent": "Cabinet", "child": "Drawer", "axis": [0, -1, 0],
                           "pivot": [0, -0.02, 0.45], "lower": 0.0, "upper": 0.35, "rest": 0.0, "motion": "drawer pulls out towards -Y"}]
        base["acceptance"].append({"id": "j1", "text": "Drawer slides out 0.35 m along -Y without penetrating the cabinet", "how": "articulation", "priority": "must"})
    return base


__all__ = ["plan_example"]
