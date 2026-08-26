"""``tracks/graphics_recipes.py``: the brief's verified cookbook recipes land in ``src/common.glsl`` BEFORE the session.

Measured 2026-08-26 (refs_v2_graphics, aurora brief, gemini-3.7-flash, shader_v2): the baseline prompt
carried ``curtain()`` five times and ``src/shader.frag`` called it zero times in both finished runs —
round 0 a comb of bars (0.33), a later round a wash (0.12).  Showing flash a recipe is not flash using
it; these tests pin the on-disk seed, its helpers, the resume behaviour, the compose/lint path and the
prompt block that names what was seeded.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeverse.config import Settings, seed_recipes_enabled
from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import GraphicsPlan
from codeverse.events import EventLog
from codeverse.languages.glsl_shader.lint import lint_text
from codeverse.languages.glsl_shader.skeleton import COMMON_GLSL, write_skeleton
from codeverse.languages.glsl_shader.wrap import compose
from codeverse.prompts import load_text, render
from codeverse.tracks import graphics_recipes as gr
from codeverse.tracks.graphics_recipes import (
    HEADER,
    NOT_SEEDED,
    cookbook_functions,
    defined_names,
    parse_functions,
    recipe_chapters,
    seed_recipes,
    seeded_on_disk,
)
from codeverse.tracks.graphics_steps import graphics_prompt_context, plan_example
from codeverse.workspace import Workspace

AURORA = "Aurora borealis over a mountain ridge with a frozen lake, dense stars, green and violet curtains"
RAIN = "neon rain on glass"
NOTHING = "a rotating rainbow gear wheel"       # matches the Instancing chapter, which defines no function
UNMATCHED = "xyzzy plugh"
AURORA_NAMES = ("curtain", "auroraCol", "aurora", "stars")
HELPERS = ("hash12", "hash22", "noise", "fbm")
_DEF = re.compile(r"^[ \t]*(?:float|vec[234]|mat[234]|int|bool|void)\s+(\w+)\s*\(", re.M)


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
                           contract_text="", tool_cards="", single_shot=False, runtime=SimpleNamespace(entry_globs=()))


def _defs(text: str) -> list[str]:
    return _DEF.findall(text)


@pytest.fixture(autouse=True)
def _default_on(monkeypatch) -> None:
    monkeypatch.delenv("CV3D_SEED_RECIPES", raising=False)


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
    assert [s.title[:10] for s in recipe_chapters(_ctx(ws, NOTHING))] == ["Instancing"]


# ----------------------------------------------------------------------------- seeding
def test_aurora_brief_seeds_recipes_and_helpers_exactly_once(tmp_path) -> None:
    ws = _ws(tmp_path, skeleton=False)                                   # no skeleton: helpers must come along
    ctx = _ctx(ws)
    names = seed_recipes(ctx)
    for n in AURORA_NAMES + HELPERS:
        assert n in names, (n, names)
    assert "hash11" not in names and "hash33" not in names               # only the helpers the recipes call
    assert names.index("fbm") < names.index("curtain") and names.index("noise") < names.index("fbm")
    assert names.index("hash12") < names.index("stars") and names.index("curtain") < names.index("aurora")
    text = (ws.src / "common.glsl").read_text()
    assert text.startswith(HEADER)
    defs = _defs(text)
    assert len(defs) == len(set(defs)) and set(defs) == set(names)
    assert "mainImage" not in text and "// usage:" not in text
    for n in names:
        assert re.search(rf"^//   \S.*\b{n}\(.*\) — .+$", text, re.M), n   # one index line per recipe
    assert "never a comb of bars" in text
    # the run record carries it
    ev = [e for e in EventLog(ws.events_path).read() if e["event"] == "recipes.seeded"]
    assert len(ev) == 1 and ev[0]["names"] == names and ev[0]["present"] == names
    assert any(t.startswith("Light phenomena") for t in ev[0]["chapters"])
    assert [e["name"] for e in ctx.extra["seeded_recipes"]] == names
    cur = next(e for e in ctx.extra["seeded_recipes"] if e["name"] == "curtain")
    assert cur["signature"].startswith("float curtain(") and "aurora" in cur["purpose"]


def test_skeleton_helpers_are_not_duplicated(tmp_path) -> None:
    ws = _ws(tmp_path)                                                   # the skeleton already defines hash/noise/fbm
    before = (ws.src / "common.glsl").read_text()
    names = seed_recipes(_ctx(ws))
    assert set(names) >= set(AURORA_NAMES) and not set(names) & set(HELPERS)
    text = (ws.src / "common.glsl").read_text()
    assert text.startswith(before) and HEADER in text
    defs = _defs(text)
    assert len(defs) == len(set(defs)), sorted(d for d in defs if defs.count(d) > 1)
    assert [r.name for r in seeded_on_disk(text, cookbook_functions(load_text("glsl_shader/cookbook.md")))] == names


def test_rain_brief_seeds_drops_and_bokeh(tmp_path) -> None:
    ws = _ws(tmp_path)
    names = seed_recipes(_ctx(ws, RAIN))
    assert "dropsLayer" in names and "bokehSoft" in names and "curtain" not in names and "stars" not in names
    text = (ws.src / "common.glsl").read_text()
    assert "vec2 dropsLayer(vec2 uv, float t, float scale)" in text and "vec3 bokehSoft(vec2 p, float t)" in text


def test_plan_key_visuals_join_the_brief(tmp_path) -> None:
    ws = _ws(tmp_path)
    plan = GraphicsPlan.model_validate({**plan_example(), "key_visuals": ["an aurora curtain over the ridge"]})
    names = seed_recipes(_ctx(ws, "a landscape", plan=plan))
    assert "curtain" in names and "bokehSoft" not in names
    # the seed follows the prompt's selection exactly, budget included: for "a city at night" the Light +
    # Gradient-sky chapters fill the 9 k budget and Bokeh does not fit, so bokehSoft is NOT seeded either
    ws2 = _ws(tmp_path / "b")
    names2 = seed_recipes(_ctx(ws2, "a city at night", plan=plan))
    assert "curtain" in names2 and "bokehSoft" not in names2


def test_nothing_matches_nothing_seeded_no_block(tmp_path) -> None:
    ws = _ws(tmp_path)
    before = (ws.src / "common.glsl").read_text()
    ctx = _ctx(ws, NOTHING)
    assert seed_recipes(ctx) == []
    assert (ws.src / "common.glsl").read_text() == before and HEADER not in before
    assert ctx.extra["seeded_recipes"] == []
    d = graphics_prompt_context(ctx, skeleton_files={}, previous_error="")
    assert d["seeded_recipes"] == []
    gen = render("tracks/generate_graphics.j2", **d)
    assert "Verified helpers ALREADY" not in gen and "harness-seeded" not in gen
    # a brief that matches nothing and NO common.glsl at all: no file is created
    ws2 = _ws(tmp_path / "b", skeleton=False)
    assert seed_recipes(_ctx(ws2, NOTHING)) == [] and not (ws2.src / "common.glsl").exists()
    assert seed_recipes(_ctx(ws2, UNMATCHED)) == [] and not (ws2.src / "common.glsl").exists()


def test_resume_appends_only_new_names(tmp_path) -> None:
    ws = _ws(tmp_path)
    first = seed_recipes(_ctx(ws))
    # the agent rewrote common.glsl: kept the seeded block, added its own helper, defined its own stars()
    p = ws.src / "common.glsl"
    agent = p.read_text().replace("float stars(vec2 p, float density, float keep)", "float starsOld(vec2 p, float density, float keep)")
    agent += "\nfloat mine(float x) { return x * 2.0; }\nfloat stars(vec2 p, float density, float keep) { return 0.0; }\n"
    p.write_text(agent)
    ctx = _ctx(ws)
    again = seed_recipes(ctx)
    assert again == [], again                                            # every name is defined (stars by the agent)
    assert p.read_text() == agent
    # drop the agent's stars() and the seeded aurora(): a resume re-seeds exactly those two, after the agent's code
    trimmed = agent.replace("float stars(vec2 p, float density, float keep) { return 0.0; }\n", "")
    trimmed = re.sub(r"vec3 aurora\(vec2 p, float t\) \{.*?\n\}\n", "", trimmed, flags=re.S)
    p.write_text(trimmed)
    third = seed_recipes(ctx)
    assert third == ["stars", "aurora"]
    text = p.read_text()
    assert text.startswith(trimmed) and text.count(HEADER) == 2 and "float mine(float x)" in text
    defs = _defs(text)
    assert len(defs) == len(set(defs)) and set(first) - {"stars", "aurora"} <= set(defs)
    # the prompt block lists the cookbook recipes on disk, not the agent's own helpers under the header
    assert [e["name"] for e in ctx.extra["seeded_recipes"]] == [n for n in first if n not in ("stars", "aurora")] + ["stars", "aurora"]


def test_gate_and_language(tmp_path, monkeypatch) -> None:
    ws = _ws(tmp_path)
    before = (ws.src / "common.glsl").read_text()
    assert Settings().limits.seed_recipes is True and seed_recipes_enabled() is True
    monkeypatch.setenv("CV3D_SEED_RECIPES", "0")
    assert seed_recipes_enabled() is False
    ctx = _ctx(ws)
    assert seed_recipes(ctx) == [] and (ws.src / "common.glsl").read_text() == before and "seeded_recipes" not in ctx.extra
    assert not [e for e in EventLog(ws.events_path).read() if e["event"] == "recipes.seeded"]
    monkeypatch.setenv("CV3D_SEED_RECIPES", "garbage")
    assert seed_recipes_enabled() is False                               # a typo is a control run, not a crash
    monkeypatch.setenv("CV3D_SEED_RECIPES", "on")
    assert seed_recipes_enabled() is True
    monkeypatch.delenv("CV3D_SEED_RECIPES")
    monkeypatch.setattr(gr, "seed_recipes_enabled", lambda: True)
    assert seed_recipes(_ctx(ws, language=Language.OPENGL_PYTHON)) == []  # opengl_python: nothing seeded
    assert (ws.src / "common.glsl").read_text() == before
    monkeypatch.setenv("CV3D_SEED_RECIPES", "off")
    assert Settings().limits.seed_recipes is False                       # the flat alias reaches Settings too


# ----------------------------------------------------------------------------- compose / lint / prompt
def test_seeded_common_composes_and_lints_clean(tmp_path) -> None:
    ws = _ws(tmp_path)
    seed_recipes(_ctx(ws))
    common = (ws.src / "common.glsl").read_text()
    shader = ("void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n"
              "    vec2 p = (fragCoord - 0.5 * u_resolution) / u_resolution.y; float t = u_time;\n"
              "    vec3 col = vec3(0.0, 0.0, 0.012) + vec3(0.9) * stars(p, 160.0, 0.22);\n"
              "    vec3 au = aurora(p, t); col += au * 1.7 + au * au * 0.5;\n"
              "    fragColor = vec4(pow(col / (1.0 + 0.6 * col), vec3(0.4545)), 1.0);\n}\n")
    assert lint_text(shader, common) == []
    comp = compose(shader, common)
    assert comp.convention == "mainImage" and not comp.uses_feedback
    src = comp.source
    assert src.index("#version 330 core") < src.index(HEADER) < src.index("float curtain(") < src.index("vec3 aurora(") < src.index("void mainImage(")
    assert comp.line_map.segments[1].file == "src/common.glsl"
    # a real compile when a GL context exists (the harness's own host; skipped otherwise)
    pytest.importorskip("moderngl")
    from codeverse.spatial.gl_render import GlHost, GlHostError

    try:
        res = GlHost(timeout_s=120).render_fragment_shader(src, tmp_path / "out", width=96, height=54, times=(0.0, 2.5))
    except GlHostError as e:  # pragma: no cover - machine without GL
        pytest.skip(f"no OpenGL context: {e}")
    if not res.ok and res.stage == "context":  # pragma: no cover
        pytest.skip(f"no OpenGL context: {res.error_message}")
    assert res.ok, res.error_message
    assert len(res.frames) == 2 and all(Path(f.path).is_file() for f in res.frames)


def test_prompt_block_lists_the_seeded_names(tmp_path) -> None:
    ws = _ws(tmp_path)
    ctx = _ctx(ws, plan=GraphicsPlan.model_validate({**plan_example(), "key_visuals": ["green aurora curtains", "dense stars"]}))
    names = seed_recipes(ctx)
    d = graphics_prompt_context(ctx, skeleton_files={}, previous_error="")
    assert [r["name"] for r in d["seeded_recipes"]] == names
    gen = render("tracks/generate_graphics.j2", **d)
    assert "Verified helpers ALREADY in `src/common.glsl`" in gen and "do not rewrite" in gen
    for n in names:
        assert re.search(rf"^- `[^`]*\b{n}\([^`]*\)` — .+$", gen, re.M), n
    assert "float curtain(vec2 p, float t, float seed, out float k)" in gen and "never a comb of bars" in gen
    assert gen.index("Cookbook excerpt:") < gen.index("Verified helpers ALREADY") < gen.index("## Plan")
    ref = render("tracks/refine_graphics.j2", **graphics_prompt_context(
        ctx, round_index=1, tasks=["[judge/likeness] overall: no curtain"], targets=["overall"], files=["src/shader.frag"],
        judge_summary="Previous score 0.3", frame_notes="(no frame metrics)", current_files={}))
    assert "harness-seeded VERIFIED helpers" in ref and all(f"`{n}`" in ref for n in names)
    # the sibling prompt-context test builds ctx without .extra: the key still resolves to []
    bare = SimpleNamespace(**{k: v for k, v in vars(ctx).items() if k != "extra"})
    assert graphics_prompt_context(bare, skeleton_files={}, previous_error="")["seeded_recipes"] == []
