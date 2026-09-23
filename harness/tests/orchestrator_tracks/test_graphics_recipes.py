"""Harness-owned GLSL recipe extraction, seeding, composition, and linting."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.common import HARNESS_OWNED_SRC, Language, Track
from codeverse3d.contracts.plan import GraphicsPlan
from codeverse3d.languages import get_runtime
from codeverse3d.languages.glsl_shader import (
    COMMON_GLSL,
    GlslShaderRuntime,
    _check_file,
    compose,
    defined_functions,
    first_error,
    lint_workspace,
    parse_glsl_log,
    write_skeleton,
)
from codeverse3d.languages.glsl_shader import HEADER as WRAP_HEADER
from codeverse3d.proc import EventLog
from codeverse3d.prompts import load_text, render
from codeverse3d.tracks.graphics import (
    HEADER,
    NOT_SEEDED,
    RECIPES_REL,
    RESUME_HEADER,
    cookbook_functions,
    defined_names,
    graphics_prompt_context,
    is_skeleton_common,
    parse_functions,
    recipe_chapters,
    seed_recipes,
    seeded_on_disk,
)
from codeverse3d.tracks.planner import plan_example
from codeverse3d.workspace import Workspace

AURORA = "Aurora borealis over a mountain ridge with a frozen lake, dense stars, green and violet curtains"
RAIN = "neon rain on glass"
NOTHING = "a rotating rainbow gear wheel"       # matches the Instancing chapter, which defines no function
UNMATCHED = "xyzzy plugh"
AURORA_NAMES = ("curtain", "auroraCol", "aurora", "stars")
HELPERS = ("hash12", "hash22", "noise", "fbm")
_DEF = re.compile(r"^[ \t]*(?:float|vec[234]|mat[234]|int|bool|void)\s+(\w+)\s*\(", re.M)
SHADER = ("void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n"
          "    vec2 p = (fragCoord - 0.5 * u_resolution) / u_resolution.y; float t = u_time;\n"
          "    vec3 col = vec3(0.0, 0.0, 0.012) + vec3(0.9) * stars(p, 160.0, 0.22);\n"
          "    vec3 au = aurora(p, t); col += au * 1.7 + au * au * 0.5;\n"
          "    fragColor = vec4(pow(col / (1.0 + 0.6 * col), vec3(0.4545)), 1.0);\n}\n")


def _ws(tmp_path: Path, *, skeleton: bool = True) -> Workspace:
    ws = Workspace(tmp_path / "ws").create()
    if skeleton:
        write_skeleton(ws, None)
    return ws


def _ctx(ws: Workspace, brief: str = AURORA, *, plan: GraphicsPlan | None = None, language: Language = Language.GLSL_SHADER):
    return SimpleNamespace(cookbook_text=load_text("glsl_shader/cookbook.md"), cookbook_rel="glsl_shader/cookbook.md", plan=plan,
                           spec=SimpleNamespace(prompt=brief, constraints=SimpleNamespace(dimensions_m={}, max_triangles=None, style="",
                                                                                          must_have=[], must_not=[]), references=[]),
                           language=language, track=Track.GRAPHICS, ws=ws, extra={}, events=EventLog(ws.events_path),
                           contract_text="", tool_cards="", single_shot=False, runtime=get_runtime(language))


def _defs(text: str) -> list[str]:
    return _DEF.findall(text)


def _recipes(ws: Workspace) -> Path:
    return ws.root / RECIPES_REL


def _seeded_events(ws: Workspace) -> list[dict]:
    return [e for e in EventLog(ws.events_path).read() if e["event"] == "recipes.seeded"]


@pytest.fixture(autouse=True)
def _default_on(monkeypatch) -> None:
    monkeypatch.delenv("C3D_SEED_RECIPES", raising=False)


# ----------------------------------------------------------------------------- extraction
def test_cookbook_functions_are_real_definitions_without_usage_lines() -> None:
    known = cookbook_functions(load_text("glsl_shader/cookbook.md"))
    for name in AURORA_NAMES + HELPERS + ("bokehSoft", "dropsLayer", "warped"):
        assert name in known, name
    assert "mainImage" not in known and "main" not in known
    assert known["curtain"].signature == "float curtain(vec2 p, float t, float seed, out float k)"
    assert {"fbm", "noise"} <= known["curtain"].calls and {"hash12", "hash22"} <= known["stars"].calls
    assert "// ORGANIC CURTAIN" in known["curtain"].text and "Never a comb" in known["curtain"].text
    for r in known.values():
        assert "// usage:" not in r.text and "// e.g." not in r.text and r.text.count("{") == r.text.count("}"), r.name
    # the stars doc comment (prose with a ';' inside) is kept, the usage line under it is not
    assert "DENSE STARS" in known["stars"].text and "col += vec3(0.9, 0.95, 1.0)" not in known["stars"].text
    assert "// sunset:" not in known["tonemapACES"].text and "// water colour" not in "".join(r.text for r in known.values())


def test_parse_functions_handles_one_liners_and_nested_braces() -> None:
    code = "// doc\nfloat a(float x) { return x; }\n// usage: a(1.0);\nvec2 b(vec2 p) {\n  for (int i = 0; i < 2; i++) { p = p * 2.0; }\n  return p; // }\n}\nvoid mainImage(out vec4 f, in vec2 c) { f = vec4(a(1.0)); }\n"
    got = parse_functions(code, "ch")
    assert [r.name for r in got] == ["a", "b"] and got[0].text == "// doc\nfloat a(float x) { return x; }"
    assert got[1].signature == "vec2 b(vec2 p)" and got[1].text.endswith("\n}") and got[1].calls == frozenset()
    assert got[1].purpose == 'from the cookbook chapter "ch"'
    assert defined_names(COMMON_GLSL) >= {"hash11", "hash12", "hash22", "noise", "fbm", "rot2", "palette", "tonemap", "PI", "TAU"}


def test_chapter_selection_excludes_always_on_and_templates(tmp_path) -> None:
    ws = _ws(tmp_path)
    titles = [s.title for s in recipe_chapters(_ctx(ws, AURORA))]
    assert any(t.startswith("Light phenomena") for t in titles) and any(t.startswith("Gradient sky") for t in titles)
    assert not any("Hash" in t or "Palettes" in t or "PITFALLS" in t for t in titles)
    assert recipe_chapters(_ctx(ws, "a raymarched temple corridor with fog")) == [] and NOT_SEEDED == ("Raymarching",)
    assert recipe_chapters(_ctx(ws, UNMATCHED)) == []

    # a chapter arrives whole or not at all — the budget never cuts one in half
    from codeverse3d.prompts.sections import split_sections
    from codeverse3d.tracks.prompting import select_cookbook_chapters

    ctx = _ctx(ws, AURORA)
    whole = {s.title: s.body.rstrip() for s in split_sections(ctx.cookbook_text)}
    for s in select_cookbook_chapters(ctx, AURORA, budget=6000):
        assert s.body.rstrip() == whole[s.title], s.title
    assert [s.title[:10] for s in recipe_chapters(_ctx(ws, NOTHING))] == ["Instancing"]


# ----------------------------------------------------------------------------- seeding
def test_the_file_is_the_harness_owned_recipes_glsl() -> None:
    assert RECIPES_REL == "src/recipes.glsl" and HARNESS_OWNED_SRC[Language.GLSL_SHADER] == (RECIPES_REL,)
    assert HEADER.startswith("// harness-owned:") and "READ-ONLY" in HEADER and "above src/common.glsl" in HEADER


def test_aurora_brief_seeds_recipes_and_helpers_exactly_once(tmp_path) -> None:
    ws = _ws(tmp_path, skeleton=False)
    ctx = _ctx(ws)
    names = seed_recipes(ctx)
    for n in AURORA_NAMES + HELPERS:
        assert n in names, (n, names)
    assert "hash11" not in names and "hash33" not in names               # only the helpers the recipes call
    assert names.index("fbm") < names.index("curtain") and names.index("noise") < names.index("fbm")
    assert names.index("hash12") < names.index("stars") and names.index("curtain") < names.index("aurora")
    text = _recipes(ws).read_text()
    assert text.startswith(HEADER)
    defs = _defs(text)
    assert len(defs) == len(set(defs)) and set(defs) == set(names)
    assert "mainImage" not in text and "// usage:" not in text
    for n in names:
        assert re.search(rf"^//   \S.*\b{n}\(.*\) — .+$", text, re.M), n   # one index line per recipe
    assert "never a comb of bars" in text
    assert not (ws.src / "common.glsl").exists(), "common.glsl is the agent's; the seed never creates it"
    # the run record carries it
    ev = _seeded_events(ws)
    assert len(ev) == 1 and ev[0]["names"] == names and ev[0]["present"] == names
    assert ev[0]["file"] == RECIPES_REL and ev[0]["trimmed"] == []
    assert any(t.startswith("Light phenomena") for t in ev[0]["chapters"])
    assert [e["name"] for e in ctx.extra["seeded_recipes"]] == names
    cur = next(e for e in ctx.extra["seeded_recipes"] if e["name"] == "curtain")
    assert cur["signature"].startswith("float curtain(") and "aurora" in cur["purpose"]


def test_recipes_are_self_contained_and_the_skeleton_common_is_trimmed(tmp_path) -> None:
    ws = _ws(tmp_path)
    assert is_skeleton_common((ws.src / "common.glsl").read_text())
    names = seed_recipes(_ctx(ws))
    assert set(names) >= set(AURORA_NAMES) | set(HELPERS)
    recipes = _recipes(ws).read_text()
    common = (ws.src / "common.glsl").read_text()
    assert not set(_defs(common)) & set(_defs(recipes)), "no name is defined twice across the two files"
    assert set(_defs(common)) == {"hash11", "rot2", "palette", "tonemap"} and "#define PI" in common and "#define TAU" in common
    assert common.splitlines()[0] == COMMON_GLSL.splitlines()[0] and "provided by src/recipes.glsl" in common.splitlines()[1]
    assert "\n\n\n" not in common and is_skeleton_common(common)
    assert _seeded_events(ws)[0]["trimmed"] == ["hash12", "hash22", "noise", "fbm"]
    # the harness's own skeleton shader still builds on top: lint clean, one definition per name
    rep = lint_workspace(ws)
    assert rep.passed and not any(f.data["kind"] in ("redefines_recipe", "stray_file") for f in rep.findings), [f.message for f in rep.findings]
    image, _ = GlslShaderRuntime().compose_sources(ws)
    defs = _defs(image.source)
    assert len(defs) == len(set(defs)), sorted(d for d in defs if defs.count(d) > 1)
    # seeding again changes nothing: no new names, no second trim
    assert seed_recipes(_ctx(ws)) == [] and (ws.src / "common.glsl").read_text() == common and _recipes(ws).read_text() == recipes
    assert [r.name for r in seeded_on_disk(recipes, cookbook_functions(load_text("glsl_shader/cookbook.md")))] == names


def test_an_agent_written_common_is_never_touched(tmp_path) -> None:
    ws = _ws(tmp_path)
    mine = "// my helpers\nfloat hash12(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }\nfloat mine(float x) { return x * 2.0; }\n"
    (ws.src / "common.glsl").write_text(mine)
    assert not is_skeleton_common(mine)
    names = seed_recipes(_ctx(ws))
    assert "hash12" in names and (ws.src / "common.glsl").read_text() == mine
    assert _seeded_events(ws)[0]["trimmed"] == []
    rep = lint_workspace(ws)
    dup = [f for f in rep.findings if f.data["kind"] == "redefines_recipe"]
    assert not rep.passed and [(f.data["file"], f.data["line"]) for f in dup] == [("src/common.glsl", 2)]
    assert "`hash12` is already provided by src/recipes.glsl — call it instead of redefining it" in dup[0].message


def test_brief_and_plan_visuals_select_recipes(tmp_path) -> None:
    ws = _ws(tmp_path)
    names = seed_recipes(_ctx(ws, RAIN))
    assert "dropsLayer" in names and "bokehSoft" in names and "curtain" not in names and "stars" not in names
    text = _recipes(ws).read_text()
    assert "vec2 dropsLayer(vec2 uv, float t, float scale)" in text and "vec3 bokehSoft(vec2 p, float t)" in text

    ws = _ws(tmp_path / "plan")
    plan = GraphicsPlan.model_validate({**plan_example(Track.GRAPHICS), "key_visuals": ["an aurora curtain over the ridge"]})
    names = seed_recipes(_ctx(ws, "a landscape", plan=plan))
    assert "curtain" in names and "bokehSoft" not in names
    # the seed follows the prompt's selection exactly, budget included: for "a city at night" the Light +
    # Gradient-sky chapters fill the 9 k budget and Bokeh does not fit, so bokehSoft is NOT seeded either
    ws2 = _ws(tmp_path / "city")
    names2 = seed_recipes(_ctx(ws2, "a city at night", plan=plan))
    assert "curtain" in names2 and "bokehSoft" not in names2


def test_nothing_matches_nothing_seeded_no_file_no_block(tmp_path) -> None:
    ws = _ws(tmp_path)
    ctx = _ctx(ws, NOTHING)
    assert seed_recipes(ctx) == []
    assert not _recipes(ws).exists() and (ws.src / "common.glsl").read_text() == COMMON_GLSL
    assert ctx.extra["seeded_recipes"] == []
    d = graphics_prompt_context(ctx, skeleton_files={}, previous_error="")
    assert d["seeded_recipes"] == []
    gen = render("tracks/generate_graphics.j2", **d)
    assert "Verified helpers in the harness-owned" not in gen and "recipes.glsl" not in gen
    ws2 = _ws(tmp_path / "b", skeleton=False)
    assert seed_recipes(_ctx(ws2, NOTHING)) == [] and not _recipes(ws2).exists() and not (ws2.src / "common.glsl").exists()
    assert seed_recipes(_ctx(ws2, UNMATCHED)) == [] and not _recipes(ws2).exists()


def test_resume_appends_only_new_names(tmp_path) -> None:
    ws = _ws(tmp_path)
    first = seed_recipes(_ctx(ws))
    recipes = _recipes(ws)
    seeded = recipes.read_text()
    # the agent did what it does: rewrote common.glsl AND shader.frag wholesale, its own helpers, its own stars()
    agent_common = "float mine(float x) { return x * 2.0; }\nfloat starsMine(vec2 p) { return 0.0; }\n"
    (ws.src / "common.glsl").write_text(agent_common)
    (ws.src / "shader.frag").write_text(SHADER)
    ctx = _ctx(ws)
    assert seed_recipes(ctx) == []                                       # every name is already in recipes.glsl
    assert recipes.read_text() == seeded and (ws.src / "common.glsl").read_text() == agent_common
    # a recipes.glsl missing stars() and aurora() (an older seed): a resume appends exactly those two, at the end
    trimmed = re.sub(r"(?:^//[^\n]*\n)*float stars\(vec2 p, float density, float keep\) \{.*?\n\}\n", "", seeded, count=1, flags=re.S | re.M)
    trimmed = re.sub(r"(?:^//[^\n]*\n)*vec3 aurora\(vec2 p, float t\) \{.*?\n\}\n", "", trimmed, count=1, flags=re.S | re.M)
    assert "stars" not in defined_names(trimmed) and "aurora" not in defined_names(trimmed)
    recipes.write_text(trimmed)
    third = seed_recipes(ctx)
    assert third == ["stars", "aurora"]
    text = recipes.read_text()
    assert text.startswith(trimmed) and text.count(HEADER) == 1 and text.count(RESUME_HEADER) == 1
    assert text.index(RESUME_HEADER) < text.index("\nfloat stars(") < text.index("\nvec3 aurora(")   # definitions, not index lines
    defs = _defs(text)
    assert len(defs) == len(set(defs)) and set(first) == set(defs)
    assert (ws.src / "common.glsl").read_text() == agent_common        # still never touched
    assert [e["name"] for e in ctx.extra["seeded_recipes"]] == [n for n in first if n not in ("stars", "aurora")] + ["stars", "aurora"]
    assert _seeded_events(ws)[-1]["names"] == third and _seeded_events(ws)[-1]["trimmed"] == []


def test_gate_and_language(tmp_path, monkeypatch) -> None:
    def seeding(raw: str | None) -> bool:
        if raw is None:
            monkeypatch.delenv("C3D_SEED_RECIPES", raising=False)
        else:
            monkeypatch.setenv("C3D_SEED_RECIPES", raw)
        get_settings.cache_clear()
        return get_settings().limits.seed_recipes

    ws = _ws(tmp_path)
    assert seeding(None) is True
    assert seeding("0") is False
    ctx = _ctx(ws)
    assert seed_recipes(ctx) == [] and not _recipes(ws).exists() and "seeded_recipes" not in ctx.extra
    assert (ws.src / "common.glsl").read_text() == COMMON_GLSL and not _seeded_events(ws)
    assert seeding("garbage") is True                  # a typo keeps the default: never a crash, never the arm
    assert seeding("on") is True and seeding("off") is False
    assert seeding(None) is True
    assert seed_recipes(_ctx(ws, language=Language.OPENGL_PYTHON)) == []  # opengl_python: nothing seeded
    assert not _recipes(ws).exists() and (ws.src / "common.glsl").read_text() == COMMON_GLSL
    monkeypatch.setenv("C3D_LIMITS__SEED_RECIPES", "off")
    assert Settings().limits.seed_recipes is False     # the nested spelling reads the same field


# ----------------------------------------------------------------------------- compose / lint / prompt
def _gl_or_skip(tmp_path: Path, src: str, **kw):
    pytest.importorskip("moderngl")
    from codeverse3d.spatial.gl_render import GlHost, GlHostError

    try:
        res = GlHost(timeout_s=120).render_fragment_shader(src, tmp_path / "out", **kw)
    except GlHostError as e:  # pragma: no cover - machine without GL
        pytest.skip(f"no OpenGL context: {e}")
    if not res.ok and res.stage == "context":  # pragma: no cover
        pytest.skip(f"no OpenGL context: {res.error_message}")
    return res


def test_recipes_compose_above_common_and_the_seeded_aurora_renders(tmp_path) -> None:
    ws = _ws(tmp_path)
    seed_recipes(_ctx(ws))
    (ws.src / "shader.frag").write_text(SHADER)
    common = (ws.src / "common.glsl").read_text()
    recipes = _recipes(ws).read_text()
    assert lint_workspace(ws).findings == []
    comp = compose(SHADER, common, recipes_src=recipes)
    assert comp.convention == "mainImage" and not comp.uses_feedback
    src = comp.source
    assert (src.index("#version 330 core") < src.index(HEADER) < src.index("float curtain(") < src.index("vec3 aurora(")
            < src.index(common.splitlines()[0]) < src.index("void mainImage("))
    assert [s.file for s in comp.line_map.segments] == ["harness", "src/recipes.glsl", "src/common.glsl", "src/shader.frag", "harness"]
    header_lines = WRAP_HEADER.count("\n")
    assert comp.line_map.locate(header_lines + 1) == ("src/recipes.glsl", 1)
    assert comp.line_map.locate(header_lines + recipes.count("\n") + 1) == ("src/common.glsl", 1)
    # the runtime composes the same way (image and buffer_a alike)
    (ws.src / "buffer_a.frag").write_text(SHADER)
    image, buffer_a = GlslShaderRuntime().compose_sources(ws)
    assert image.source == src and buffer_a is not None
    assert [s.file for s in buffer_a.line_map.segments][:3] == ["harness", "src/recipes.glsl", "src/common.glsl"]
    # a real compile when a GL context exists: the seeded aurora lights the frame (skipped otherwise)
    res = _gl_or_skip(tmp_path, src, width=96, height=54, times=(0.0, 2.5))
    assert res.ok, res.error_message
    assert len(res.frames) == 2 and all(Path(f.path).is_file() for f in res.frames)
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    lum = [float(np.asarray(Image.open(f.path).convert("L"), dtype=float).mean() / 255.0) for f in res.frames]
    assert all(v > 0.0 for v in lum), lum


def test_a_compile_error_inside_recipes_is_reported_at_its_recipes_line(tmp_path) -> None:
    recipes = "// harness-owned: verified cookbook recipes ...\nfloat ok(float x) { return x; }\n\nfloat bad(float x) { return x + ; }\n"
    common = "float mine(float x) { return x; }\n"
    comp = compose(SHADER, common, recipes_src=recipes)
    header_lines = WRAP_HEADER.count("\n")
    composed_line = header_lines + 4                                     # `bad` is recipes.glsl line 4
    assert comp.source.splitlines()[composed_line - 1].startswith("float bad(")
    for log in (f"0:{composed_line}(32): error: syntax error, unexpected ';'",           # Mesa
                f"0({composed_line}) : error C0000: syntax error, unexpected ';'",       # NVIDIA
                f"ERROR: 0:{composed_line}: ';' : syntax error"):                        # ANGLE / ES
        first = first_error(parse_glsl_log(log, comp.line_map))
        assert first is not None and (first.file, first.file_line) == ("src/recipes.glsl", 4), log
        assert first.text().startswith("src/recipes.glsl:4: error:")
    # and common.glsl / shader.frag lines still map to themselves, shifted by the recipes block
    assert comp.line_map.locate(header_lines + 5) == ("src/common.glsl", 1)
    assert comp.line_map.locate(header_lines + 6) == ("src/shader.frag", 1)
    # the real compiler, when there is one: the build names src/recipes.glsl:4
    res = _gl_or_skip(tmp_path, comp.source, width=32, height=18, times=(0.0,))
    assert not res.ok and res.stage == "compile"
    first = first_error(parse_glsl_log(res.error_message, comp.line_map))
    assert first is not None and (first.file, first.file_line) == ("src/recipes.glsl", 4), res.error_message


def test_lint_flags_a_redefinition_of_a_seeded_recipe(tmp_path) -> None:
    recipes = "float curtain(vec2 p, float t, float seed, out float k) { k = 0.0; return 0.0; }\nvec3 aurora(vec2 p, float t) { return vec3(0.0); }\n"
    shader = "// mine\nfloat curtain(vec2 p, float t, float seed, out float k) { k = 1.0; return 1.0; }\n" + SHADER
    common = "float hash12(vec2 p) { return 0.0; }\n\nvec3 aurora(vec2 p, float t) { return vec3(1.0); }\n"
    names = frozenset(defined_functions(recipes))
    found = [f for rel, text, role in (("src/shader.frag", shader, "shader"), ("src/common.glsl", common, "common"))
             for f in _check_file(rel, text, role=role, recipe_names=names) if f.data["kind"] == "redefines_recipe"]
    assert [(f.data["file"], f.data["line"]) for f in found] == [("src/shader.frag", 2), ("src/common.glsl", 3)]
    assert found[0].message == "src/shader.frag:2: `curtain` is already provided by src/recipes.glsl — call it instead of redefining it"
    assert found[1].message.endswith("`aurora` is already provided by src/recipes.glsl — call it instead of redefining it")
    assert all(f.severity.value == "error" for f in found) and "harness-owned" in found[0].fix_hint
    assert not [f for f in _check_file("src/shader.frag", shader, role="shader") if f.data["kind"] == "redefines_recipe"]  # no recipes: no rule
    # the workspace lint reads recipes.glsl itself (and does not call it a stray file); buffer_a is checked too
    ws = _ws(tmp_path, skeleton=False)
    (ws.src / "shader.frag").write_text(SHADER)
    (ws.src / "buffer_a.frag").write_text("vec3 aurora(vec2 p, float t) { return vec3(0.0); }\n" + SHADER)
    _recipes(ws).write_text(recipes)
    rep = lint_workspace(ws)
    assert not rep.passed and [f.data["file"] for f in rep.findings if f.data["kind"] == "redefines_recipe"] == ["src/buffer_a.frag"]
    assert not any(f.data["kind"] == "stray_file" for f in rep.findings)


def test_prompt_block_lists_the_seeded_names(tmp_path) -> None:
    ws = _ws(tmp_path)
    ctx = _ctx(ws, plan=GraphicsPlan.model_validate({**plan_example(Track.GRAPHICS), "key_visuals": ["green aurora curtains", "dense stars"]}))
    names = seed_recipes(ctx)
    d = graphics_prompt_context(ctx, skeleton_files={}, previous_error="")
    assert [r["name"] for r in d["seeded_recipes"]] == names
    gen = render("tracks/generate_graphics.j2", **d)
    assert "Verified helpers in the harness-owned, read-only `src/recipes.glsl`" in gen and "do not redefine them" in gen
    assert "do not copy them into `src/common.glsl`" in gen and "pastes it above" in gen and "ALREADY in `src/common.glsl`" not in gen
    for n in names:
        assert re.search(rf"^- `[^`]*\b{n}\([^`]*\)` — .+$", gen, re.M), n
    assert "float curtain(vec2 p, float t, float seed, out float k)" in gen and "never a comb of bars" in gen
    assert gen.index("Cookbook excerpt:") < gen.index("Verified helpers in the harness-owned") < gen.index("## Plan")
    ref = render("tracks/refine_graphics.j2", **graphics_prompt_context(
        ctx, round_index=1, tasks=["[judge/likeness] overall: no curtain"], targets=["overall"], files=["src/shader.frag"],
        judge_summary="Previous score 0.3", frame_notes="(no frame metrics)", current_files={}))
    assert "harness-owned, read-only `src/recipes.glsl`" in ref and "do not copy them into common.glsl" in ref
    assert all(f"`{n}`" in ref for n in names)


def test_a_single_shot_repair_never_inlines_the_harness_owned_recipes(tmp_path) -> None:
    """``repair.files_for_repair`` re-implemented ``prompting.current_files`` without its
    harness-owned rule, so a build error naming src/recipes.glsl pasted the read-only
    file into the single-shot repair prompt as a failing file to rewrite."""
    from codeverse3d.contracts.artifacts import BuildResult, GateReport
    from codeverse3d.tracks.repair import files_for_repair

    ws = _ws(tmp_path, skeleton=False)
    _recipes(ws).write_text("float hash12(vec2 p) { return 0.0; }\n")
    (ws.src / "shader.frag").write_text(SHADER)
    ctx = SimpleNamespace(ws=ws, language=Language.GLSL_SHADER, runtime=SimpleNamespace())
    build = BuildResult(ok=False, language="glsl_shader", error_file=str(_recipes(ws)))
    assert list(files_for_repair(ctx, build, GateReport(gate="lint", passed=True), ["src/shader.frag"])) == ["src/shader.frag"]
