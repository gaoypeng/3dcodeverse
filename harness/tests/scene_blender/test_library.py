"""The bpy starter library (D51): the effects catalog prints every signature as the lib declares it (a
parameter printed positional that the lib takes by keyword only is a TypeError in every scene that trusts
the row), and ONE Blender build proves every factory seats what it makes, names nothing twice and stays
under its triangle ceiling."""

from __future__ import annotations

import ast
import json
import re
import struct
from pathlib import Path

import pytest

from codeverse3d.config import get_settings
from codeverse3d.contracts.common import Language
from codeverse3d.contracts.plan import CameraPlan
from codeverse3d.languages import get_runtime
from codeverse3d.languages.scene_blender import lib_files, render_scene_py, write_example
from codeverse3d.prompts.catalog import language_text
from tests.scene_runtime.conftest import needs_browser

MODULES: dict[str, Path] = {p.stem: p for p in lib_files()}
CATALOG = language_text(Language.SCENE_BLENDER, "effects_catalog.md")
_ROW_MODULE = re.compile(r"`lib/([a-z_]+)\.py`")
_SPAN = re.compile(r"`([^`]+)`")
_CALL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(")
_METHOD = re.compile(r"^\.([a-z_][a-z0-9_]*)\(")
needs_blender = pytest.mark.skipif(not get_settings().resolve_blender(), reason="no Blender binary")


def _tree(stem: str) -> ast.Module:
    return ast.parse(MODULES[stem].read_text(), filename=str(MODULES[stem]))


def _defs(stem: str) -> tuple[dict[str, ast.FunctionDef], dict[str, dict[str, ast.FunctionDef]]]:
    """Top-level functions and {class: {method}} of a module."""
    funcs: dict[str, ast.FunctionDef] = {}
    classes: dict[str, dict[str, ast.FunctionDef]] = {}
    for node in _tree(stem).body:
        if isinstance(node, ast.FunctionDef):
            funcs[node.name] = node
        elif isinstance(node, ast.ClassDef):
            classes[node.name] = {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}
    return funcs, classes


def _boundary(fn: ast.FunctionDef) -> tuple[list[str], list[str], bool]:
    """(positional names, keyword-only names, takes ``**kwargs``) of a def, ``self`` dropped."""
    a = fn.args
    return [p.arg for p in a.posonlyargs + a.args if p.arg != "self"], [p.arg for p in a.kwonlyargs], a.kwarg is not None


_MORE = "_more"   # what a printed "…" parses as


def _printed(span: str) -> ast.FunctionDef | None:
    """A printed signature — ``instance(collection, source, *, height_at, …)``, ``.box(mat, loc, size)``,
    ``lib.place.seat(obj, height_at)`` — parsed as a def; None when the span is not one."""
    if "(" not in span or not span.endswith(")"):
        return None
    body = (span[1:] if span.startswith(".") else span).replace("…", _MORE).replace("...", _MORE)
    head, _, rest = body.partition("(")
    try:
        node = ast.parse(f"def {head.rsplit('.', 1)[-1]}({rest}: pass").body[0]
    except SyntaxError:
        return None
    return node if isinstance(node, ast.FunctionDef) else None


def _faults(printed: ast.FunctionDef, lib: ast.FunctionDef) -> list[str]:
    """How a printed signature misleads a caller of ``lib``: a parameter printed positional that the lib
    takes by keyword only, a ``*`` anywhere but at the lib's boundary (or missing), a name the lib lacks.
    ``…`` stands for "and the rest"; the lib spells a shadowing ``rng`` as ``rng_``."""
    a = printed.args
    names = [p.arg for p in a.posonlyargs + a.args]
    n_bare = len(names) - len(a.defaults)
    abbreviated = bool(names) and names[0] == _MORE
    lpos, lkw, var_kw = _boundary(lib)
    out: list[str] = []
    for i, p in enumerate([] if abbreviated else names):
        if p == _MORE:
            continue
        if i < len(lpos):
            if p.rstrip("_") != lpos[i].rstrip("_"):
                out.append(f"positional #{i + 1} is {p!r}, the lib's is {lpos[i]!r}")
        elif p in lkw:
            out.append(f"{p!r} is printed positional but the lib takes it by keyword only")
        elif not (var_kw and i >= n_bare):
            out.append(f"{p!r} is not a parameter of the lib")
    if a.kwonlyargs and not lkw:
        out.append("a `*` is printed but the lib has no keyword-only parameters")
    elif a.kwonlyargs and not abbreviated:
        printed_pos = [p for p in names if p != _MORE]
        if [p.rstrip("_") for p in printed_pos] != [p.rstrip("_") for p in lpos]:
            out.append(f"the `*` is printed after {printed_pos}, the lib's boundary is after {lpos}")
    elif lkw:
        out.append(f"the lib's `*` before {lkw} is not printed")
    out += [f"{p.arg!r} is not a parameter of the lib" for p in a.kwonlyargs
            if p.arg != _MORE and p.arg not in lkw and p.arg not in lpos and not var_kw]
    return out


def test_the_catalog_prints_every_signature_with_the_libs_keyword_only_boundary() -> None:
    """Regression: 15 catalog rows printed a REQUIRED keyword-only parameter as positional; a zone written
    from the row raised ``TypeError`` and was dropped from the build."""
    faults: list[str] = []
    checked = 0
    for row in [line for line in CATALOG.splitlines() if line.startswith("| ") and "`lib/" in line]:
        stem = _ROW_MODULE.findall(row)[0]
        funcs, classes = _defs(stem)
        methods = {m: fn for cls in classes.values() for m, fn in cls.items()}
        for span in _SPAN.findall(row):
            printed = _printed(span)
            if printed is None:
                continue
            lib = methods.get(printed.name) if span.startswith(".") else (funcs.get(printed.name) or classes.get(printed.name, {}).get("__init__"))
            if lib is not None:
                checked += 1
                faults += [f"lib/{stem}.py `{span}`: {f}" for f in _faults(printed, lib)]
    assert checked > 100, checked
    assert not faults, "\n".join(faults)


# --------------------------------------------------------------------------- one Blender build
#: every module exercised in one zone; each thing its own placement row (the gate's unit)
LIB_CHECK = '''"""lib_check: a representative call per library module, each its own placement row."""
from lib import architecture, figure, lighting, materials, mesh, motion, place, scatter, sky, terrain, vegetation, water
from mathutils import Matrix


def build(ctx):
    rng = ctx.rng(3)
    h = ctx.height_at
    rock = materials.mineral_surface("CheckRock", "6F6A60", stone=True)
    wood = materials.woodgrain(materials.material("CheckWood", "6B4A2B"))
    stone = materials.ashlar("CheckStone", "A89F8C")
    lime = materials.limestone("CheckLime", "C9BFA8")
    brick = materials.masonry("CheckBrick", "8B4A3A")
    alloy = materials.worn_alloy("CheckAlloy", "8C8C90", seed=2)
    bark = materials.material("CheckBark", "4A3524", roughness=0.9, noise_scale=6.0)
    leaves = materials.foliage("CheckLeaf", "4A7A2A", variants=3)
    coat = materials.material("CheckCoat", "8A3A3A")
    glow = materials.emissive("CheckGlow", "FFB270", 5.0)
    glass = materials.glass("CheckGlass")
    clear = materials.clear_water("CheckClear")
    foam = water.foam_shader("CheckFoam")

    b = mesh.Batch("Prims_0")   # primitives, a transformed view, a bevel; seated, spun by a driver
    b.box(wood, (-30.0, 24.0, 0.5), (1.0, 0.6, 1.0), rotation=0.3)
    b.sphere(rock, (-28.0, 24.0, 0.5), 0.5, subdivision=2)
    b.lathe(alloy, (-26.0, 24.0), [(0.0, 0.0), (0.4, 0.0), (0.3, 0.8), (0.45, 1.2), (0.0, 1.25)], sides=24)
    b.beam(wood, (-22.0, 24.0, 0.1), (-21.5, 24.3, 1.5), 0.08, end=0.04)
    b.transformed(Matrix.Translation((-16.0, 24.0, 0.0))).box(brick, (0.0, 0.0, 0.5), (1.0, 1.0, 1.0))
    prims = b.finish(ctx.collection, bevel=0.02)
    place.seat(prims[0], h)
    motion.spin(prims[0], "Z", 0.25, ctx)
    mesh.curve_object("Rope_0", [(-30.0, 28.0, 0.03), (-29.5, 28.5, 0.03), (-29.0, 28.0, 0.03)], 0.03, wood, ctx.collection)

    r = mesh.Batch("Rocks_0")   # terrain recipes stand on h
    terrain.boulder(r, rock, (-8.0, 28.0, h(-8.0, 28.0)), 1.2, seed=1)
    terrain.rock_field(r, rock, (-4.0, 28.0), 3.0, 10, h, rng)
    r.finish(ctx.collection)

    for i, kind in enumerate(("broadleaf", "oak", "cypress", "pine")):   # the four tree kinds
        t = mesh.Batch(f"Tree{kind.title()}_0")
        x = 10.0 + 6.0 * i
        vegetation.tree(t, (x, 24.0, h(x, 24.0)), 7.0, bark, leaves[i % 3], seed=i, kind=kind, max_leaves=2500)
        t.finish(ctx.collection)
    p = mesh.Batch("Plants_0")
    vegetation.shrub(p, bark, leaves[0], (10.0, 30.0, h(10.0, 30.0)), scale=1.2, seed=4)
    vegetation.fern(p, leaves[2], (16.0, 30.0, h(16.0, 30.0)), seed=2)
    p.finish(ctx.collection)
    patch = terrain.ground("MeadowSurface", ctx.collection, size=6.0, segments=6, height_at=h, material=None, center=(-20.0, 32.0))
    patch.hide_render = True
    patch.hide_viewport = True
    vegetation.grass_patch("Meadow_0", ctx.collection, patch, density=8.0, seed=1)

    house = mesh.Batch("CheckHouse_0")   # the example already has a House_0: names are global
    architecture.timber_house(house, {"tile": brick, "trim": wood}, -28.0, 33.0, 6.0, 5.0, 2,
                              {"wall": lime, "timber": wood, "door": coat, "window": glass}, rng, 1)
    place.seat(house.finish(ctx.collection)[0], h)
    bridge = mesh.Batch("Bridge_0")
    architecture.stone_bridge(bridge, stone, rock, center=(-16.0, 38.0, h(-16.0, 38.0)), span=12.0, width=3.0, rise=2.0, height_at=h)
    bridge.finish(ctx.collection)
    lan = mesh.Batch("Lantern_0")
    architecture.lantern(lan, glow, wood, (16.0, 44.0, 2.0))
    hung = lan.finish(ctx.collection)[0]
    hung["placement"] = "free"
    sign = architecture.text_mesh("Sign_0", ctx.collection, "LIB", (20.0, 42.0, h(20.0, 42.0)), 0.6, coat)
    place.seat(sign, h)
    # a glazed room shell (the env's recipe) stood on the ground, so the gate measures it as ONE row
    cabin = architecture.room_shell(ctx.collection, alloy, center=(0.0, -28.0, h(0.0, -28.0) + 1.6), extents=(4.0, 3.0, 2.8),
                                    openings=(("front", 0.0, 1.2, 0.0, 2.2),), glazed=True)
    assert [o.name for o in cabin[:4]] == ["RoomShell", "RoomShellFloor", "Ceiling", "CeilingGlass"]

    fall = water.waterfall("Fall_0", ctx.collection, (6.0, 44.0, h(6.0, 44.0)), width=2.0, height=3.0, ctx=ctx)
    assert len(fall) == 5
    fountain = mesh.Batch("Fountain_0")
    spray = water.fountain_jets(fountain, ctx.collection, clear, center=(-2.0, 44.0, h(-2.0, 44.0)), nozzle_r=0.3, landing_r=1.4,
                                count=10, ctx=ctx, seed=3)
    water.droplets(fountain, foam, (-2.0, 44.0, h(-2.0, 44.0) + 0.3), 1.0, h(-2.0, 44.0), rng, count=60)
    fountain.finish(ctx.collection)
    assert spray is not None and spray["placement"] == "free"

    folk = mesh.Batch("Visitors_0")
    figure.figure(folk, coat, coat, (-12.0, 44.0, h(-12.0, 44.0)), heading=place.heading((-12.0, 44.0), (0.0, 0.0)), pose=0.5)
    folk.finish(ctx.collection)
    lighting.point_light(ctx.collection, "CheckBulb", (-2.0, 44.0, 2.0), "FFD9A0", 60.0)
    sky.aurora("AuroraSky_0", ctx.collection, (0.0, 80.0, 120.0), ctx=ctx)
    stars = mesh.Batch("Stars_0")
    sky.stars(stars, glow, seed=1, count=200)
    stars.finish(ctx.collection)[0]["placement"] = "free"

    crate = ctx.assets["crate"]   # an instance, a stack, the self-checks, a keyframed drift
    one = scatter.instance(ctx.collection, crate, "LibCrate_0", -14.0, 44.0, height_at=h, rot_z=0.4)
    yaw = place.face_toward(one, (0.0, 0.0))
    # the same size and yaw as the base: a smaller or turned crate's corner columns overhang the
    # open-topped base, which the gate (rightly) reads as floating
    top = scatter.instance(ctx.collection, crate, "StackedCrate_0", -14.0, 44.0, height_at=h, rot_z=yaw, scale=1.0)
    place.rest_on(top, one)
    pts = scatter.poisson(rng, area=(0.0, 40.0, 4.0, 43.0), min_dist=1.4, max_count=2, mask=lambda x, y: 1.0)
    assert len(pts) == 2
    scatter.scatter(ctx.collection, crate, "CheckCrate", pts, height_at=h, rng=rng, scale=(0.8, 1.1))
    assert place.clear_of([prims[0], one, hung]) == []
    motion.drift(one, (0.0, 0.5, 0.0), ctx)
    return ctx.collection
'''

#: the catalog's per-call ceilings, on the GLB the gate reads (evaluated triangles per root)
CEILINGS = {
    "Prims_0": 12_000, "Rocks_0": 4_000, "TreeBroadleaf_0": 9_000, "TreeOak_0": 9_000, "TreeCypress_0": 9_000, "TreePine_0": 600,
    "Plants_0": 3_000, "CheckHouse_0": 14_000, "Bridge_0": 1_500, "Lantern_0": 800, "Sign_0": 2_000, "Fall_0": 2_500,
    "Fountain_0": 6_000, "Visitors_0": 2_000, "Stars_0": 600, "RoomShell": 2_000,
}
SEATED = {"Prims_0", "Rope_0", "Rocks_0", "TreeBroadleaf_0", "TreeOak_0", "TreeCypress_0", "TreePine_0", "Plants_0",
          "CheckHouse_0", "Bridge_0", "Sign_0", "Fall_0", "Fountain_0", "Visitors_0", "LibCrate_0", "StackedCrate_0",
          "CheckCrate_0", "CheckCrate_1", "RoomShell"}
EXEMPT = {"Meadow_0": "instanced", "Lantern_0": "free", "Fountain_0Spray_0": "free", "AuroraSky_0": "free", "Stars_0": "free"}


def _glb_json(path: Path) -> dict:
    data = path.read_bytes()
    assert data[:4] == b"glTF"
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + length])


def _subtree_tris(g: dict, ix: int) -> int:
    node = g["nodes"][ix]
    n = 0
    if "mesh" in node:
        per = sum(g["accessors"][p["indices"]]["count"] // 3 for p in g["meshes"][node["mesh"]]["primitives"])
        inst = node.get("extensions", {}).get("EXT_mesh_gpu_instancing")
        n += per * (g["accessors"][inst["attributes"]["TRANSLATION"]]["count"] if inst else 1)
    return n + sum(_subtree_tris(g, c) for c in node.get("children", []))


@pytest.mark.blender
@pytest.mark.node
@needs_blender
@needs_browser
def test_one_blender_build_seats_what_every_factory_makes(tmp_ws) -> None:
    """The example scene plus a zone that calls every module, built ONCE through the wrapper
    and measured by the gate's own placement table: every root rests on what is under it
    (no floating, no burial past the 10 cm WARN), the clouds and hanging things carry their
    exemption, no name was deduplicated, every driver is simple, nothing touched the render
    settings, and each root stays under the catalog's triangle ceiling."""
    rt = get_runtime(Language.SCENE_BLENDER)
    write_example(tmp_ws)
    (tmp_ws.src / "zones" / "lib_check.py").write_text(LIB_CHECK)
    cams = [CameraPlan(name="overview", position=(0.0, 28.0, 75.0), look_at=(0.0, 0.0, -20.0), fov=50.0)]
    (tmp_ws.src / "scene.py").write_text(render_scene_py(["hamlet", "lakeside", "orchard", "lib_check"], ["crate", "lantern_post"], [], cams))
    assert rt.lint(tmp_ws).passed
    res = rt.build(tmp_ws)
    assert res.ok, res.stdout_tail
    report = json.loads((tmp_ws.artifacts / "bpy_build.json").read_text())
    assert report["errors"] == [] and not [w for w in report["warnings"] if "render settings" in w], report
    assert res.census["bpy"]["drivers_invalid"] == [] and res.census["bpy"]["handlers"] == []
    rows = {r["name"]: r for r in res.census["placement"]["assets"]}
    check = {n: r for n, r in rows.items() if r.get("zone") == "LibCheck"}
    assert set(check) >= SEATED, sorted(SEATED - set(check))
    for name in SEATED:
        r = check[name]
        assert r["supported"] and not r["floating"], (name, r)
        assert r["sunk_m"] <= 0.10, (name, r["sunk_m"], r["sunk_into"])
    for name, why in EXEMPT.items():
        assert check[name]["exempt"] == why, (name, check[name].get("exempt"))
    assert check["StackedCrate_0"]["support"] == "LibCrate_0"
    # the example's own rows are seated too, and nothing interpenetrates
    for r in rows.values():
        if not r["exempt"]:
            assert r["supported"] and not r["floating"] and r["sunk_m"] <= 0.10, r
    assert res.census["placement"]["interpenetrations"] == []
    # names: no Blender dedupe suffix anywhere, every root once
    g = _glb_json(tmp_ws.artifacts / "census.glb")
    zone = next(n for n in g["nodes"] if n["name"] == "LibCheck")
    roots = [g["nodes"][c]["name"] for c in zone["children"]]
    assert len(roots) == len(set(roots)) and not [n for n in roots if re.search(r"\.\d{3}$", n)], roots
    tris = {g["nodes"][c]["name"]: _subtree_tris(g, c) for c in zone["children"]}
    over = {n: (tris[n], cap) for n, cap in CEILINGS.items() if tris[n] > cap}
    assert not over, over
    assert all(tris[n] > 0 for n in CEILINGS)

