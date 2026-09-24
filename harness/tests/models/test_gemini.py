"""GeminiModel with a fake google-genai client (offline)."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from codeverse3d.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ImagePart,
    TextPart,
)
from codeverse3d.models.base import ModelError
from codeverse3d.models.gemini import (
    GeminiModel,
    classify_exception,
    to_contents,
)
from codeverse3d.models.retry import KeyPool, failure_outcome
from codeverse3d.tracks.planner import _truncated

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

def text_response(
    text: str, *, finish: str = "STOP", thoughts: int = 3
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        model_version="gemini-3.7-flash",
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
                finish_reason=finish,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100,
            candidates_token_count=20,
            thoughts_token_count=thoughts,
            cached_content_token_count=40,
        ),
    )


class FakeModels:
    def __init__(self, script: list[Any], key: str, log: list[dict]):
        self.script, self.key, self.log = script, key, log

    def generate_content(self, *, model: str, contents, config):
        self.log.append({"key": self.key, "model": model, "contents": contents, "config": config})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def generate_content_stream(self, *, model: str, contents, config):
        yield self.generate_content(model=model, contents=contents, config=config)


class FakeClient:
    def __init__(self, script, key, log):
        self.models = FakeModels(script, key, log)


def make_model(script: list[Any], keys=("k1", "k2", "k3"), pool: KeyPool | None = None, **kw):
    """Shared script across keys (each call pops the next item); log records which key was used."""
    log: list[dict] = []
    pool = pool or KeyPool(list(keys), cooldown_s=30)
    m = GeminiModel(
        "gemini-3.7-flash",
        pool=pool,
        sleep=lambda s: None,
        client_factory=lambda key: FakeClient(script, key, log),
        **kw,
    )
    return m, log, pool


def _api_error(code: int, message: str, status: str) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": message, "status": status}})


SUSPENDED = _api_error(
    403, "PERMISSION_DENIED: Consumer 'api_key:xxx' has been suspended.", "PERMISSION_DENIED"
)
THROTTLED = genai_errors.APIError(
    429,
    {
        "error": {
            "message": "quota",
            "status": "RESOURCE_EXHAUSTED",
            "details": [{"retryDelay": "7s"}],
        }
    },
)


def test_429_rotates_keys_and_honours_retry_delay():
    m, log, pool = make_model([THROTTLED, THROTTLED, text_response("third time lucky")])
    assert m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "third time lucky"
    assert [e["key"] for e in log] == ["k1", "k2", "k3"]
    st = pool.stats()
    assert st["429"] == 2 and st["ok"] == 1 and st["n_cooling"] == 2
    assert 0 < {k["key"]: k["cooldown_s"] for k in st["keys"]}["…k1"] <= 7.0


def test_text_usage_cost_and_raw():
    m, log, _ = make_model([text_response("hello")])
    r = m.generate(
        ChatRequest(
            messages=[ChatMessage.user("hi")], system="sys", thinking="medium", temperature=0.2
        )
    )
    assert r.text == "hello" and r.finish_reason == "STOP" and r.parsed is None
    u = r.usage
    assert (u.input_tokens, u.output_tokens, u.thoughts_tokens, u.cached_tokens) == (100, 20, 3, 40)
    assert u.cost_usd > 0 and u.backend == "gemini" and u.model == "gemini-3.7-flash"
    cfg = log[0]["config"]
    assert cfg.system_instruction == "sys" and cfg.temperature == 0.2
    assert cfg.thinking_config.thinking_budget == 4096
    assert r.raw["key"] == "…k1"


def test_api_key_invalid_400_is_treated_as_dead_key():
    bad = _api_error(400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT")
    m, log, pool = make_model([bad, text_response("ok")], keys=("k1", "k2"))
    assert m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "ok"
    assert [event["key"] for event in log] == ["k1", "k2"]
    assert pool.stats()["dead"] == 1
    assert failure_outcome(classify_exception(bad)) == "dead"
    assert failure_outcome(classify_exception(SUSPENDED)) == "dead"
    assert failure_outcome(classify_exception(THROTTLED)) == "429"
    assert failure_outcome(ModelError("bad json", retryable=True)) == "skip"  # not the key's fault: no health nudge


def test_thinking_rejected_falls_back_without_thinking_config():
    err = genai_errors.APIError(
        400,
        {
            "error": {
                "message": "Thinking level is not supported for this model",
                "status": "INVALID_ARGUMENT",
            }
        },
    )
    m, log, _ = make_model([err, text_response("ok")])
    r = m.generate(ChatRequest(messages=[ChatMessage.user("x")], thinking="high"))
    assert r.text == "ok"
    assert log[0]["config"].thinking_config is not None and log[1]["config"].thinking_config is None
    assert any("thinking" in w for w in r.raw["warnings"])
    # sticky for the next call
    log.clear()
    m._client_factory = lambda key: FakeClient([text_response("again")], key, log)
    m.generate(ChatRequest(messages=[ChatMessage.user("y")]))
    assert log[0]["config"].thinking_config is None


def test_images_inline_in_order(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(PNG_1PX)
    m, log, _ = make_model([text_response("red")])
    msg = ChatMessage.user(
        "look",
        images=[
            ImagePart(path=str(p), label="view1"),
            ImagePart(data_b64=base64.b64encode(PNG_1PX).decode(), mime="image/png"),
        ],
    )
    m.generate(ChatRequest(messages=[msg]))
    parts = log[0]["contents"][0].parts
    kinds = [("img" if pt.inline_data else "txt") for pt in parts]
    assert kinds == ["txt", "txt", "img", "img"]
    assert parts[2].inline_data.data == PNG_1PX and parts[2].inline_data.mime_type == "image/png"


def test_missing_image_is_loud():
    m, _, _ = make_model([text_response("x")])
    with pytest.raises(ModelError):
        m.generate(
            ChatRequest(
                messages=[ChatMessage.user("look", images=[ImagePart(path="/nonexistent.png")])]
            )
        )


def test_empty_candidates_and_blocked_prompt():
    empty = types.GenerateContentResponse(candidates=[], usage_metadata=None)
    m, log, _ = make_model([empty, text_response("ok")])
    assert m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "ok"
    blocked = types.GenerateContentResponse(
        candidates=[],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY"),
    )
    m2, log2, _ = make_model([blocked, text_response("never")])
    with pytest.raises(ModelError) as ei:
        m2.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert not ei.value.retryable and len(log2) == 1


def test_classify_exception_transport():
    import httpx

    assert classify_exception(httpx.ReadTimeout("t")).retryable
    assert classify_exception(httpx.ConnectError("c")).retryable
    assert not classify_exception(ValueError("v")).retryable
    # one status table with the SDK adapters (N76): a 409 is retried and is an outage, a 400 is neither
    from codeverse3d.tracks.generation import is_model_outage

    for code, retry in ((409, True), (408, True), (429, True), (529, True), (400, False), (404, False)):
        err = classify_exception(_api_error(code, "x", "X"))
        assert (err.retryable, is_model_outage(err)) == (retry, retry), code


def test_to_contents_merges_same_role_and_requires_content():
    msgs = [
        ChatMessage.user("a"),
        ChatMessage(role="user", parts=[TextPart(text="b")]),
        ChatMessage.assistant("c"),
    ]
    c = to_contents(msgs)
    assert [x.role for x in c] == ["user", "model"] and len(c[0].parts) == 2
    with pytest.raises(ModelError):
        to_contents([ChatMessage(role="user", parts=[])])


def test_no_keys_is_loud():
    with pytest.raises(ModelError):
        GeminiModel("gemini-3.7-flash", keys=[])


def test_max_token_failures_are_not_retried():
    cases = (
        (
            text_response('{"a": [1, 2', finish="MAX_TOKENS"),
            ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"}),
            "max_output_tokens",
        ),
        (
            text_response("", finish="MAX_TOKENS", thoughts=49),
            ChatRequest(messages=[ChatMessage.user("x")], max_output_tokens=50),
            "thinking",
        ),
    )
    for response, request, message in cases:
        model, log, _ = make_model([response, text_response("never")])
        with pytest.raises(ModelError) as error:
            model.generate(request)
        assert not error.value.retryable and len(log) == 1
        assert message in str(error.value)


def test_a_charged_but_invalid_reply_carries_its_usage_on_the_error():
    """A failed structured reply preserves its billed token usage."""
    m, _log, _ = make_model([text_response("nope")] * 6)
    with pytest.raises(ModelError) as ei:
        m.generate(
            ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"})
        )
    assert ei.value.usage.input_tokens == 100 and ei.value.usage.output_tokens == 20
    assert ei.value.usage.cost_usd > 0


def test_max_output_tokens_eaten_by_thinking_carries_the_thought_tokens():
    """A MAX_TOKENS failure reports the thinking tokens it consumed."""
    resp = types.GenerateContentResponse(
        model_version="gemini-3.7-flash",
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[]), finish_reason="MAX_TOKENS"
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1000, candidates_token_count=0, thoughts_token_count=8000
        ),
    )
    m, _log, _ = make_model([resp])
    with pytest.raises(ModelError) as e:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert "exhausted by thinking" in str(e.value)
    assert _truncated(e.value), "the planner's grow-the-budget retry must fire on exactly this case"
    assert e.value.usage.thoughts_tokens == 8000 and e.value.usage.cost_usd > 0


# --------------------------------------------------------------------- streaming
def test_merge_stream_chunks_empty_stream_is_retryable():
    from codeverse3d.models.gemini import _merge_stream_chunks

    with pytest.raises(ModelError) as e:
        _merge_stream_chunks([])
    assert e.value.retryable


def test_drain_stream_cuts_a_stream_past_its_attempt_budget():
    """The attempt deadline also bounds a healthy but endless stream."""
    from codeverse3d.models.gemini import _drain_stream

    ticks = iter([1.0, 10.0, 20.0])
    chunk = types.GenerateContentResponse(candidates=[])
    with pytest.raises(ModelError) as e:
        _drain_stream(iter([chunk, chunk, chunk]), deadline=5.0, clock=lambda: next(ticks))
    assert e.value.retryable and "attempt budget" in str(e.value)


@pytest.mark.parametrize(("raw", "streamed"), [(None, True), ("off", False)])  # spellings: test_config_env_aliases
def test_streaming_toggle(monkeypatch, switch, raw, streamed):
    """``C3D_STREAM=off`` was silently ignored until 2026-09-22: only "0" was read."""
    switch("C3D_STREAM", raw)
    calls: list[int] = []
    real = FakeModels.generate_content_stream
    monkeypatch.setattr(FakeModels, "generate_content_stream", lambda self, **kw: (calls.append(1), real(self, **kw))[1])
    m, _log, _ = make_model([text_response("hi")])
    assert m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "hi"
    assert bool(calls) is streamed


@pytest.mark.parametrize(("raw", "bound"), [(None, True), ("off", False)])  # spellings: test_config_env_aliases
def test_ipv4_transport_toggle(switch, raw, bound):
    """``C3D_IPV4=off`` was silently ignored until 2026-09-22: only "0" restored dual-stack."""
    import httpx

    from codeverse3d.models.gemini import _ipv4_client_args

    switch("C3D_IPV4", raw)
    assert isinstance(_ipv4_client_args().get("transport"), httpx.HTTPTransport) is bound


def test_merge_stream_chunks_concatenates_text_and_keeps_final_usage():
    """Stream merging concatenates text parts in order; usage comes from the final chunk."""
    from codeverse3d.models.gemini import _merge_stream_chunks, extract_candidate

    c1 = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text="hel")])
            )
        ]
    )
    c2 = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text="lo")]),
                finish_reason="STOP",
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=9, candidates_token_count=4
        ),
    )
    merged = _merge_stream_chunks([c1, c2])
    text, finish = extract_candidate(merged)
    assert text == "hello" and finish == "STOP"
    assert merged.usage_metadata.prompt_token_count == 9
