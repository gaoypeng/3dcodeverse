"""Fixtures shared by orchestrator/tracks tests."""

from __future__ import annotations

import contextlib

import pytest

from codeverse.config import Settings
from codeverse.contracts.common import Backends, Budget, Language, Track
from codeverse.contracts.plan import AcceptanceItem, BBox, PartPlan, StaticPlan
from codeverse.contracts.spec import Constraints, Spec


@pytest.fixture(autouse=True)
def no_brief_expansion(monkeypatch) -> None:
    """Turn the planner's optional brief-expansion call OFF for this package by default.

    A fake planner model answers one canned plan per request; the extra brief call would
    eat it and every ``FakeChatModel(lambda req: answers.pop(0))`` in here would go one
    answer out of step.  The tests that exercise the brief set ``CV3D_PLAN_BRIEF=on``
    themselves (``test_planner_depth.py``).

    Also clear the global Settings cache around each test: ``get_settings()`` is an
    ``lru_cache`` singleton that snapshots ``CV3D_*`` env vars at first construction, so
    whichever test happens to touch it first bakes ITS monkeypatched env into every later
    test in the worker — ``test_fewer_turns`` failed alone and passed in file order for
    exactly this reason."""
    from codeverse.config import get_settings

    monkeypatch.setenv("CV3D_PLAN_BRIEF", "off")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")


@pytest.fixture
def chair_plan() -> StaticPlan:
    return StaticPlan(
        object_name="DiningChair", summary="Mid-century oak dining chair, 0.45 x 0.50 x 0.82 m.",
        overall_bbox=BBox(center=(0, 0.41, 0), extents=(0.45, 0.82, 0.50)), style_notes="Danish, tapered legs",
        parts=[
            PartPlan(name="Seat", role="seat", description="40 mm oak board", bbox=BBox(center=(0, 0.43, 0), extents=(0.42, 0.04, 0.40))),
            PartPlan(name="FrontLeg", role="front leg", description="tapered", bbox=BBox(center=(0.17, 0.205, 0.16), extents=(0.035, 0.41, 0.035)),
                     attach_to="Seat", symmetry="mirror_x", instances=2),
            PartPlan(name="BackLeg", role="back leg", description="tapered, continues into backrest", bbox=BBox(center=(0.17, 0.41, -0.16), extents=(0.035, 0.82, 0.035)),
                     attach_to="Seat", symmetry="mirror_x", instances=2),
            PartPlan(name="Backrest", role="backrest", description="curved slat", bbox=BBox(center=(0, 0.72, -0.17), extents=(0.40, 0.12, 0.03)), attach_to="BackLeg"),
            PartPlan(name="Armrest", role="armrest", description="flat", bbox=BBox(center=(0.2, 0.65, 0), extents=(0.04, 0.03, 0.35)), attach_to="BackLeg",
                     symmetry="mirror_x", instances=2),
        ],
        acceptance=[AcceptanceItem(id="a1", text="Seat top at 0.45 m", how="measure"), AcceptanceItem(id="a2", text="Four legs touch the ground", how="visual")],
    )


def make_spec(track: Track = Track.STATIC_OBJECT, language: Language = Language.THREEJS, *, generator: str = "fake:fake-model",
              max_rounds: int = 3, max_minutes: float = 10.0, prompt: str = "a mid-century wooden dining chair", **kw) -> Spec:
    return Spec(id="t1", track=track, language=language, prompt=prompt,
                constraints=Constraints(dimensions_m={"height": 0.82}, must_have=["armrests"]),
                budget=Budget(max_rounds=max_rounds, max_minutes=max_minutes, max_repair_attempts=2),
                backends=Backends(planner="fake:planner", generator=generator, judge="fake:judge"), **kw)


@pytest.fixture
def spec() -> Spec:
    return make_spec()


#: minutes the fakes have "spent".  A fake answers instantly, so a scenario that needs a
#: run to stop mid-way gives its fakes a duration: FakeAgent(..., minutes=8).
FAKE_CLOCK = {"minutes": 0.0}


@contextlib.contextmanager
def fake_clock():
    """Point BudgetGuard's wall clock at FAKE_CLOCK for the duration of a test."""
    from codeverse.orchestrator import BudgetGuard

    real = BudgetGuard.elapsed_minutes
    FAKE_CLOCK["minutes"] = 0.0
    BudgetGuard.elapsed_minutes = lambda self: FAKE_CLOCK["minutes"]   # type: ignore[method-assign]
    try:
        yield FAKE_CLOCK
    finally:
        BudgetGuard.elapsed_minutes = real                             # type: ignore[method-assign]
