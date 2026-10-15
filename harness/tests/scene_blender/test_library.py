"""The bpy starter library (D51 for scene_blender): the effects catalog indexes exactly it and prints
every signature as the lib declares it (the names AND the keyword-only ``*`` boundary — a parameter
printed positional that the lib takes by keyword only is a TypeError in every scene that trusts the
row), the cookbook and the bpy skills never print one positionally either, the signatures the cookbook
cites hold, a glazed room shell is glass on a frame that lets the sun in, and ONE Blender build proves
that every factory seats what it makes (the placement gate's own rows), names nothing twice and stays
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
from codeverse3d.languages.scene_blender import LIB_DIR, lib_files, render_scene_py, write_example
from codeverse3d.prompts.catalog import language_text
from codeverse3d.skills import skills_dir
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


def _params(fn: ast.FunctionDef) -> list[str]:
    """The parameter names with the keyword-only boundary as ``"*"``: the shape a caller must honour,
    not just the names (review 2026-10-15 finding 3: a name-only pin let 15 catalog rows print a
    required keyword-only parameter as positional)."""
    pos, kw, _ = _boundary(fn)
    return pos + (["*", *kw] if kw else [])


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


def _faults(printed: ast.FunctionDef, lib: ast.FunctionDef, *, strict: bool) -> list[str]:
    """How a printed signature misleads a caller of ``lib``: a parameter printed positional that the
    lib takes by keyword only (bare always; printed with a default only under ``strict``, the
    catalog's rule), a ``*`` anywhere but at the lib's boundary (or missing under ``strict``), a name
    the lib does not have.  ``…`` stands for "and the rest"; a head of ``…`` means "keywords only";
    the lib spells a shadowing ``rng`` as ``rng_``, which a positional caller never sees; a printed
    keyword the lib takes through ``**kwargs`` is a pass-through."""
    a = printed.args
    names = [p.arg for p in a.posonlyargs + a.args]
    n_bare = len(names) - len(a.defaults)
    star, abbreviated = bool(a.kwonlyargs), bool(names) and names[0] == _MORE
    lpos, lkw, var_kw = _boundary(lib)
    out: list[str] = []
    for i, p in enumerate([] if abbreviated else names):
        if p == _MORE:
            continue
        if i >= n_bare and not strict:            # `size=…` in prose is a keyword wherever it is printed
            if p not in lpos and p not in lkw and not var_kw:
                out.append(f"{p!r} is not a parameter of the lib")
        elif i < len(lpos):
            if p.rstrip("_") != lpos[i].rstrip("_"):
                out.append(f"positional #{i + 1} is {p!r}, the lib's is {lpos[i]!r}")
        elif p in lkw:
            out.append(f"{p!r} is printed positional but the lib takes it by keyword only")
        elif not (var_kw and i >= n_bare):
            out.append(f"{p!r} is not a parameter of the lib")
    if star and not lkw:
        out.append("a `*` is printed but the lib has no keyword-only parameters")
    elif star and not abbreviated:
        printed_pos = [p for p in names if p != _MORE]
        if [p.rstrip("_") for p in printed_pos] != [p.rstrip("_") for p in lpos]:
            out.append(f"the `*` is printed after {printed_pos}, the lib's boundary is after {lpos}")
    elif strict and lkw:
        out.append(f"the lib's `*` before {lkw} is not printed")
    out += [f"{p.arg!r} is not a parameter of the lib" for p in a.kwonlyargs
            if p.arg != _MORE and p.arg not in lkw and p.arg not in lpos and not var_kw]
    return out


# --------------------------------------------------------------------------- the tree itself
def test_the_library_is_thirteen_modules_under_the_starter_tree() -> None:
    assert LIB_DIR.name == "lib" and (LIB_DIR / "__init__.py").is_file()
    assert set(MODULES) == {"noise", "mesh", "materials", "terrain", "sky", "lighting", "water", "vegetation",
                            "architecture", "scatter", "place", "figure", "motion"}
    for stem, path in MODULES.items():
        lines = path.read_text().splitlines()
        assert len(lines) < 500, f"{stem}: {len(lines)} lines (the catalog promises small modules)"
        doc = ast.get_docstring(_tree(stem)) or ""
        assert doc.startswith(f"lib/{stem}.py"), f"{stem}: the module docstring names another file"


# --------------------------------------------------------------------------- the catalog
def test_the_catalog_has_one_row_per_module_naming_only_real_calls() -> None:
    """The catalog is the model's only index of the library: a call it names that does not
    exist is a build error in every scene that trusts it, and a module it leaves out is dead
    weight in every workspace."""
    rows = [line for line in CATALOG.splitlines() if line.startswith("| ") and "`lib/" in line]
    named: list[str] = []
    for row in rows:
        mods = _ROW_MODULE.findall(row)
        assert len(mods) == 1, f"a row names {mods}: one module per row\n{row[:120]}"
        stem = mods[0]
        assert stem in MODULES, f"{stem}: no such module"
        named.append(stem)
        funcs, classes = _defs(stem)
        methods = {m for cls in classes.values() for m in cls}
        for span in _SPAN.findall(row):
            if m := _CALL.match(span):
                assert m.group(1) in funcs or m.group(1) in classes, f"lib/{stem}.py has no {m.group(1)!r}"
            elif m := _METHOD.match(span):
                assert m.group(1) in methods, f"lib/{stem}.py's class has no method {m.group(1)!r}"
    assert sorted(named) == sorted(MODULES), (sorted(set(MODULES) - set(named)), sorted(set(named) - set(MODULES)))
    assert "| Want | Call |" in CATALOG and "## What differs on this renderer" in CATALOG


def test_the_catalog_prints_every_signature_with_the_libs_keyword_only_boundary() -> None:
    """Review 2026-10-15 finding 3: 15 rows printed a REQUIRED keyword-only parameter as if positional
    (`instance(collection, source, name, x, y, height_at, …)` for a lib `…, x, y, *, height_at`) and 44
    more an optional one; a zone written from the row raised ``TypeError`` and was dropped from the
    build.  Every printed signature carries the lib's ``*`` exactly where the lib has it, its
    positional names in the lib's order, and no name the lib lacks."""
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
                faults += [f"lib/{stem}.py `{span}`: {f}" for f in _faults(printed, lib, strict=True)]
    assert checked > 100, checked
    assert not faults, "\n".join(faults)


def _prose_spans(text: str):
    """(line, span) for every backticked span of a markdown text outside its fenced code."""
    in_code = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code:
            for span in _SPAN.findall(line):
                yield n, span


def test_the_cookbook_and_the_bpy_skills_never_print_a_keyword_only_parameter_positionally() -> None:
    """The weaker rule for prose the catalog does not own (the cookbook's snippets RUN in Blender —
    tests/prompts/test_snippets_run.py — so only its printed signatures can mislead): a keyword-only
    parameter is never printed bare, a printed ``*`` sits where the lib's is, every name exists.
    Lib names are unique across the modules, so a bare name finds its def."""
    funcs: dict[str, ast.FunctionDef] = {}
    methods: dict[str, ast.FunctionDef] = {}
    for stem in MODULES:
        fs, classes = _defs(stem)
        for name, fn in fs.items():
            assert name.startswith("_") or name not in funcs, f"{name!r} is defined in two lib modules"
            funcs[name] = fn
        for cls, ms in classes.items():
            funcs[cls] = ms.get("__init__", funcs.get(cls))
            methods.update(ms)
    texts = {"scene_blender/cookbook.md": language_text(Language.SCENE_BLENDER, "cookbook.md")}
    texts |= {str(md.relative_to(skills_dir().parent)): md.read_text() for md in sorted(skills_dir().glob("c3d-bpy-scene-*/**/*.md"))}
    assert len(texts) >= 4, sorted(texts)    # the cookbook and three SKILL.md at least
    faults: list[str] = []
    for where, text in texts.items():
        for n, span in _prose_spans(text):
            printed = _printed(span)
            lib = None if printed is None else (methods.get(printed.name) if span.startswith(".") else funcs.get(printed.name))
            if lib is not None:
                faults += [f"{where}:{n} `{span}`: {f}" for f in _faults(printed, lib, strict=False)]
    assert not faults, "\n".join(faults)


# --------------------------------------------------------------------------- the signatures the cookbook cites
SIGNATURES = [
    ("noise", "fbm2", ["x", "y", "*", "octaves", "lacunarity", "gain", "seed"]),
    ("noise", "mountain_height", ["x", "y", "*", "seed", "amplitude", "frequency"]),
    ("mesh", "Batch.finish", ["collection", "*", "bevel", "parent", "name_prefix"]),
    ("mesh", "Batch.beam", ["mat", "a", "b", "radius", "end", "sides"]),
    ("mesh", "curve_object", ["name", "points", "radius", "mat", "collection", "*", "cyclic"]),
    ("materials", "material", ["name", "color", "roughness", "metal", "*", "noise_scale", "bump", "emission"]),
    ("materials", "water", ["name", "color", "roughness", "scale", "*", "flow", "ctx"]),
    ("terrain", "make_height_at", ["*", "seed", "relief", "scale", "flat_radius", "base"]),
    ("terrain", "ground", ["name", "collection", "*", "size", "segments", "height_at", "material", "center", "smooth"]),
    ("sky", "sun_world", ["scene", "*", "elevation_deg", "azimuth_deg", "strength", "dust"]),
    ("sky", "haze", ["collection", "loc", "size", "density", "color", "name"]),
    ("lighting", "sun", ["collection", "*", "azimuth_deg", "elevation_deg", "color", "energy", "angle_deg", "name"]),
    ("water", "water_surface", ["name", "collection", "*", "size", "level", "material", "ripple", "seed", "height_fn"]),
    ("vegetation", "tree", ["batch", "loc", "height", "bark", "foliage", "*", "seed", "kind", "max_leaves"]),
    ("vegetation", "grass_patch", ["name", "collection", "surface", "*", "blade", "density", "seed", "height", "color"]),
    ("architecture", "room_shell", ["collection", "mat", "*", "center", "extents", "thickness", "openings", "glazed"]),
    ("architecture", "text_mesh", ["name", "collection", "body", "loc", "size", "mat", "*", "rotation", "align", "extrude"]),
    ("scatter", "instance", ["collection", "source", "name", "x", "y", "*", "height_at", "rot_z", "scale", "sink"]),
    ("scatter", "gn_scatter", ["name", "collection", "surface", "instance_obj", "*", "density", "seed", "align_normal", "scale_range"]),
    ("place", "seat", ["obj", "height_at", "*", "sink", "columns"]),
    ("place", "clear_of", ["objs", "min_gap", "*", "overlap"]),
    ("figure", "figure", ["batch", "cloth", "skin", "center", "*", "scale", "heading", "pose"]),
    ("motion", "spin", ["obj", "axis", "turns_per_s", "ctx"]),
    ("motion", "shape_key_sweep", ["obj", "key_name", "ctx", "*", "from_value", "to_value"]),
]


@pytest.mark.parametrize(("stem", "name", "params"), SIGNATURES, ids=[f"{s}.{n}" for s, n, _ in SIGNATURES])
def test_the_signatures_the_catalog_and_cookbook_cite(stem: str, name: str, params: list[str]) -> None:
    """Names AND the keyword-only boundary (``"*"``): the prompts cite these by position."""
    funcs, classes = _defs(stem)
    fn = classes[name.split(".")[0]][name.split(".")[1]] if "." in name else funcs[name]
    assert _params(fn) == params


def test_every_factory_takes_its_collection_or_batch_explicitly() -> None:
    """Nothing reads ``bpy.context.collection`` / ``bpy.context.object``: a factory links where it
    is told (the zone's collection); ``bpy.ops`` never appears (it needs a UI context the build
    has none of, and rebuilds the scene state on every call); no deprecated ``use_nodes``, no
    ``ShaderNodeMixRGB`` (the 5.x node is ``ShaderNodeMix``).  Read off the AST, not the prose."""
    from codeverse3d.languages._ast_lint import dotted

    for stem in MODULES:
        names: set[str] = set()
        consts: set[str] = set()
        for node in ast.walk(_tree(stem)):
            if isinstance(node, (ast.Attribute, ast.Name)):
                names.add(dotted(node))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                consts.add(node.value)
        assert not [n for n in names if n.startswith(("bpy.ops.", "bpy.context.collection", "bpy.context.object"))], stem
        assert not [n for n in names if n.endswith(".use_nodes")], stem
        assert "ShaderNodeMixRGB" not in consts, stem


# --------------------------------------------------------------------------- one Blender build
#: every module exercised in one zone; each thing its own placement row (the gate's unit)
LIB_CHECK = '''"""lib_check: one representative call per library module, each its own placement row."""
from lib import architecture, figure, lighting, materials, mesh, motion, place, scatter, sky, terrain, vegetation, water
from mathutils import Matrix

SEED = 3


def build(ctx):
    rng = ctx.rng(SEED)
    h = ctx.height_at
    rock = materials.mineral_surface("CheckRock", "6F6A60", stone=True)
    wood = materials.woodgrain(materials.material("CheckWood", "6B4A2B"))
    stone = materials.ashlar("CheckStone", "A89F8C")
    lime = materials.limestone("CheckLime", "C9BFA8")
    brick = materials.masonry("CheckBrick", "8B4A3A")
    alloy = materials.worn_alloy("CheckAlloy", "8C8C90", seed=2)
    paint = materials.chipped_paint(materials.material("CheckPaint", "2F5F8F"), "2F5F8F", "7A7A72")
    bark = materials.material("CheckBark", "4A3524", roughness=0.9, noise_scale=6.0)
    leaves = materials.foliage("CheckLeaf", "4A7A2A", variants=3)
    coats = [materials.material("CheckCoat", "8A3A3A"), materials.tint(materials.material("CheckCoat2", "3A3A8A"), 0.3),
             materials.material("CheckSkin", "D9A880")]
    glow = materials.emissive("CheckGlow", "FFB270", 5.0)
    glass = materials.glass("CheckGlass")
    clear = materials.clear_water("CheckClear")
    foam = water.foam_shader("CheckFoam")
    materials.snow("CheckSnow")
    materials.ice("CheckIce")
    materials.water("CheckFlow", ctx=ctx)

    # mesh: every primitive, a transformed view, a bevel; seated afterwards, spun by a driver
    b = mesh.Batch("Prims_0")
    b.box(wood, (-30.0, 24.0, 0.5), (1.0, 0.6, 1.0), rotation=0.3)
    b.sphere(rock, (-28.0, 24.0, 0.5), 0.5, subdivision=2)
    b.lathe(alloy, (-26.0, 24.0), [(0.0, 0.0), (0.4, 0.0), (0.3, 0.8), (0.45, 1.2), (0.0, 1.25)], sides=24)
    b.cone(paint, (-24.0, 24.0, 0.0), 0.4, 1.2, top=0.1)
    b.beam(wood, (-22.0, 24.0, 0.1), (-21.5, 24.3, 1.5), 0.08, end=0.04)
    b.torus(alloy, (-20.0, 24.0, 0.3), 0.5, 0.08)
    b.ring(alloy, (-18.0, 24.0, 0.64), (0.0, 1.0, 0.0), 0.6, 0.04)
    b.transformed(Matrix.Translation((-16.0, 24.0, 0.0))).box(brick, (0.0, 0.0, 0.5), (1.0, 1.0, 1.0))
    prims = b.finish(ctx.collection, bevel=0.02)
    place.seat(prims[0], h)
    motion.spin(prims[0], "Z", 0.25, ctx)
    mesh.curve_object("Rope_0", [(-30.0, 28.0, 0.03), (-29.5, 28.5, 0.03), (-29.0, 28.0, 0.03)], 0.03, wood, ctx.collection)
    tri = mesh.mesh_object("Tri_0", [(-12.0, 24.0, 0.0), (-11.0, 24.0, 0.0), (-12.0, 25.0, 0.0), (-11.5, 24.5, 1.0)],
                           [(0, 1, 3), (1, 2, 3), (2, 0, 3), (0, 2, 1)], rock, ctx.collection)
    mesh.parent_under([tri], ctx.collection, "Parented_0")

    # terrain recipes stand on h
    r = mesh.Batch("Rocks_0")
    terrain.boulder(r, rock, (-8.0, 28.0, h(-8.0, 28.0)), 1.2, seed=1)
    terrain.rock_field(r, rock, (-4.0, 28.0), 3.0, 10, h, rng)
    terrain.talus(r, rock, (0.0, 24.0, h(0.0, 24.0)), 2.5, 2)
    terrain.basalt_column(r, [rock, lime], (4.0, 29.0, h(4.0, 29.0)), 1.6, 2.5, rng, lean=(0.05, 0.0))
    r.finish(ctx.collection)
    assert 0.0 <= terrain.shoreline_mask(1.0, 1.0, -0.3, h) <= 1.0

    # vegetation: the four tree kinds, the small plants, a cloud, a stand
    for i, kind in enumerate(("broadleaf", "oak", "cypress", "pine")):
        t = mesh.Batch(f"Tree{kind.title()}_0")
        x = 10.0 + 6.0 * i
        vegetation.tree(t, (x, 24.0, h(x, 24.0)), 7.0, bark, leaves[i % 3], seed=i, kind=kind, max_leaves=2500)
        t.finish(ctx.collection)
    p = mesh.Batch("Plants_0")
    vegetation.shrub(p, bark, leaves[0], (10.0, 30.0, h(10.0, 30.0)), scale=1.2, seed=4)
    vegetation.leaf_spray(p, leaves[1], (13.0, 30.0, h(13.0, 30.0) + 0.5), rng)
    vegetation.fern(p, leaves[2], (16.0, 30.0, h(16.0, 30.0)), seed=2)
    vegetation.tropical(p, leaves[0], bark, (19.0, 30.0, h(19.0, 30.0)), seed=5)
    vegetation.grass_clump(p, leaves, 22.0, 30.0, h(22.0, 30.0), rng)
    vegetation.perennial_bed(p, (26.0, 30.0), 1.5, h, leaves[1], coats[0], rng)
    p.finish(ctx.collection)
    patch = terrain.ground("MeadowSurface", ctx.collection, size=6.0, segments=6, height_at=h, material=None, center=(-20.0, 32.0))
    patch.hide_render = True
    patch.hide_viewport = True
    vegetation.grass_patch("Meadow_0", ctx.collection, patch, density=8.0, seed=1)
    vegetation.tree_stand(ctx.collection, mesh.Batch("Copse_0"), [(-6.0, 40.0), (-3.0, 42.0), (-9.0, 42.0)], h, rng,
                          bark=bark, foliage=leaves[2], height=5.0, kind="oak", max_leaves=600)

    # architecture
    house = mesh.Batch("CheckHouse_0")   # the example already has a House_0: names are global
    architecture.timber_house(house, {"tile": brick, "trim": wood}, -28.0, 33.0, 6.0, 5.0, 2,
                              {"wall": lime, "timber": wood, "door": paint, "window": glass}, rng, 1)
    place.seat(house.finish(ctx.collection)[0], h)
    bridge = mesh.Batch("Bridge_0")
    architecture.stone_bridge(bridge, stone, rock, center=(-16.0, 38.0, h(-16.0, 38.0)), span=12.0, width=3.0, rise=2.0, height_at=h)
    bridge.finish(ctx.collection)
    foot = mesh.Batch("CheckFootbridge_0")
    architecture.arched_footbridge(foot, wood, stone, start=(2.0, 36.0, h(2.0, 36.0)), end=(7.0, 38.0, h(7.0, 38.0)), rise=1.0, height_at=h)
    foot.finish(ctx.collection)
    pav = mesh.Batch("Paving_0")
    architecture.paving(pav, [lime, stone], center=(12.0, 36.0, h(12.0, 36.0)), inner_r=0.6, rings=3, seed=1)
    architecture.paving_grid(pav, [lime, stone], (12.0, 40.0, h(12.0, 40.0)), 3.0, 0.5, 0.03, rng)
    pav.finish(ctx.collection)
    tow = mesh.Batch("Tower_0")
    architecture.tower(tow, stone, (18.0, 36.0), 1.5, h(18.0, 36.0), h(18.0, 36.0) + 6.0, glass, count=6)
    tow.finish(ctx.collection)
    win = mesh.Batch("Window_0")
    architecture.fret_window(win, wood, glass, 24.0, 36.0, h(24.0, 36.0), 1.2, 1.6, -90.0, pattern=1)
    win.finish(ctx.collection)
    arch = mesh.Batch("Arch_0")
    architecture.arched_opening(arch, stone, (28.0, 32.0, h(28.0, 32.0)), 2.0, 3.0)
    roof = arch.transformed(Matrix.Translation((0.0, 0.0, 3.6)))
    architecture.clay_roof(roof, wood, brick, wood, (28.0, 32.0, h(28.0, 32.0)), 3.0, 2.0, 0.8, rng)
    architecture.tiled_roof(roof, brick, wood, (28.0, 36.5, h(28.0, 36.5)), 3.0, 2.0, 0.8, rng)
    arch.finish(ctx.collection)
    lan = mesh.Batch("Lantern_0")
    architecture.lantern(lan, glow, wood, (16.0, 44.0, 2.0))
    hung = lan.finish(ctx.collection)[0]
    hung["placement"] = "free"
    motion.sway(hung, "X", 0.1, 2.0, ctx)
    sign = architecture.text_mesh("Sign_0", ctx.collection, "LIB", (20.0, 42.0, h(20.0, 42.0)), 0.6, paint)
    place.seat(sign, h)
    # a glazed room shell (the env's recipe) stood on the ground, so the gate measures it as ONE row
    cabin = architecture.room_shell(ctx.collection, alloy, center=(0.0, -28.0, h(0.0, -28.0) + 1.6), extents=(4.0, 3.0, 2.8),
                                    openings=(("front", 0.0, 1.2, 0.0, 2.2),), glazed=True)
    assert [o.name for o in cabin[:4]] == ["RoomShell", "RoomShellFloor", "Ceiling", "CeilingGlass"]

    # water
    fall = water.waterfall("Fall_0", ctx.collection, (6.0, 44.0, h(6.0, 44.0)), width=2.0, height=3.0, ctx=ctx)
    assert len(fall) == 5
    water.hanging_fall("Spout_0", ctx.collection, (12.0, 44.0, h(12.0, 44.0) + 2.5), (0.0, -1.0), (1.0, 0.0), width=0.6, height=2.5, ctx=ctx)
    fountain = mesh.Batch("Fountain_0")
    spray = water.fountain_jets(fountain, ctx.collection, clear, center=(-2.0, 44.0, h(-2.0, 44.0)), nozzle_r=0.3, landing_r=1.4,
                                count=10, ctx=ctx, seed=3)
    water.droplets(fountain, foam, (-2.0, 44.0, h(-2.0, 44.0) + 0.3), 1.0, h(-2.0, 44.0), rng, count=60)
    fountain.finish(ctx.collection)
    assert spray is not None and spray["placement"] == "free"
    assert water.pool_height(0.0, 0.0, level=-0.25) == -0.25

    # figures, lights, sky dressing
    folk = mesh.Batch("Visitors_0")
    figure.figure(folk, coats[0], coats[2], (-12.0, 44.0, h(-12.0, 44.0)), heading=place.heading((-12.0, 44.0), (0.0, 0.0)), pose=0.5)
    figure.visitor(folk, coats[1], coats, (-11.0, 45.0, h(-11.0, 45.0)), variant=1)
    figure.crowd(folk, coats, [(-13.0, 43.0, h(-13.0, 43.0))], rng)
    folk.finish(ctx.collection)
    lighting.point_light(ctx.collection, "CheckBulb", (-2.0, 44.0, 2.0), "FFD9A0", 60.0)
    lighting.spot(ctx.collection, "CheckSpot", (3.0, 40.0, 4.0), "FFFFFF", 300.0, target=(2.0, 36.0, 0.0))
    lighting.practical(ctx.collection, "CheckPractical", (16.0, 44.0, 1.7))
    sky.aurora("AuroraSky_0", ctx.collection, (0.0, 80.0, 120.0), ctx=ctx)
    stars = mesh.Batch("Stars_0")
    sky.stars(stars, glow, seed=1, count=200)
    stars.finish(ctx.collection)[0]["placement"] = "free"

    # scatter + place: an instance, a scatter, a stack, the self-checks, a keyframed drift
    crate = ctx.assets["crate"]
    one = scatter.instance(ctx.collection, crate, "LibCrate_0", -14.0, 44.0, height_at=h, rot_z=0.4)
    yaw = place.face_toward(one, (0.0, 0.0))
    place.keep_inside(one, ((-14.0, 44.0, 0.0), (6.0, 6.0, 6.0)))
    # the same size and yaw as the base: a smaller or turned crate's corner columns overhang the
    # open-topped base, which the gate (rightly) reads as floating
    top = scatter.instance(ctx.collection, crate, "StackedCrate_0", -14.0, 44.0, height_at=h, rot_z=yaw, scale=1.0)
    place.rest_on(top, one)
    pts = scatter.poisson(rng, area=(0.0, 40.0, 4.0, 43.0), min_dist=1.4, max_count=2, mask=lambda x, y: 1.0)
    assert len(pts) == 2
    scatter.scatter(ctx.collection, crate, "CheckCrate", pts, height_at=h, rng=rng, scale=(0.8, 1.1))
    assert len(scatter.along_path([(0.0, 0.0), (10.0, 0.0)], 5, jitter=0.2, rng=rng)) == 5
    assert len(scatter.grid((0.0, 0.0), 4.0, 2.0, rng)) == 4
    assert place.clear_of([prims[0], one, hung]) == []
    motion.drift(one, (0.0, 0.5, 0.0), ctx)
    flag = mesh.mesh_object("Flag_0", [(24.0, 40.0, 0.0), (25.0, 40.0, 0.0), (25.0, 40.0, 1.2), (24.0, 40.0, 1.2)], [(0, 1, 2, 3)],
                            coats[1], ctx.collection)
    flag.shape_key_add(name="Basis")
    key = flag.shape_key_add(name="Wave")
    key.data[2].co.y += 0.3
    motion.shape_key_sweep(flag, "Wave", ctx)
    return ctx.collection
'''

#: the catalog's per-call ceilings, on the GLB the gate reads (evaluated triangles per root)
CEILINGS = {
    "Prims_0": 12_000, "Rocks_0": 4_000, "TreeBroadleaf_0": 9_000, "TreeOak_0": 9_000, "TreeCypress_0": 9_000, "TreePine_0": 600,
    "Plants_0": 3_000, "Copse_0": 5_000, "CheckHouse_0": 14_000, "Bridge_0": 1_500, "CheckFootbridge_0": 2_000, "Paving_0": 4_000,
    "Tower_0": 1_500, "Window_0": 400, "Arch_0": 6_000, "Lantern_0": 800, "Sign_0": 2_000, "Fall_0": 2_500, "Spout_0": 600,
    "Fountain_0": 6_000, "Visitors_0": 2_000, "Stars_0": 600, "RoomShell": 2_000,
}
SEATED = {"Prims_0", "Rope_0", "Parented_0", "Rocks_0", "TreeBroadleaf_0", "TreeOak_0", "TreeCypress_0", "TreePine_0", "Plants_0",
          "Copse_0", "CheckHouse_0", "Bridge_0", "CheckFootbridge_0", "Paving_0", "Tower_0", "Window_0", "Arch_0", "Sign_0",
          "Fall_0", "Spout_0", "Fountain_0", "Visitors_0", "LibCrate_0", "StackedCrate_0", "CheckCrate_0", "CheckCrate_1", "Flag_0",
          "RoomShell"}
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


# --------------------------------------------------------------------------- the glazed room shell
#: the real ``room_shell`` twice in one factory scene — glazed and opaque, the same doors — and the
#: share of the floor a high sun cannot reach, walked through the panes (they cast no shadow)
GLAZED_CHECK = r'''
import json
import math
import sys

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from lib.architecture import room_shell
from lib.materials import material

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
iron = material("Iron", "1F2B24", roughness=0.55, metal=0.6)
wall = material("Wall", "B8AE9A", roughness=0.85)
doors = (("front", 2.0, 3.0, 0.0, 3.0), ("left", -1.0, 2.0, 1.0, 2.6))
glazed = room_shell(sc.collection, iron, center=(0.0, 0.0, 5.0), extents=(20.0, 16.0, 10.0), openings=doors, glazed=True)
opaque = room_shell(sc.collection, wall, center=(100.0, 0.0, 5.0), extents=(20.0, 16.0, 10.0), openings=doors)
dg = bpy.context.evaluated_depsgraph_get()
n = math.sqrt(1.0 + 0.25 + 1.96)
sun = (1.0 / n, 0.5 / n, 1.4 / n)   # towards a high sun


def shaded(cx):
    """The share of floor points whose ray to the sun a shadow-casting object stops."""
    blocked = total = 0
    for i in range(-4, 5):
        for j in range(-3, 4):
            origin, total = (cx + i * 1.9, j * 1.9, 0.02), total + 1
            for _ in range(8):   # walk on through a pane: it casts no shadow
                hit, loc, _normal, _index, ob, _matrix = sc.ray_cast(dg, origin, sun)
                if not hit:
                    break
                if ob.visible_shadow:
                    blocked += 1
                    break
                origin = tuple(loc[k] + sun[k] * 0.01 for k in range(3))
    return blocked / total


def row(ob, dedupe=""):
    me = ob.data
    return {"name": ob.name.removesuffix(dedupe), "parent": ob.parent.name if ob.parent else None, "shadow": ob.visible_shadow,
            "material": me.materials[0].name if me and me.materials else None, "verts": len(me.vertices) if me else 0,
            "dims": [max(v.co[k] for v in me.vertices) - min(v.co[k] for v in me.vertices) for k in range(3)] if me else None}


pane = bpy.data.materials["RoomShellGlass"].node_tree.nodes["Principled BSDF"].inputs["Transmission Weight"].default_value
# names are global in Blender: the second shell's came back deduped (RoomShell.001); the first's are the pin
print("C3D " + json.dumps({"glazed": [row(o) for o in glazed], "opaque": [row(o, ".001") for o in opaque], "transmission": pane,
                           "shaded": shaded(0.0), "shaded_opaque": shaded(100.0)}))
'''


@pytest.mark.blender
@needs_blender
def test_a_glazed_room_shell_is_glass_on_an_iron_frame_that_lets_the_sun_in(tmp_path: Path) -> None:
    """Review 2026-10-15 findings 1 / 7: the glass-house env brief called ``room_shell(…, glazed=True)``
    and the lib had no such option (a ``TypeError`` dropped the whole env stage).  Now it has: the
    same panels under the same names (the lid still ``Ceiling`` under ``RoomShell``, for the rig),
    each a frame of ``mat`` bars plus a 2 cm ``materials.glass`` twin (``…Glass``) that casts no
    shadow, the floor an opaque slab of ``mat`` — and between the bars the sun reaches the floor
    that the opaque shell shades completely (the three.js ``roomShell`` test's own measure)."""
    from codeverse3d.languages.blender import blender_env
    from codeverse3d.languages.scene_blender import STARTER_DIR
    from codeverse3d.proc import run_subprocess

    script = tmp_path / "glazed_check.py"
    script.write_text(GLAZED_CHECK)
    proc = run_subprocess([get_settings().resolve_blender(), "-b", "--factory-startup", "--python", str(script), "--", str(STARTER_DIR)],
                          cwd=tmp_path, env=blender_env(), timeout_s=180)
    assert proc.returncode == 0, proc.stderr[-2000:] + proc.stdout[-2000:]
    got = json.loads(next(line[4:] for line in proc.stdout.splitlines() if line.startswith("C3D ")))
    glazed = {r["name"]: r for r in got["glazed"]}
    opaque = {r["name"]: r for r in got["opaque"]}
    panels = set(opaque) - {"RoomShell", "RoomShellFloor"}
    assert {"Ceiling", "RoomShellWallBack", "RoomShellWallFront_0", "RoomShellWallFront_0Lintel", "RoomShellWallLeft_0Sill"} <= panels, sorted(panels)
    assert set(glazed) == set(opaque) | {f"{n}Glass" for n in panels}, sorted(set(glazed) ^ set(opaque))
    assert [r["name"] for r in got["glazed"][:4]] == ["RoomShell", "RoomShellFloor", "Ceiling", "CeilingGlass"]
    assert all(r["parent"] == "RoomShell" for n, r in glazed.items() if n != "RoomShell")
    floor = glazed["RoomShellFloor"]
    assert (floor["material"], floor["shadow"], floor["verts"]) == ("Iron", True, 8) and floor["dims"] == pytest.approx(opaque["RoomShellFloor"]["dims"], abs=1e-3)
    for n in panels:
        frame, pane, slab = glazed[n], glazed[f"{n}Glass"], opaque[n]
        thin = 2 if n == "Ceiling" else 1 if "Front" in n or "Back" in n else 0
        assert frame["material"] == "Iron" and frame["shadow"] and frame["verts"] >= 32 and frame["verts"] % 8 == 0, (n, frame)   # ≥ 4 edge bars
        assert pane["material"] == "RoomShellGlass" and not pane["shadow"] and pane["verts"] == 8, (n, pane)
        assert pane["dims"][thin] == pytest.approx(0.02, abs=1e-3) and slab["dims"][thin] == pytest.approx(0.2, abs=1e-3), (n, pane["dims"], slab["dims"])
        assert [pane["dims"][k] for k in range(3) if k != thin] == pytest.approx([slab["dims"][k] for k in range(3) if k != thin], abs=1e-3), n
    assert got["transmission"] == 1.0
    assert got["shaded_opaque"] == 1.0 and got["shaded"] < 0.3, (got["shaded"], got["shaded_opaque"])
