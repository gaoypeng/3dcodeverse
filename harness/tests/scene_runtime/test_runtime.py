"""SceneThreeJsRuntime: protocol conformance + build gate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.contracts.common import TRACK_LANGUAGES, Language, Track
from codeverse3d.languages import get_runtime
from codeverse3d.languages.base import LanguageRuntime, SceneRuntime
from codeverse3d.languages.scene_threejs import SceneThreeJsRuntime
from codeverse3d.prompts.catalog import language_text
from tests.scene_runtime.conftest import needs_browser


def test_runtime_registered_conforms_and_writes_skeleton(ws):
    rt = get_runtime(Language.SCENE_THREEJS)
    assert isinstance(rt, SceneThreeJsRuntime)
    assert isinstance(rt, LanguageRuntime)
    # the scene track reaches every scene language only through SceneRuntime (D101)
    assert all(isinstance(get_runtime(lang), SceneRuntime) for lang in TRACK_LANGUAGES[Track.SCENE])
    assert rt.language == Language.SCENE_THREEJS
    assert "src/scene.js" in rt.entry_globs and "src/zones/*.js" in rt.entry_globs
    doc = language_text(rt.language, "contract.md")
    assert "createScene" in doc and "update(t, dt)" in doc
    paths = rt.skeleton(ws, None)
    assert (ws.src / "scene.js") in paths


#: an asset module composing an effect-library factory around its own plinth (D100)
LIB_ASSET = """import { makeTree } from '../lib/tree.js';
export function buildBirchStand(THREE, opts = {}) {
  const g = new THREE.Group(); g.name = 'BirchStand';
  const tree = makeTree({ species: 'birch', height: 5, leafSegments: 3, maxLeaves: 400, seed: opts.seed ?? 3 });
  g.add(tree);
  const plinth = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.6, 0.2, 12),
                                new THREE.MeshStandardMaterial({ color: 0x6b6259 }));
  plinth.position.y = 0.1; g.add(plinth);
  return g;
}
"""


def add_lib_asset(ws, body: str = LIB_ASSET) -> str:
    rel = "src/assets/birch_stand.js"
    (ws.root / rel).parent.mkdir(parents=True, exist_ok=True)
    (ws.root / rel).write_text(body)
    return rel


@pytest.mark.node
def test_asset_composing_a_library_factory_passes_the_asset_check(starter_ws):
    """D100: an asset may import ``../lib/``.  The factory's triangles are the library's, not the
    asset's budget, and its foot is its origin (a tree's roots go below grade by design) — before,
    every such asset failed the check (80k tris "over budget", roots "sinking") and escalated."""
    from codeverse3d.languages.scene_threejs import lint
    from codeverse3d.tracks.scene_assets import ASSET_MAX_TRIS, check_threejs_asset

    ctx = SimpleNamespace(ws=starter_ws)
    rel = add_lib_asset(starter_ws)
    assert not [f for f in lint(starter_ws).findings if f.target.startswith(rel)]
    chk = check_threejs_asset(ctx, rel, "BirchStand", expected_size_m=(3.0, 5.0, 3.0))
    assert chk.ran and chk.ok, chk.errors
    assert chk.tris < 200 < ASSET_MAX_TRIS < chk.lib_tris and chk.min_y == 0.0
    # the asset's OWN geometry is still held to the rules — a sunk plinth — and so is where it
    # puts a factory: a tree lowered by half a metre sinks by its origin
    for sunk, text in (("plinth.position.y = -0.3", "sinks 0.40 m"), ("plinth.position.y = 0.1; tree.position.y = -0.5", "sinks 0.50 m")):
        add_lib_asset(starter_ws, LIB_ASSET.replace("plinth.position.y = 0.1", sunk))
        assert any(text in e for e in check_threejs_asset(ctx, rel, "BirchStand").errors), sunk
    # ... and a factory at its heaviest defaults is past the one-object ceiling
    add_lib_asset(starter_ws, LIB_ASSET.replace("leafSegments: 3, maxLeaves: 400, ", ""))
    heavy = check_threejs_asset(ctx, rel, "BirchStand")
    assert not heavy.fatal and any("effect-library factories" in e for e in heavy.errors), heavy.errors


@pytest.mark.node
@needs_browser
def test_build_ok_on_example(starter_ws):
    """The example scene: one probe boot passes both gates and records its census — with a zone
    placing an asset that composes a library factory (D100)."""
    add_lib_asset(starter_ws)
    meadow = starter_ws.src / "zones" / "meadow.js"
    meadow.write_text(meadow.read_text()
                      .replace("import { buildWindmill }", "import { buildBirchStand } from '../assets/birch_stand.js';\nimport { buildWindmill }")
                      .replace("  return zone;\n", "  const stand = buildBirchStand(THREE); stand.position.set(6, 0, 6); zone.add(stand);\n  return zone;\n"))
    rt = SceneThreeJsRuntime()
    res = rt.build(starter_ws)
    assert res.ok, res.stdout_tail
    assert "BirchStand" in json.dumps(res.census), "the zone placed the library-composing asset"
    assert res.language == "scene_threejs" and res.glb_path is None
    census = res.census
    assert census["totals"]["meshes"] > 10 and census["totals"]["lights"] == 3
    assert {g["name"] for g in census["groups"]} == {"Environment", "Meadow", "Pondside"}
    assert census["content_bbox"]["size"][0] > 50
    assert (starter_ws.artifacts / "build.json").is_file() and (starter_ws.artifacts / "census.json").is_file()
    probe, shaders = res.gates
    # no wall-clock bound: a loaded box (load 25-30) took 15-25 s here, and a production
    # browser-loss retry is a second boot — neither is a defect of the build
    assert probe.gate == "scene_probe" and probe.passed
    assert json.loads((starter_ws.artifacts / "gates" / "shader_preflight.json").read_text())["passed"]
    assert shaders.gate == "shader_preflight"
    info = [f for f in shaders.findings if f.severity == Severity.INFO]
    assert info and info[0].data.get("programs", 0) >= 3


@pytest.mark.node
@needs_browser
def test_build_fails_with_file_line_on_shader_error(starter_ws):
    # the pond's water shader: a file the example scene compiles (the starter's sky comes
    # from lib/environment.js worldShell since D71)
    p = starter_ws.src / "shaders" / "water.js"
    text = p.read_text()
    needle = "float fres = pow(1.0 - max(dot(N, V), 0.0), 3.0);"
    assert needle in text
    text = text.replace(needle, "float fres = pow(1.0 - max(dot(N, V), 0.0), 3.0) * undefinedThing;")
    p.write_text(text)
    line = next(i for i, ln in enumerate(text.splitlines(), 1) if "undefinedThing" in ln)
    res = SceneThreeJsRuntime().build(starter_ws)
    assert not res.ok
    assert res.error_file == "src/shaders/water.js" and res.error_line == line
    assert "undefinedThing" in res.error_message
    assert "shader_preflight: FAILED" in res.stdout_tail
    err = res.gates[1].errors[0]
    assert err.target == f"src/shaders/water.js:{line}", err
    assert "undeclared identifier" in err.message and "undefinedThing" in err.message
    assert err.data.get("material") and "PondWater" in err.data["material"]
    assert err.fix_hint


@pytest.mark.node
@needs_browser
def test_build_fails_on_import_error(starter_ws):
    (starter_ws.src / "scene.js").write_text("import { x } from './nope.js';\nexport function createScene() {}\n")
    res = SceneThreeJsRuntime().build(starter_ws)
    assert not res.ok and res.error_file == "src/scene.js" and "nope.js" in res.error_message
    err = res.gates[0].errors[0]
    assert err.data.get("stage") == "import" and err.target == "src/scene.js"
    assert "fix the syntax/import error" in err.fix_hint


def test_build_interprets_combined_summary_offline(ws, monkeypatch):
    """One probe boot yields both gate reports, including the no-boot shape."""
    import codeverse3d.languages.scene_threejs as rt_mod
    from codeverse3d.spatial.render_scene import NodeResult

    calls: list[list[str]] = []

    def fake_run(script, args, **kw):
        calls.append([script, *args])
        return NodeResult(0, "", "", {
            "ok": True,
            "boot": {"ok": True, "stage": "ready", "cameras": [{"name": "a"}], "camera_problems": []},
            "update_ok": True, "census": {"totals": {"meshes": 3, "lights": 1}},
            "console_errors": [], "console_warnings": [], "shader_errors": [],
            "shader_report": {"ok": False, "errors": [
                {"file": "src/shaders/x.js", "line": 7, "kind": "compile", "message": "fragment shader: bad", "fix_hint": "fix it"},
            ], "warnings": [], "compile": {"ms": 3, "programs": 4, "custom_materials": 1}, "duration_ms": 9},
        }, 5)

    import codeverse3d.spatial.probes as probes_mod
    monkeypatch.setattr(probes_mod, "run_scene_script", fake_run)
    (ws.src / "scene.js").write_text("export function createScene() {}\n")
    res = rt_mod.SceneThreeJsRuntime().build(ws)
    assert len(calls) == 1 and calls[0][0] == "probe_scene.mjs" and "--compile" in calls[0]
    assert not res.ok  # shader gate failed
    assert res.error_file == "src/shaders/x.js" and res.error_line == 7
    import json as _json
    probe = _json.loads((ws.artifacts / "gates" / "scene_probe.json").read_text())
    shaders = _json.loads((ws.artifacts / "gates" / "shader_preflight.json").read_text())
    assert probe["gate"] == "scene_probe" and probe["passed"]
    assert shaders["gate"] == "shader_preflight" and not shaders["passed"]
    assert shaders["findings"][0]["target"] == "src/shaders/x.js:7"
    assert (ws.artifacts / "census.json").is_file()

    # boot failure → shader gate failed-empty (old two-call behaviour preserved)
    def fake_run_noboot(script, args, **kw):
        return NodeResult(1, "", "", {
            "ok": False, "boot": {"ok": False, "stage": "import", "error": "SyntaxError: x"},
            "console_errors": [], "console_warnings": [], "shader_errors": [],
            "shader_report": {"ok": False, "skipped": "scene did not boot", "errors": [], "warnings": []},
        }, 5)

    monkeypatch.setattr(probes_mod, "run_scene_script", fake_run_noboot)
    res2 = rt_mod.SceneThreeJsRuntime().build(ws)
    assert not res2.ok
    shaders2 = _json.loads((ws.artifacts / "gates" / "shader_preflight.json").read_text())
    assert not shaders2["passed"] and shaders2["findings"] == []


def test_probe_crash_leaves_no_stale_probe_outputs(ws, monkeypatch):
    """A probe crash removes prior outputs and publishes a failed build."""
    import codeverse3d.spatial.probes as probes_mod
    from codeverse3d.spatial.render_scene import SceneRenderError

    for name in ("census.json", "scene_probe.json", "shader_preflight.json"):
        (ws.artifacts / name).write_text('{"stale": true}')
    (ws.artifacts / "build.json").write_text('{"ok": true}')

    def crash(script, args, **kw):
        raise SceneRenderError("chrome went away")

    monkeypatch.setattr(probes_mod, "run_scene_script", crash)
    res = SceneThreeJsRuntime().build(ws)
    assert not res.ok
    for name in ("census.json", "scene_probe.json", "shader_preflight.json"):
        assert not (ws.artifacts / name).exists(), name
    assert json.loads((ws.artifacts / "build.json").read_text())["ok"] is False


@pytest.mark.node
def test_hand_over_of_a_library_composing_asset_is_self_contained(starter_ws):
    """D100: the deliverable and the dataset sample read the round's commit, and ``src/lib/`` is
    committed with ``src/`` — every import of the handed-over code resolves inside it."""
    from codeverse3d.addons.dataset.sample import code_files_for_round
    from codeverse3d.contracts.common import Track
    from codeverse3d.contracts.run import RoundRecord, RunRecord, RunStatus
    from codeverse3d.contracts.spec import Spec
    from codeverse3d.languages.scene_threejs import lint
    from codeverse3d.record.deliverable import build_deliverable
    from codeverse3d.workspace import Workspace

    add_lib_asset(starter_ws)
    rnd = RoundRecord(index=0, kind="baseline", commit=starter_ws.commit("r0"))
    rec = RunRecord(spec=Spec(id="s", track=Track.SCENE, language=Language.SCENE_THREEJS, prompt="a grove"),
                    workspace=str(starter_ws.root), status=RunStatus.MAX_ROUNDS, rounds=[rnd])
    manifest = build_deliverable(starter_ws, rec, 0)
    shipped = {f.path for f in manifest.files}
    assert {"deliverable/src/assets/birch_stand.js", "deliverable/src/lib/tree.js", "deliverable/src/lib/lifecycle.js"} <= shipped
    bad = [f for f in lint(Workspace(starter_ws.deliverable)).findings if f.data.get("kind") == "bad_import"]
    assert not bad, bad
    files, source = code_files_for_round(starter_ws, rnd)
    assert source == "commit" and {p[len("deliverable/"):] for p in shipped if p.startswith("deliverable/src/")} <= set(files)
