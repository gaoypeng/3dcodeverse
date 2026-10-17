"""scene_blender raw mode (D113, ``Settings.scene_lib`` off, the ``argon_raw`` variant): no harness bpy
library ships, so every mesh, material, light, world and motion of the scene is the model's.

Pinned here: the raw skeleton (no ``src/lib``, no stub that imports or names it, the plan-less
example refused), the lint (a ``lib`` import is an ERROR in raw mode only), the runtime reading the
knob at call time, the ``argon_raw`` overlay (it resolves, its knob reaches the settings a run uses,
no file of it names the library, every overlaid stage template renders with the scene track's
context) and — in real Blender — the raw placeholder env and an empty zone building through the
wrapper.  The default (library) mode is pinned byte-identical by the prompt manifest and the
existing scene_blender tests."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.languages.scene_blender import (
    SceneBlenderRuntime,
    lint,
    lint_scene_source,
    write_skeleton,
)
from codeverse3d.prompts import render
from codeverse3d.prompts.variants import (
    VARIANTS_DIR,
    activate,
    knobs_applied,
    run_knob,
    variant_info,
)
from codeverse3d.skills import variant_dir
from codeverse3d.tracks.planner import build_system_prompt, plan_example
from codeverse3d.workspace import Workspace

BPY = Language.SCENE_BLENDER
RAW = "argon_raw"
#: what names the harness library in text a model reads: the workspace path, a dotted helper call
#: (``lib.place.seat``), an import of the package, a module file (``lib/vegetation.py``)
LIB_MENTION = re.compile(r"src/lib|\blib\.[a-z_]+|\bfrom lib\b|\bimport lib\b|\blib/[a-z_]+\.py")
#: the stage templates the overlay shadows (the planner's included)
OVERLAID_TEMPLATES = ("tracks/scene_env_bpy.j2", "tracks/scene_zone_bpy.j2", "tracks/scene_refine_bpy.j2",
                      "tracks/scene_asset_bpy.j2", "tracks/plan_scene_bpy.j2")


def _plan() -> ScenePlan:
    return ScenePlan.model_validate(plan_example(Track.SCENE, BPY))


def _lib_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    out = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names if a.name.split(".")[0] == "lib"]
    return out + [str(n.module) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "lib"]


# --------------------------------------------------------------------------- the skeleton
def test_the_raw_skeleton_ships_no_library_and_no_stub_imports_or_names_it(tmp_ws) -> None:
    plan = _plan()
    written = {p.relative_to(tmp_ws.root).as_posix() for p in write_skeleton(tmp_ws, plan, lib=False)}
    assert not (tmp_ws.src / "lib").exists() and not any(rel.startswith("src/lib/") for rel in written), sorted(written)
    assert {"src/env.py", "src/scene.py", "src/zones/quay.py", "src/assets/bollard.py"} <= written
    for p in sorted(tmp_ws.src.rglob("*.py")):
        text = p.read_text()
        assert not _lib_imports(text), (p.name, _lib_imports(text))
        assert not LIB_MENTION.search(text), (p.name, LIB_MENTION.search(text))
        assert "toolbox" not in text, p.name
    env = (tmp_ws.src / "env.py").read_text()
    assert "def height_at(x, y):" in env and "def build_env(ctx):" in env and '"Ground"' in env
    assert "worlds.new" not in env and "lights.new" not in env, "the placeholder builds ONE plate and nothing else"
    rep = lint(tmp_ws, lib=False)
    assert rep.passed and not [f for f in rep.findings if f.severity.value != "info"], [(f.target, f.message) for f in rep.findings]
    # the interior placeholder too: the plan's data, no library helper named
    inside = Workspace(tmp_ws.root.parent / "inside")
    write_skeleton(inside, plan.model_copy(update={"interior": True, "glazed": True}), lib=False)
    env = (inside.src / "env.py").read_text()
    assert "INTERIOR = True" in env and "GLAZED = True" in env and not LIB_MENTION.search(env) and "room_shell" not in env


def test_the_plan_less_example_is_refused_in_raw_mode(tmp_ws) -> None:
    """The example scene is built on the library: a raw run starts from a plan's stubs or not at all."""
    with pytest.raises(ValueError, match="raw mode"):
        write_skeleton(tmp_ws, None, lib=False)
    assert not tmp_ws.src.exists() or not any(tmp_ws.src.rglob("*.py"))


def test_the_runtime_reads_the_knob_at_call_time(tmp_ws, monkeypatch) -> None:
    """The run's knob is the ACTIVE variant's (``run_knob``, per thread): a runtime built before or inside
    the run follows it, and the default comes back after."""
    monkeypatch.delenv("C3D_SCENE_LIB", raising=False)
    settings = Settings()
    rt = SceneBlenderRuntime(settings=settings)
    assert rt.ships_lib is True
    with activate(RAW), knobs_applied(settings, variant_info(RAW)) as applied:
        assert applied == {"scene_lib": False} and rt.ships_lib is False
        assert settings.scene_lib is True, "a per-run knob is never written onto the shared settings"
        rt.skeleton(tmp_ws, _plan())
        assert not (tmp_ws.src / "lib").exists() and rt.lib_interface(tmp_ws) == ""
        (tmp_ws.src / "zones" / "quay.py").write_text("from lib.materials import material\n\n\ndef build(ctx):\n    return ctx.collection\n")
        assert not rt.lint(tmp_ws).passed
    assert rt.ships_lib is True and rt.lint(tmp_ws).passed, "library mode: the import is allowed again"


# --------------------------------------------------------------------------- the lint
def test_a_lib_import_is_an_error_in_raw_mode_and_not_in_library_mode() -> None:
    source = "from lib.materials import material\nimport bpy\n\n\ndef build(ctx):\n    return ctx.collection\n"
    raw = [f for f in lint_scene_source(source, target="src/zones/quay.py", role="zone", lib=False) if "lib" in f.message]
    assert len(raw) == 1 and raw[0].severity.value == "error" and raw[0].data["kind"] == "bad_import", raw
    assert "no harness library ships in raw mode" in raw[0].message and "src/env.py" in raw[0].fix_hint
    assert raw[0].target == "src/zones/quay.py:1"
    assert not [f for f in lint_scene_source(source, target="src/zones/quay.py", role="zone") if f.severity.value != "info"]
    for spelling in ("import lib.place\n", "from lib import place\n"):
        assert any(f.severity.value == "error" for f in lint_scene_source(spelling, target="src/env.py", role="helper", lib=False)), spelling
    # the shared code of a raw scene lives in env.py: importing it stays allowed
    assert not lint_scene_source("from env import linked\n", target="src/zones/quay.py", role="helper", lib=False)


# --------------------------------------------------------------------------- the variant
def test_the_argon_raw_variant_resolves_and_its_knob_is_a_default_a_statement_beats(monkeypatch) -> None:
    info = variant_info(RAW)
    assert info.version == 1 and info.generators == ("agy:gemini-4-argon",) and info.knobs == {"scene_lib": False}
    monkeypatch.delenv("C3D_SCENE_LIB", raising=False)
    s = Settings()
    with activate(RAW), knobs_applied(s, info) as applied:
        assert applied == {"scene_lib": False} and run_knob("scene_lib", s) is False
    assert s.scene_lib is True and "scene_lib" not in s.model_fields_set and run_knob("scene_lib", s) is True
    with activate(""):
        assert run_knob("scene_lib", s) is True, "the base run: the field's default"
    monkeypatch.setenv("C3D_SCENE_LIB", "1")          # the user's own statement wins over the knob
    s = Settings()
    with activate(RAW), knobs_applied(s, info) as applied:
        assert applied == {} and run_knob("scene_lib", s) is True
    monkeypatch.setenv("C3D_SCENE_LIB", "0")          # ... and so does raw mode stated without the variant
    with activate(""):
        assert run_knob("scene_lib", Settings()) is False


def test_parallel_runs_of_one_process_never_flip_each_others_mode(tmp_path, monkeypatch) -> None:
    """The bench runs ``--parallel N`` runs as threads of ONE process sharing the settings singleton.  A
    raw run ending while a library run is still going (and the reverse) changes neither's answer, and
    the process is left at its default (the D113 review's race: the knob used to be written onto the
    singleton and put back, unreferenced-counted, by whichever run ended)."""
    import threading

    monkeypatch.delenv("C3D_SCENE_LIB", raising=False)
    get_settings.cache_clear()
    rt, seen = SceneBlenderRuntime(), {"raw": [], "lib": []}
    raw_in, lib_in, raw_out = threading.Event(), threading.Event(), threading.Event()

    def run(name: str, variant: str, start: threading.Event, wait: threading.Event, done: threading.Event | None) -> None:
        with activate(variant), knobs_applied(get_settings(), variant_info(variant) if variant else None):
            start.set()
            seen[name].append(rt.ships_lib)
            wait.wait(5)
            seen[name].append(rt.ships_lib)
        if done is not None:
            done.set()

    try:
        a = threading.Thread(target=run, args=("raw", RAW, raw_in, lib_in, raw_out))   # starts first, ends first
        b = threading.Thread(target=run, args=("lib", "", lib_in, raw_out, None))
        a.start()
        raw_in.wait(5)
        b.start()
        a.join(10)
        b.join(10)
        assert seen == {"raw": [False, False], "lib": [True, True]}, seen
        assert get_settings().scene_lib is True and rt.ships_lib is True
    finally:
        get_settings.cache_clear()


def test_a_run_context_with_the_real_runtime_decides_once(tmp_ws, tmp_path, monkeypatch) -> None:
    """The run's real ``SceneBlenderRuntime`` is the one answer: the track's ``ships_lib`` asks it, the
    skeleton stage's cache key carries raw mode (a resume that changed the mode writes the skeleton
    again — and removes the other mode's library), and the lint rejects ``lib`` — under the variant, in
    a fan-out worker thread like the zone sessions."""
    from codeverse3d.proc import fan_out
    from codeverse3d.tracks import scene_assets as SA
    from codeverse3d.tracks.lifecycle import SKELETON
    from tests.orchestrator_tracks.conftest import make_spec

    monkeypatch.delenv("C3D_SCENE_LIB", raising=False)
    settings = Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")
    spec, plan = make_spec(Track.SCENE, BPY), _plan()
    ctx = _ctx_bpy(tmp_ws, settings, spec, plan)
    lib_key = SKELETON.key(ctx)
    assert "scene_lib" not in lib_key and SA.ships_lib(ctx) is True
    ctx.runtime.skeleton(tmp_ws, plan)
    assert (tmp_ws.src / "lib").is_dir()
    with activate(RAW):
        def worker(_: int) -> tuple[bool, dict]:
            return SA.ships_lib(ctx), SKELETON.key(ctx)

        (ships, key), = fan_out([0], worker, max_workers=1, label="t", item_name=str)
        assert ships is False and key == {**lib_key, "scene_lib": False}
        ctx.runtime.skeleton(tmp_ws, plan)
        assert not (tmp_ws.src / "lib").exists(), "the library run's src/lib does not outlive a switch to raw"
        (tmp_ws.src / "zones" / "quay.py").write_text("from lib.materials import material\n\n\ndef build(ctx):\n    return ctx.collection\n")
        assert not ctx.runtime.lint(tmp_ws).passed
    # a rig's stated Settings(scene_lib=False) with the real runtime: the runtime still answers for both
    raw_ctx = _ctx_bpy(tmp_ws, Settings(scene_lib=False), spec, plan)
    assert SA.ships_lib(raw_ctx) is raw_ctx.runtime.ships_lib


def _ctx_bpy(ws: Workspace, settings: Settings, spec, plan):
    from dataclasses import replace

    from tests.orchestrator_tracks.test_prompts_assets import _ctx

    return replace(_ctx(ws, settings, spec, plan, agent_id="fake:x"), runtime=SceneBlenderRuntime())


def test_a_variant_toml_knob_scene_lib_is_type_checked(tmp_path, monkeypatch) -> None:
    from codeverse3d.prompts import variants as V

    (tmp_path / "tv").mkdir()
    (tmp_path / "tv" / "variant.toml").write_text('[variant]\nname = "tv"\n[knobs]\nscene_lib = 0\n')
    monkeypatch.setattr(V, "VARIANTS_DIR", tmp_path)
    with pytest.raises(ValueError, match="scene_lib must be a bool"):
        variant_info("tv")


def test_no_file_of_the_overlay_names_the_library() -> None:
    """Prompts AND skills: every file a model can read under ``argon_raw`` (the READMEs, which no model
    reads, are left out) — no ``src/lib``, no ``lib.<module>`` call, no import of it, no module file."""
    files = {p.relative_to(root).as_posix(): p for root in (VARIANTS_DIR / RAW, variant_dir(RAW)) for p in sorted(root.rglob("*"))
             if p.is_file() and p.name != "README.md" and "__pycache__" not in p.parts}
    names = set(files)
    assert {"scene_blender/contract.md", "tracks/scene_env_bpy.j2", "c3d-bpy-scene-placement/SKILL.md"} <= names, sorted(names)
    for rel, p in files.items():
        hits = sorted({m.group(0) for m in LIB_MENTION.finditer(p.read_text(encoding="utf-8"))})
        assert not hits, (rel, hits)


def test_every_overlaid_template_renders_with_the_scene_track_context(tmp_ws, tmp_path) -> None:
    """The env / zone / refine / asset briefs through the scene track's own context builders, and the
    planner's system prompt — under the variant, so the overlay's copies render (a jinja block the
    track does not feed would raise under StrictUndefined or drop a section)."""
    from codeverse3d.tracks import scene_assets as SA
    from codeverse3d.tracks.scene import SceneTrack, plan_bbox_line, scene_templates
    from codeverse3d.tracks.zone_layout import layout_block
    from tests.orchestrator_tracks.conftest import make_spec
    from tests.orchestrator_tracks.fakes import small_scene
    from tests.orchestrator_tracks.test_prompts_assets import _ctx

    plan = small_scene(BPY)
    spec = make_spec(Track.SCENE, BPY)
    settings = Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")
    ctx = _ctx(tmp_ws, settings, spec, plan, agent_id="fake:x")
    with activate(RAW), knobs_applied(ctx.settings, variant_info(RAW)):
        ctx.extra["asset_api"] = SA.asset_api_summary(plan, {}, language=BPY, lib=SA.ships_lib(ctx))
        st = SceneTrack(services=ctx.services)
        t = scene_templates(BPY)
        quay = plan.zones[0]
        prompts = {
            "env": render(t.env, **st._ctx(ctx, lib_interface="", env_check_error="", env_previous="")),
            "zone": render(t.zone, **st._ctx(ctx, zone_name=quay.name, zone_description=quay.description,
                                             zone_bbox=plan_bbox_line(quay.bbox, BPY), zone_contents=quay.contents,
                                             zone_file="src/zones/quay.py", neighbours=[], layout=layout_block(
                                                 {"zone": "Quay", "placements": [{"asset": "Bollard", "count": 2,
                                                                                  "cluster": [3.0, -4.0], "spread_m": 0.5}]},
                                                 language=BPY))),
            "refine": render(t.refine, **st._ctx(ctx, round_index=1, tasks=["t"], targets=["Quay"], files=["src/env.py"],
                                                 edit_only_these=False, judge_summary="j", current_files={})),
            "asset": SA._asset_prompt(ctx, plan.assets[0], "src/assets/bollard.py", language=BPY),
            "plan": build_system_prompt(spec, ScenePlan),
        }
        for name, text in prompts.items():
            assert text.strip(), name
            assert not LIB_MENTION.search(text), (name, LIB_MENTION.search(text))
        assert "No harness library ships" in prompts["env"] and "No harness library ships" in prompts["zone"]
        assert "write `src/env.py`" in prompts["env"] and "def build(ctx)" in prompts["zone"]
        assert "NO LIBRARY" in prompts["plan"] and "SHIPPED BPY LIBRARY" not in prompts["plan"]
        assert "instance_type = 'COLLECTION'" in prompts["zone"], "the asset API names the Empty, not a library call"
        assert "Reference rows" not in prompts["asset"], "no catalog row reaches a raw asset sheet"
    assert {Path(r).name for r in OVERLAID_TEMPLATES} <= {p.name for p in (VARIANTS_DIR / RAW / "tracks").glob("*.j2")}


def test_a_run_under_argon_raw_sees_the_overlay_and_the_knob(tmp_path) -> None:
    """The whole run, offline (the manifest's scenario): the knob reaches the settings the scene track
    reads (the asset API's raw wording), the planner and every session read the overlay, and the
    routed bpy bundles are the overlay's lib-free copies."""
    from tests.prompts.manifest import run_scenario

    payloads = run_scenario("scene.scene_blender.agent.variant_argon_raw", tmp_path).payloads
    readable = {k: v for k, v in payloads.items() if not k.endswith("/scope")}   # scope: the read-only list, not text
    for key, text in readable.items():
        assert not LIB_MENTION.search(text), (key, LIB_MENTION.search(text))
    assert "No harness library ships" in payloads["job:env@r00/prompt"]
    assert "instance_type = 'COLLECTION'" in payloads["job:zones_quay_water@r00/prompt"]
    assert "NO LIBRARY" in payloads["chat:planner:planner/system"]
    assert "c3d-bpy-scene-placement" in payloads["skills:runs/run:zone@r00/routed"]
    assert get_settings().scene_lib is True, "the knob does not outlive the run"


# --------------------------------------------------------------------------- in Blender
@pytest.mark.blender
def test_the_raw_placeholder_env_and_an_empty_zone_build_in_blender(tmp_path) -> None:
    """The raw stubs as written build in Blender 5.2 through the wrapper: no env error, every zone ok,
    the placeholder's Ground in the census, and no world, lamp or fog until the author writes them."""
    from codeverse3d.conventions import to_pascal

    if not get_settings().resolve_blender():
        pytest.skip("no Blender binary configured")
    ws = Workspace(tmp_path / "ws")
    rt = SceneBlenderRuntime(settings=Settings(scene_lib=False))
    plan = _plan()
    rt.skeleton(ws, plan)
    assert not (ws.src / "lib").exists()
    zones = {rt.zone_file(z.name).rsplit("/", 1)[-1][:-3] for z in plan.zones}
    run = rt.run_wrapper(ws, ws.artifacts, zones={n: to_pascal(n) for n in zones}, blend=False, timeout_s=600)
    errors = [(e.get("stage"), e.get("name"), e.get("error_file"), e.get("error_line"), e.get("error_message"))
              for e in run.report.get("errors") or []]
    assert run.result.ok and not errors, (run.result.error_type, run.result.error_message, errors)
    assert sorted(run.report["zones_ok"]) == sorted(zones)
    assert run.facts.get("fog") is None and not run.facts.get("lights"), {k: run.facts.get(k) for k in ("fog", "lights")}
    assert run.facts.get("unique_tris", 0) > 0, "the placeholder plate and the asset blockouts are geometry"


def test_the_mcp_server_applies_the_runs_variant_knobs_in_its_own_process(tmp_ws, monkeypatch) -> None:
    """The session's MCP server is a separate process: it reads the run's variant off ``spec.json`` (the
    frozen ``options.variant``) and applies its knobs at start, so its ``build`` lint is the round's."""
    from codeverse3d.spatial.mcp_server import apply_run_knobs, build_context
    from tests.orchestrator_tracks.conftest import make_spec

    monkeypatch.delenv("C3D_SCENE_LIB", raising=False)
    monkeypatch.delenv("C3D_VARIANT", raising=False)
    spec = make_spec(Track.SCENE, BPY)
    tmp_ws.write_json(tmp_ws.spec_path, spec.model_copy(update={"options": spec.options.model_copy(update={"variant": RAW})}))
    get_settings.cache_clear()
    try:
        ctx = build_context(tmp_ws.root)
        assert ctx.language == BPY.value and get_settings().scene_lib is True
        assert apply_run_knobs(ctx) == {"scene_lib": False} and get_settings().scene_lib is False
        tmp_ws.write_json(tmp_ws.spec_path, spec)          # a base run applies nothing
        get_settings.cache_clear()
        assert apply_run_knobs(build_context(tmp_ws.root)) == {} and get_settings().scene_lib is True
    finally:
        get_settings.cache_clear()
