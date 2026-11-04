"""scene_blender raw mode (D113, ``Settings.scene_lib`` off, the ``argon_raw`` variant): the raw skeleton, the lint
(a ``lib`` import is an ERROR in raw mode only), the knob read at call time, the overlay's knob, and the raw stubs
building in real Blender."""

from __future__ import annotations

import ast
import re

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
from codeverse3d.prompts.variants import (
    activate,
    knobs_applied,
    variant_info,
)
from codeverse3d.tracks.planner import plan_example
from codeverse3d.workspace import Workspace

BPY = Language.SCENE_BLENDER
RAW = "argon_raw"
#: what names the harness library in text a model reads: the workspace path, a dotted helper call
#: (``lib.place.seat``), an import of the package, a module file (``lib/vegetation.py``)
LIB_MENTION = re.compile(r"src/lib|\blib\.[a-z_]+|\bfrom lib\b|\bimport lib\b|\blib/[a-z_]+\.py")
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
    rep = lint(tmp_ws, lib=False)
    assert rep.passed and not [f for f in rep.findings if f.severity.value != "info"], [(f.target, f.message) for f in rep.findings]


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
