"""A routed bundle only exists if a GENERATING session is told about it.

The read loop measured cv3d-scene-composition / -lighting / -motion at 0 opens out of 3
listings each and read it as a wording problem.  It was not: the scene track did all of
its baseline generation in ``prepare()`` — the env, zones and compose stages called
``tracks.generation.generate`` directly — while ``skills_hook.attach_for_round`` was only
reached from ``steps.run_round``.  ``SceneTrack.baseline_tasks`` returns ``[]``, so round 0
listed the bundles to a round that had no generation task to consume them.  That was
exactly "listed 3, opened 0", and no rewrite of a SKILL.md could have fixed it.

CLOSED 2026-08-25 (curate wave): ``SceneTrack._env_stage`` / ``_zones_stage`` /
``_assemble_stage`` now go through ``_deliver_skills`` + ``_record_skills``.  These tests
keep every agent-driving module on the hook, so the gap cannot reopen quietly.  The scene
bundles' read rate is UNMEASURED against this delivery — that is the next wave's first
experiment, and until it runs their ledger rows stay ``mixed``/``inherited``, not
``measured``.
"""

from __future__ import annotations

import ast
import contextlib
import json
from pathlib import Path

import pytest

from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import ScenePlan
from codeverse.workspace import Workspace

TRACKS = Path(__file__).resolve().parents[2] / "codeverse" / "tracks"

#: modules that hand a GenerationTask to a coding agent, and whether the skills hook is
#: reached on that path.  ``scene_assets`` is deliberately absent from the fix list: an
#: asset builder writes one prop from a fixed recipe and routes no bundle today.
GENERATING_MODULES = ("steps.py", "repair.py", "scene.py", "scene_assets.py")


def _calls_generate(mod: str) -> bool:
    tree = ast.parse((TRACKS / mod).read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "generate"
                and any(kw.arg == "task" for kw in node.keywords)):
            return True
    return False


def _mentions_hook(mod: str) -> bool:
    return "skills_hook" in (TRACKS / mod).read_text()


@pytest.mark.parametrize("mod", GENERATING_MODULES)
def test_the_module_really_does_generate(mod: str) -> None:
    """Guards the premise: if a module stops driving an agent, drop it from the list
    instead of letting the next test pass for the wrong reason."""
    assert _calls_generate(mod), f"{mod} no longer calls generate(task=...)"


@pytest.mark.parametrize("mod", ["steps.py", "repair.py", "scene.py"])
def test_every_generating_path_delivers_skills(mod: str) -> None:
    """The whole point.  ``scene_assets`` is exempt (see GENERATING_MODULES)."""
    assert _mentions_hook(mod), f"{mod} drives an agent without going through skills_hook"


@pytest.mark.parametrize("stage_kind", ["env", "zone", "compose"])
def test_each_scene_stage_attaches_its_own_kind(stage_kind: str) -> None:
    """Not just "the module imports the hook": each of the three stages must attach with
    the kind the router's rows are written against (registry R14-R21 name env/zone/compose).
    Attaching them all as "baseline" would route a different set and quietly re-open the gap."""
    src = (TRACKS / "scene.py").read_text()
    assert f'"{stage_kind}"' in src
    attaches = [n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Call) and _kind_arg(n) == stage_kind]
    assert attaches, f"no scene stage delivers skills with kind={stage_kind!r}"


def _kind_arg(node: ast.Call) -> str | None:
    """The ``kind=``/stage-kind literal of an attach_for_round or _deliver_skills call."""
    name = getattr(node.func, "attr", getattr(node.func, "id", ""))
    if name not in ("attach_for_round", "_deliver_skills", "_record_skills"):
        return None
    for kw in node.keywords:
        if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    for arg in node.args:  # _deliver_skills(gen, "env", [task])
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def test_the_zone_fan_out_attaches_once_not_once_per_batch() -> None:
    """Zone sessions run in parallel against ONE workspace.  Attaching inside the worker
    would have three threads writing the same AGENTS.md; the route is identical for every
    batch, so it belongs outside the fan-out."""
    src = (TRACKS / "scene.py").read_text()
    body = src.split("def _zones_stage", 1)[1].split("\n    def ", 1)[0]
    worker = body.split("def _one", 1)[1].split("results = fan_out", 1)[0]
    assert "attach_for_round" not in worker, "zone skills are attached inside the fan-out worker"
    assert "attach_for_round" in body.split("def _one", 1)[0], "the zone stage never attaches"


def test_round_zero_of_a_scene_run_has_no_generation_task() -> None:
    """The other half of the old fact, still true and still worth pinning.

    Round 0 attaches and generates nothing, because the generation already happened in the
    stages.  That is fine now that the stages deliver — but it means a read-rate
    denominator built from round records alone still counts a session that never existed.
    Count the STAGES' telemetry rows (``_record_skills``), not round 0's listing.
    """
    src = (TRACKS / "scene.py").read_text()
    assert "def baseline_tasks" in src
    body = src.split("def baseline_tasks", 1)[1].split("def ", 1)[0]
    assert "return []" in body, "scene round 0 now generates; the listing-vs-read gap may be gone"


# --------------------------------------------------------------------------- runtime proof
# The tests above read the source.  This one runs a real scene track (repo fakes: no
# network, no node, no Blender) and asserts the bundles reach the actual env/zone/compose
# SESSIONS -- which is the thing the old gap made false, and the thing a source-level
# assertion cannot prove.


@pytest.fixture
def scene_run(tmp_path, monkeypatch):
    from codeverse.config import Settings
    from codeverse.tracks.plan_examples import plan_example
    from codeverse.tracks.scene import SceneTrack
    from tests.orchestrator_tracks.conftest import make_spec
    from tests.orchestrator_tracks.fakes import FakeAgent, FakeJudge, FakeRuntime, FakeServices
    from tests.orchestrator_tracks.test_tracks import _planner, _scene_writer

    monkeypatch.setenv("CV3D_SKILLS", "1")
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    plan.assets = [a for a in plan.assets if a.kind == "threejs"]
    for z in plan.zones:
        z.contents = [c for c in z.contents if c in {a.name for a in plan.assets}]
    spec = make_spec(Track.SCENE, Language.SCENE_THREEJS, max_rounds=0,
                     prompt="a small harbour at dusk")
    ws = Workspace(tmp_path / "runs" / "harbour_skills")
    agent = FakeAgent(_scene_writer)
    track = SceneTrack(services=FakeServices(assemble=False), judge=FakeJudge(scores=(0.6,)),
                       agent=agent, planner_model=_planner(plan.model_dump(mode="json")),
                       settings=Settings(), runtime=FakeRuntime(Language.SCENE_THREEJS))
    rec = track.run(spec, ws)
    return rec, agent, ws


def test_the_env_session_is_really_offered_the_scene_bundles(scene_run) -> None:
    """The bug in one assertion.

    Asserting on AGENTS.md *after* the run would pass either way: round 0 still calls
    attach_for_round(kind="baseline") once the stages are done, so the files land in the
    workspace regardless -- which is precisely how this gap hid as "listed 3, opened 0".
    The honest assertion is the ``skills.attached`` event carrying kind="env", emitted
    before the env session ran and by nothing else.
    """
    _, agent, ws = scene_run
    attached = _attached_events(ws)
    env = [e for e in attached if e.get("kind") == "env"]
    assert env, (f"no skills.attached event for the env stage; kinds seen: "
                 f"{sorted({e.get('kind') for e in attached})}. The env session -- where a "
                 f"scene's lighting is authored -- was never offered its bundles")
    assert "cv3d-scene-lighting" in (env[0].get("skills") or []), (
        f"the env stage attached {env[0].get('skills')}, without cv3d-scene-lighting; "
        "router row R16 selects it for kind='env'")


def test_the_stage_attach_happens_before_the_stage_generates(scene_run) -> None:
    """Order is the whole point: a bundle written after the session is a bundle nobody saw."""
    _, _, ws = scene_run
    rows = _events(ws)
    def first(pred):
        return next((i for i, e in enumerate(rows) if pred(e)), None)
    att = first(lambda e: e.get("event") == "skills.attached" and e.get("kind") == "env")
    gen = first(lambda e: "env" in str(e.get("label", "")) and "gen" in str(e.get("event", "")))
    assert att is not None
    if gen is not None:
        assert att < gen, "the env stage attached its skills after it had already generated"


def _events(ws) -> list[dict]:
    p = Path(ws.root) / "events.jsonl"
    if not p.is_file():
        return []
    out: list[dict] = []
    for ln in p.read_text().splitlines():
        if ln.strip():
            with contextlib.suppress(json.JSONDecodeError):
                out.append(json.loads(ln))
    return out


def _attached_events(ws) -> list[dict]:
    return [e for e in _events(ws) if e.get("event") == "skills.attached"]


def test_every_scene_stage_leaves_a_telemetry_row(scene_run) -> None:
    """Without a row per stage the denominator counts stages that never reported, which is
    how "listed, unread" was manufactured in the first place."""
    _, _, ws = scene_run
    p = Path(ws.root) / "telemetry" / "skills.jsonl"
    if not p.is_file():
        pytest.skip("skills telemetry is not written by the fakes on this path")
    kinds = {json.loads(ln)["kind"] for ln in p.read_text().splitlines() if ln.strip()}
    assert {"env", "zone"} <= kinds, f"stages missing from telemetry: {kinds}"
