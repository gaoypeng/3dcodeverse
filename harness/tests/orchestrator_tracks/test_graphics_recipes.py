"""Harness-owned GLSL recipe extraction, seeding, composition, and linting."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import GraphicsPlan
from codeverse3d.languages import get_runtime
from codeverse3d.languages.glsl_shader import (
    COMMON_GLSL,
    GlslShaderRuntime,
    compose,
    lint_text,
    lint_workspace,
    write_skeleton,
)
from codeverse3d.languages.glsl_shader import HEADER as WRAP_HEADER
from codeverse3d.proc import EventLog
from codeverse3d.prompts import load_text
from codeverse3d.tracks.graphics import (
    HEADER,
    RECIPES_REL,
    RESUME_HEADER,
    cookbook_functions,
    defined_names,
    is_skeleton_common,
    seed_recipes,
    seeded_on_disk,
)
from codeverse3d.workspace import Workspace

AURORA = "Aurora borealis over a mountain ridge with a frozen lake, dense stars, green and violet curtains"
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
    return SimpleNamespace(cookbook_text=load_text("glsl_shader/cookbook.md"), plan=plan,
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
    assert lint_text(SHADER, common, recipes_src=recipes) == [] and lint_workspace(ws).passed
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


def test_a_single_shot_repair_never_inlines_the_harness_owned_recipes(tmp_path) -> None:
    from codeverse3d.contracts.artifacts import BuildResult, GateReport
    from codeverse3d.tracks.repair import files_for_repair

    ws = _ws(tmp_path, skeleton=False)
    _recipes(ws).write_text("float hash12(vec2 p) { return 0.0; }\n")
    (ws.src / "shader.frag").write_text(SHADER)
    ctx = SimpleNamespace(ws=ws, language=Language.GLSL_SHADER, runtime=SimpleNamespace())
    build = BuildResult(ok=False, language="glsl_shader", error_file=str(_recipes(ws)))
    assert list(files_for_repair(ctx, build, GateReport(gate="lint", passed=True), ["src/shader.frag"])) == ["src/shader.frag"]
