"""A truncated plan is retried with low thinking and a compact-answer note, not just more tokens."""

from __future__ import annotations

from codeverse3d.contracts.common import Language
from codeverse3d.contracts.plan import StaticPlan
from codeverse3d.proc import EventLog
from codeverse3d.tracks.planner import TRUNCATION_NOTE, plan
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import FakeChatModel, FakeRuntime
from tests.orchestrator_tracks.test_generation_planner_repair import _valid_plan_dict


def test_truncation_retries_compact_with_low_thinking(tmp_ws):
    calls = {"n": 0}

    def respond(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model stopped: finish_reason=MAX_TOKENS")
        return _valid_plan_dict()

    model = FakeChatModel(respond)
    events = EventLog(tmp_ws.events_path)
    p = plan(make_spec(), "fake:planner", StaticPlan, tmp_ws, model=model, events=events,
             runtime=FakeRuntime(Language.THREEJS))
    assert p.object_name == "DiningChair" and len(model.requests) == 2
    first, second = model.requests
    assert first.thinking == "medium" and second.thinking == "off"
    assert second.max_output_tokens > first.max_output_tokens
    assert second.max_wait_s > first.max_wait_s  # the retry may run longer than the size rule alone allows
    assert second.messages[-1].text.endswith(TRUNCATION_NOTE.format(tokens=second.max_output_tokens))
    assert second.messages[-1].text.startswith(first.messages[-1].text)  # the original request is kept
    assert len(second.messages) == len(first.messages)  # no dangling user turn
    kinds = [e["event"] for e in events.read()]
    assert "plan.truncated" in kinds and "plan.done" in kinds
