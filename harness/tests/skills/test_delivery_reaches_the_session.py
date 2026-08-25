"""A routed bundle only exists if a GENERATING session is told about it.

The read loop measured cv3d-scene-composition / -lighting / -motion at 0 opens out of 3
listings each and read it as a wording problem.  It is not: the scene track does all of
its baseline generation in ``prepare()`` — the env, zones and compose stages call
``tracks.generation.generate`` directly — while ``skills_hook.attach_for_round`` is only
reached from ``steps.run_round``.  ``SceneTrack.baseline_tasks`` then returns ``[]``, so
round 0 attaches the bundles to a round that has no generation task to consume them.
That is exactly "listed 3, opened 0", and no rewrite of a SKILL.md can change it.

These tests pin the shape of the gap so it cannot widen quietly, and so that the day the
scene stages are routed through the hook the strict xfail turns the suite red and forces
this file, docs/SKILLS_LEDGER.md and the bundles' evidence labels to be revisited together.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("mod", ["steps.py", "repair.py"])
def test_the_round_and_repair_paths_deliver_skills(mod: str) -> None:
    assert _mentions_hook(mod), f"{mod} drives an agent without going through skills_hook"


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN GAP, measured 2026-08-25: the scene track generates its whole baseline in "
    "prepare() (env/zones/compose stages -> generate()), which never reaches "
    "skills_hook.attach_for_round.  The four scene-routed bundles were listed 3 times each "
    "and opened 0 times because of this, and cv3d-scene-composition's target metric is read "
    "on the FIRST gated round, which those stages produce.  Fix: call attach_for_round + "
    "with_inlined_skill in SceneTrack._env_stage / _zone_task / _compose, as steps.run_round "
    "does.  When this XPASSes, re-measure the scene bundles' read rate and effect."))
def test_the_scene_stages_deliver_skills() -> None:
    assert _mentions_hook("scene.py"), (
        "scene.py drives three generating sessions and none of them is offered the routed "
        "bundles: the scene skills cannot affect a scene baseline as delivered")


def test_round_zero_of_a_scene_run_has_no_generation_task() -> None:
    """The other half of the same fact, and the reason the bundles look 'listed'.

    ``attach_for_round`` still fires for round 0, so the round record names the routed
    bundles — while ``baseline_tasks`` hands ``run_generation_tasks`` an empty list, so no
    session is ever created to read them.  A read-rate denominator built from listings
    therefore counts sessions that never existed.
    """
    src = (TRACKS / "scene.py").read_text()
    assert "def baseline_tasks" in src
    body = src.split("def baseline_tasks", 1)[1].split("def ", 1)[0]
    assert "return []" in body, "scene round 0 now generates; the listing-vs-read gap may be gone"
