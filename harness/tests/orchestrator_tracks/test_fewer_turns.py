"""The fewer-turns switch inlines owned files and adds prompt discipline."""

from __future__ import annotations

from codeverse3d.config import FEWER_TURNS_ENV, Settings, fewer_turns_enabled, get_settings
from codeverse3d.contracts.common import Language
from codeverse3d.orchestrator import RefineTask, TaskGroup
from codeverse3d.tracks.plan_features import LIVE_SWITCHES
from codeverse3d.tracks.prompting import INLINE_MAX_CHARS, INLINE_MAX_FILES, TURN_DISCIPLINE

from .conftest import make_spec
from .fakes import FakeAgent, FakeRuntime, FakeServices
from .test_generation_depth import _round

SEAT = "import bpy\n\n\ndef build_seat():\n    return bpy.context.object  # SEAT-BODY\n"
LEG = "import bpy\n\n\ndef build_leg():\n    return bpy.context.object  # LEG-BODY\n"


def _ctx(tmp_path, plan, settings, *, language=Language.BLENDER, agent_id="fake-agent:m"):
    from codeverse3d.orchestrator import RunState
    from codeverse3d.proc import EventLog
    from codeverse3d.tracks.static_object import StaticObjectTrack
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "runs" / "d").create()
    track = StaticObjectTrack(services=FakeServices(), agent=FakeAgent(lambda job: None), settings=settings,
                              runtime=FakeRuntime(language))
    ctx = track.build_context(make_spec(language=language, generator=agent_id), ws, EventLog(ws.events_path), RunState(slug="d"))
    ctx.plan = plan
    return track, ctx


def _group(files):
    return TaskGroup(tasks=[RefineTask(target="Seat", kind="geometry", instruction="thicken the seat", priority=2, files=list(files))],
                     files=list(files))


def _write(ws, files):
    for rel, body in files.items():
        p = ws.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


# --------------------------------------------------------------------------- the switch
def test_switch_contract(monkeypatch):
    monkeypatch.delenv(FEWER_TURNS_ENV, raising=False)
    assert not fewer_turns_enabled()
    for raw in ("1", "on", "true", "YES"):
        monkeypatch.setenv(FEWER_TURNS_ENV, raw)
        assert fewer_turns_enabled()
    for raw in ("off", "maybe"):
        monkeypatch.setenv(FEWER_TURNS_ENV, raw)
        assert not fewer_turns_enabled()

    monkeypatch.setenv(FEWER_TURNS_ENV, "on")
    assert Settings().limits.fewer_turns is True
    monkeypatch.delenv(FEWER_TURNS_ENV)
    get_settings.cache_clear()
    assert Settings().limits.fewer_turns is False
    monkeypatch.setenv("C3D_LIMITS__FEWER_TURNS", "true")
    assert Settings().limits.fewer_turns is True
    assert LIVE_SWITCHES[FEWER_TURNS_ENV] == "codeverse3d/config.py"


# --------------------------------------------------------------------------- refine: the files ride along
def test_refine_prompt_inlines_a_small_scoped_file_set(tmp_path, chair_plan, settings, monkeypatch):
    monkeypatch.setenv(FEWER_TURNS_ENV, "1")
    track, ctx = _ctx(tmp_path, chair_plan, settings)
    files = {"src/parts/seat.py": SEAT, "src/parts/leg.py": LEG}
    _write(ctx.ws, files)
    task = track._refine_task(ctx, _group(files), _round(1, 0.6), 2, parallel=False)
    assert task.edit_only and task.files_hint == list(files)
    assert "SEAT-BODY" in task.prompt and "LEG-BODY" in task.prompt
    assert "--- src/parts/seat.py ---" in task.prompt and "--- src/parts/leg.py ---" in task.prompt
    assert "do NOT read_file them first" in task.prompt and "return each edited file COMPLETE" not in task.prompt
    assert "## Turn discipline" in task.prompt


def test_refine_prompt_inlines_nothing_when_off_or_too_big(tmp_path, chair_plan, settings, monkeypatch):
    monkeypatch.delenv(FEWER_TURNS_ENV, raising=False)
    track, ctx = _ctx(tmp_path, chair_plan, settings)
    files = {"src/parts/seat.py": SEAT, "src/parts/leg.py": LEG}
    _write(ctx.ws, files)
    off = track._refine_task(ctx, _group(files), _round(1, 0.6), 2, parallel=False).prompt
    assert "SEAT-BODY" not in off and "Current file contents" not in off and "Turn discipline" not in off
    monkeypatch.setenv(FEWER_TURNS_ENV, "1")
    many = {f"src/parts/p{i}.py": SEAT for i in range(INLINE_MAX_FILES + 1)}
    _write(ctx.ws, many)
    assert "SEAT-BODY" not in track._refine_task(ctx, _group(many), _round(1, 0.6), 2, parallel=False).prompt
    big = {"src/parts/big.py": "import bpy\n" + "# " + "x" * INLINE_MAX_CHARS + "\n"}
    _write(ctx.ws, big)
    p = track._refine_task(ctx, _group(big), _round(1, 0.6), 2, parallel=False).prompt
    assert "Current file contents" not in p                    # not truncated INTO the prompt: left on disk
    # an unscoped group (target "overall") keeps the whole tree and inlines nothing
    whole = track._refine_task(ctx, TaskGroup(tasks=[RefineTask(target="overall", kind="geometry", instruction="x", priority=2)]),
                               _round(1, 0.6), 2, parallel=False)
    assert not whole.edit_only and "Current file contents" not in whole.prompt


def test_single_shot_refine_still_inlines_regardless_of_the_switch(tmp_path, chair_plan, settings, monkeypatch):
    monkeypatch.delenv(FEWER_TURNS_ENV, raising=False)
    track, ctx = _ctx(tmp_path, chair_plan, settings, agent_id="single-shot:fake:m")
    files = {"src/parts/seat.py": SEAT}
    _write(ctx.ws, files)
    p = track._refine_task(ctx, _group(files), _round(1, 0.6), 2, parallel=False).prompt
    assert "SEAT-BODY" in p and "return each edited file COMPLETE" in p and "Turn discipline" not in p


# --------------------------------------------------------------------------- baseline: every file in turn one
def test_baseline_prompt_carries_the_turn_discipline_block(tmp_path, chair_plan, settings, monkeypatch):
    monkeypatch.setenv("C3D_SCOPED_PARTS", "off")
    monkeypatch.setenv(FEWER_TURNS_ENV, "1")
    track, ctx = _ctx(tmp_path, chair_plan, settings)
    (task,) = track.baseline_tasks(ctx)
    assert task.label == "baseline" and TURN_DISCIPLINE in task.prompt
    assert "write EVERY file" in task.prompt and "do not call those two tools separately".lower() in task.prompt.lower()
    assert task.prompt.index("## Turn discipline") < task.prompt.index("## Output")
    monkeypatch.delenv(FEWER_TURNS_ENV)
    get_settings.cache_clear()  # setenv baked fewer_turns=True into the cached Settings; the fallback must not read it
    (off,) = _ctx(tmp_path / "off", chair_plan, settings)[0].baseline_tasks(_ctx(tmp_path / "off", chair_plan, settings)[1])
    assert "Turn discipline" not in off.prompt
    # single-shot has no tools to discipline
    monkeypatch.setenv(FEWER_TURNS_ENV, "1")
    t2, c2 = _ctx(tmp_path / "ss", chair_plan, settings, agent_id="single-shot:fake:m")
    assert "Turn discipline" not in t2.baseline_tasks(c2)[0].prompt


def test_scoped_baseline_and_assembly_prompts_carry_it_too(tmp_path, settings, monkeypatch):
    from .test_generation_depth import big_plan

    monkeypatch.delenv("C3D_SCOPED_PARTS", raising=False)
    monkeypatch.setenv(FEWER_TURNS_ENV, "1")
    track, ctx = _ctx(tmp_path, big_plan(), settings, language=Language.THREEJS)
    tasks = track.baseline_tasks(ctx)
    assert len(tasks) >= 3
    assert all("## Turn discipline" in t.prompt for t in tasks)
    assemble = tasks[-1].prompt
    assert "Read the CONNECTIVITY and CONTRACT sections of the `build` result" in assemble
    assert "Run `check_connectivity` and `check_contract`" not in assemble
    monkeypatch.delenv(FEWER_TURNS_ENV)
    get_settings.cache_clear()  # setenv baked fewer_turns=True into the cached Settings; the fallback must not read it
    t2, c2 = _ctx(tmp_path / "off", big_plan(), settings, language=Language.THREEJS)
    off = t2.baseline_tasks(c2)[-1].prompt
    assert "Run `check_connectivity` and `check_contract`" in off and "Turn discipline" not in off
