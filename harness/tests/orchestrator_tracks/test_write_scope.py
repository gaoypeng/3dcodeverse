"""Write-scope enforcement at the task layer.

* the single-shot envelope (``write_files(only=...)``) skips a pre-existing path the
  task does not own — new files stay allowed, the entry only when owned;
* ``GenerationTask.owns_entry`` → ``AgentJob.always_writable`` wiring;
* the constructors whose plans are file-disjoint by construction (scoped baseline,
  zones, threejs assets, parallel refine groups) now set
  ``edit_only=True`` so the split they promise is enforced, not just prompted.
"""

from __future__ import annotations

from codeverse3d.contracts.artifacts import BuildResult, Judgment
from codeverse3d.contracts.chat import ChatResponse
from codeverse3d.contracts.common import Language, Track, Usage
from codeverse3d.contracts.plan import BBox, PartPlan, ScenePlan, StaticPlan
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.languages.scene_threejs import zone_file
from codeverse3d.orchestrator import RefineTask, RunState, TaskGroup
from codeverse3d.proc import EventLog
from codeverse3d.tracks.generation import (
    GenerationResult,
    GenerationTask,
    _always_writable,
    generate_files,
    run_agent_task,
    write_files,
)
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene import SceneTrack
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace

from .conftest import make_spec
from .fakes import FakeAgent, FakeRuntime, FakeServices


# --------------------------------------------------------------------------- envelope
def test_write_files_only_skips_existing_out_of_scope_paths(tmp_ws: Workspace):
    (tmp_ws.src / "parts").mkdir(parents=True, exist_ok=True)
    (tmp_ws.src / "zones").mkdir(parents=True, exist_ok=True)
    (tmp_ws.src / "parts" / "a.js").write_text("// old a\n")
    (tmp_ws.src / "zones" / "q.js").write_text("// old q\n")
    skipped: list[tuple[str, str]] = []
    files = {"src/parts/a.js": "// new a", "src/zones/q.js": "// new q", "src/parts/new.js": "// new"}
    changes = write_files(tmp_ws, files, only={"src/parts/a.js"},
                          on_skip=lambda p, r: skipped.append((p, r)))
    assert [c.path for c in changes] == ["src/parts/a.js", "src/parts/new.js"]
    assert (tmp_ws.src / "zones" / "q.js").read_text() == "// old q\n"
    assert skipped == [("src/zones/q.js", skipped[0][1])] and "scope" in skipped[0][1]
    # only=None keeps the old behaviour: everything valid is written
    assert [c.path for c in write_files(tmp_ws, {"src/zones/q.js": "// new q"})] == ["src/zones/q.js"]


class ScriptedModel:
    """Yields ``(text, finish_reason)`` pairs in order (same as test_fix_generation)."""

    def __init__(self, answers):
        self.answers = list(answers)

    def generate(self, req):
        text, fr = self.answers.pop(0)
        return ChatResponse(text=text, usage=Usage(backend="fake", cost_usd=0.001), finish_reason=fr)


ANSWER = (
    "=== FILE: src/parts/a.js ===\n// new a\n=== END FILE ===\n"
    "=== FILE: src/object.js ===\n// new entry\n=== END FILE ===\n"
    "=== FILE: src/parts/b.js ===\n// new b\n=== END FILE ===\n"
    "=== FILE: src/parts/new.js ===\n// new part\n=== END FILE ==="
)


def _envelope_ws(tmp_path) -> Workspace:
    ws = Workspace(tmp_path / "ws").create()
    ws.write_json(ws.spec_path, {"language": "threejs", "track": "static_object"})
    (ws.src / "parts").mkdir(parents=True, exist_ok=True)
    (ws.src / "object.js").write_text("// entry\n")
    (ws.src / "parts" / "a.js").write_text("// a\n")
    (ws.src / "parts" / "b.js").write_text("// b\n")
    return ws


def test_generate_files_enforces_the_task_scope(tmp_path):
    ws = _envelope_ws(tmp_path)
    events = EventLog(tmp_path / "e.jsonl")
    task = GenerationTask(label="refine_a", prompt="p", files_hint=["src/parts/a.js"], edit_only=True)
    res = generate_files(ws, model=ScriptedModel([(ANSWER, "STOP")]), task=task, events=events)
    assert res.ok and [c.path for c in res.files_changed] == ["src/parts/a.js", "src/parts/new.js"]
    assert (ws.src / "parts" / "a.js").read_text() == "// new a\n"
    assert (ws.src / "object.js").read_text() == "// entry\n", "the entry is not owned"
    assert (ws.src / "parts" / "b.js").read_text() == "// b\n", "the sibling part is protected"
    assert (ws.src / "parts" / "new.js").read_text() == "// new part\n", "new files stay allowed"
    skips = [e for e in events.read() if e["event"] == "generate.skipped_path"]
    assert {e["path"] for e in skips} == {"src/object.js", "src/parts/b.js"}
    assert all("scope" in e["reason"] for e in skips)


def test_generate_files_lets_an_owner_write_the_entry(tmp_path):
    ws = _envelope_ws(tmp_path)
    task = GenerationTask(label="refine_a", prompt="p", files_hint=["src/parts/a.js"],
                          edit_only=True, owns_entry=True)
    res = generate_files(ws, model=ScriptedModel([(ANSWER, "STOP")]), task=task,
                         events=EventLog(tmp_path / "e.jsonl"))
    assert res.ok and (ws.src / "object.js").read_text() == "// new entry\n"
    assert (ws.src / "parts" / "b.js").read_text() == "// b\n", "b stays out of scope"


def test_generate_files_without_edit_only_is_unchanged(tmp_path):
    ws = _envelope_ws(tmp_path)
    task = GenerationTask(label="baseline", prompt="p", files_hint=["src/parts/a.js"])
    generate_files(ws, model=ScriptedModel([(ANSWER, "STOP")]), task=task,
                   events=EventLog(tmp_path / "e.jsonl"))
    assert (ws.src / "object.js").read_text() == "// new entry\n"
    assert (ws.src / "parts" / "b.js").read_text() == "// new b\n"


def test_generate_files_never_rewrites_a_harness_owned_file(tmp_path):
    """The single-shot mirror of tests/agents/test_cli_write_scope.py's recipes test: the
    CLI path restored src/recipes.glsl post-hoc, the envelope path wrote it (2026-08-29)."""
    ws = Workspace(tmp_path / "ws").create()
    ws.write_json(ws.spec_path, {"language": "glsl_shader", "track": "graphics"})
    (ws.src / "recipes.glsl").write_text("float aurora(vec2 p){return 0.0;}\n")
    events = EventLog(tmp_path / "e.jsonl")
    answer = ("=== FILE: src/shader.frag ===\nvoid main(){}\n=== END FILE ===\n"
              "=== FILE: src/recipes.glsl ===\n// clobbered\n=== END FILE ===")
    res = generate_files(ws, model=ScriptedModel([(answer, "STOP")]),
                         task=GenerationTask(label="baseline", prompt="p"), events=events)
    assert res.ok and [c.path for c in res.files_changed] == ["src/shader.frag"]
    assert (ws.src / "recipes.glsl").read_text().startswith("float aurora")
    (skip,) = [e for e in events.read() if e["event"] == "generate.skipped_path"]
    assert skip["path"] == "src/recipes.glsl" and "harness-owned" in skip["reason"]



def test_generate_files_never_writes_under_a_harness_owned_directory(tmp_path):
    """``HARNESS_OWNED_SRC`` names ``src/lib/`` as a DIRECTORY; the envelope checked by exact
    match, so a single-shot answer could overwrite the effect library (B1, 2026-09-22) while
    the CLI path (``is_harness_owned``) reverted the same write."""
    ws = Workspace(tmp_path / "ws").create()
    ws.write_json(ws.spec_path, {"language": "scene_threejs", "track": "scene"})
    (ws.src / "lib").mkdir(parents=True)
    (ws.src / "lib" / "post.js").write_text("// harness effect library\n")
    events = EventLog(tmp_path / "e.jsonl")
    answer = ("=== FILE: src/scene.js ===\n// scene\n=== END FILE ===\n"
              "=== FILE: src/lib/post.js ===\n// clobbered\n=== END FILE ===")
    res = generate_files(ws, model=ScriptedModel([(answer, "STOP")]),
                         task=GenerationTask(label="baseline", prompt="p"), events=events)
    assert res.ok and [c.path for c in res.files_changed] == ["src/scene.js"]
    assert (ws.src / "lib" / "post.js").read_text() == "// harness effect library\n"
    (skip,) = [e for e in events.read() if e["event"] == "generate.skipped_path"]
    assert skip["path"] == "src/lib/post.js" and "harness-owned" in skip["reason"]

# --------------------------------------------------------------------------- entry ownership
def test_always_writable_requires_ownership():
    part = GenerationTask(label="p", prompt="p", files_hint=["src/parts/seat.js"])
    assert _always_writable("threejs", part) == []
    assert _always_writable("threejs", part.model_copy(update={"owns_entry": True})) == ["src/object.js"]
    hinted = GenerationTask(label="w", prompt="p", files_hint=["src/object.js", "src/parts/seat.js"])
    assert _always_writable("threejs", hinted) == ["src/object.js"], "files_hint naming the entry owns it"
    assert _always_writable("", part) == [] and _always_writable("nope", part) == []


def test_run_agent_task_wires_entry_ownership_into_the_job(tmp_ws: Workspace):
    tmp_ws.write_json(tmp_ws.spec_path, {"language": "threejs", "track": "static_object"})
    agent = FakeAgent(lambda job, ws: {"src/parts/seat.js": "// seat\n"})
    part = GenerationTask(label="detail_seat", prompt="p", files_hint=["src/parts/seat.js"], edit_only=True)
    res = run_agent_task(tmp_ws, agent=agent, task=part)
    assert res.ok and agent.jobs[0].edit_only is True and agent.jobs[0].always_writable == []
    run_agent_task(tmp_ws, agent=agent, task=part.model_copy(update={"owns_entry": True, "label": "d2"}))
    assert agent.jobs[1].always_writable == ["src/object.js"]


# --------------------------------------------------------------------------- constructors
def _big_plan(n: int = 8) -> StaticPlan:
    parts = [PartPlan(name="Body", role="main mass", description="the shell",
                      bbox=BBox(center=(0, 0.5, 0), extents=(0.6, 1.0, 0.4)))]
    for i in range(n - 1):
        parts.append(PartPlan(name=f"Fitting{i}", role=f"fitting {i}", description="a fitting",
                              bbox=BBox(center=(0.05 * i, 0.2 + 0.05 * i, 0.1), extents=(0.05, 0.05, 0.05)),
                              attach_to="Body"))
    return StaticPlan(object_name="Machine", summary="A machine, 0.6 x 0.4 x 1.0 m.",
                      overall_bbox=BBox(center=(0, 0.5, 0), extents=(0.6, 1.0, 0.4)), parts=parts,
                      acceptance=[])


def _static_ctx(tmp_path, settings):
    ws = Workspace(tmp_path / "runs" / "d").create()
    track = StaticObjectTrack(services=FakeServices(), agent=FakeAgent(lambda job, ws: None),
                              settings=settings, runtime=FakeRuntime(Language.THREEJS))
    ctx = track.build_context(make_spec(language=Language.THREEJS, generator="fake-agent:m"),
                              ws, EventLog(ws.events_path), RunState(slug="d"))
    ctx.plan = _big_plan()
    return track, ctx


def _round(i: int, score: float) -> RoundRecord:
    j = Judgment(rubric="r", scores={}, overall=score, passed=score >= 0.8)
    return RoundRecord(index=i, kind="refine", judgment=j, commit=f"c{i}",
                       build=BuildResult(ok=True, language="threejs"))


def test_scoped_static_constructors_enforce_their_file_sets(tmp_path, settings):
    track, ctx = _static_ctx(tmp_path, settings)
    tasks = track.baseline_tasks(ctx)
    parts, assemble = tasks[:-1], tasks[-1]
    assert len(parts) >= 2
    assert all(t.edit_only and not t.owns_entry for t in parts), "part sessions are scoped"
    assert assemble.owns_entry and not assemble.edit_only, "assemble owns the entry"
    last = _round(2, 0.62)
    # refine keeps entry access: the prompt promises it ("edit the entry file to import a new part")
    group = TaskGroup(tasks=[RefineTask(target="Body", kind="geometry", instruction="thicken",
                                        priority=2, files=["src/parts/body.js"])],
                      files=["src/parts/body.js"])
    rt = track._refine_task(ctx, group, last, 2, parallel=True)
    assert rt.edit_only and rt.owns_entry


def test_scene_constructors_enforce_their_file_sets(tmp_path, settings, monkeypatch):
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    ws = Workspace(tmp_path / "runs" / "s").create()
    track = SceneTrack(services=FakeServices(), settings=settings,
                       runtime=FakeRuntime(Language.SCENE_THREEJS))
    ctx = track.build_context(make_spec(Track.SCENE, Language.SCENE_THREEJS),
                              ws, EventLog(ws.events_path), RunState())
    ctx.plan = plan
    zone = plan.zones[0]
    zt = track._zone_task(ctx, [zone])
    assert zt.edit_only and not zt.owns_entry and zt.files_hint == [zone_file(zone.name)]
    if len(plan.zones) >= 2:
        bt = track._zone_task(ctx, list(plan.zones[:2]))
        assert bt.edit_only and bt.files_hint == [zone_file(z.name) for z in plan.zones[:2]]
    last = RoundRecord(index=0, kind="baseline", build=BuildResult(ok=True, language="scene_threejs"))
    group = TaskGroup(tasks=[RefineTask(target=zone.name, kind="geometry", instruction="denser",
                                        priority=2, files=[zone_file(zone.name)])], files=[zone_file(zone.name)])
    assert track._refine_task(ctx, group, last, 1, parallel=True).edit_only is True
    assert track._refine_task(ctx, group, last, 1, parallel=False).edit_only is False

    # threejs asset tasks share the scene workspace → scoped; blender heroes own a sub-workspace
    import codeverse3d.tracks.common as common
    import codeverse3d.tracks.scene_assets as scene_assets

    captured: dict[str, GenerationTask] = {}

    def fake_generate(ws, **kw):
        captured["task"] = kw["task"]
        return GenerationResult(ok=True, label=kw["task"].label)

    monkeypatch.setattr(common, "generate", fake_generate)   # the one seam: tracks.common.generate_for
    assert plan.assets, "plan_example(SCENE) is expected to plan assets"
    asset = plan.assets[0]
    scene_assets._generate_asset(ctx, asset, "src/assets/koi.js", language=Language.SCENE_THREEJS, attempt=0)
    assert captured["task"].edit_only is True and captured["task"].files_hint == ["src/assets/koi.js"]
    scene_assets._generate_asset(ctx, asset, "src/model.py", language=Language.BLENDER, attempt=0)
    assert captured["task"].edit_only is False
