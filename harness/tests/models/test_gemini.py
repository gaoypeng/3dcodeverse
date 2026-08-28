"""GeminiModel with a fake google-genai client (offline)."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from codeverse.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ImagePart,
    TextPart,
    ToolResultPart,
    ToolSpec,
)
from codeverse.contracts.plan import StaticPlan
from codeverse.models.base import ModelError
from codeverse.models.gemini import (
    SIGNATURES,
    GeminiModel,
    build_config,
    classify_exception,
    failure_outcome,
    to_contents,
)
from codeverse.models.retry import MAX_WAIT_S, KeyPool

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

#: the one tool spec every tool-call test uses.
MEASURE_TOOL = ToolSpec(name="measure", description="m",
                        parameters={"type": "object", "properties": {"part": {"type": "string"}}})


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


def call_response(
    name: str, args: dict, sig: bytes | None = b"sig-bytes"
) -> types.GenerateContentResponse:
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    part.thought_signature = sig
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=[part]), finish_reason="STOP")
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5
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


def test_read_timeout_comes_from_settings(monkeypatch):
    """CV3D_MODEL_TIMEOUT_S raises the per-attempt HTTP read timeout (degraded-provider escape hatch)."""
    from codeverse.config import Settings, get_settings
    from codeverse.models.gemini import GeminiModel

    monkeypatch.setenv("GEMINI_API_KEYS", "k1")
    get_settings.cache_clear()
    assert GeminiModel("gemini-3.7-flash").timeout_s == Settings().model_timeout_s
    monkeypatch.setenv("CV3D_MODEL_TIMEOUT_S", "900")
    get_settings.cache_clear()
    assert GeminiModel("gemini-3.7-flash").timeout_s == 900.0
    assert GeminiModel("gemini-3.7-flash", timeout_s=42.0).timeout_s == 42.0
    get_settings.cache_clear()


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


def test_structured_output_parsed_and_schema_sanitised():
    payload = {
        "object_name": "Stool",
        "summary": "s",
        "overall_bbox": {"center": [0, 0, 0.25], "extents": [0.4, 0.4, 0.5]},
        "parts": [
            {
                "name": "Seat",
                "role": "r",
                "description": "d",
                "bbox": {"center": [0, 0, 0.45], "extents": [0.4, 0.4, 0.05]},
            }
        ],
    }
    m, log, _ = make_model([text_response("```json\n" + json.dumps(payload) + "\n```")])
    r = m.generate(
        ChatRequest(
            messages=[ChatMessage.user("plan")], response_schema=StaticPlan.model_json_schema()
        )
    )
    assert StaticPlan.model_validate(r.parsed).object_name == "Stool"
    cfg = log[0]["config"]
    assert cfg.response_mime_type == "application/json"
    assert "$defs" not in json.dumps(
        cfg.response_schema
        if isinstance(cfg.response_schema, dict)
        else cfg.response_schema.model_dump()
    )


def test_bad_json_is_retried_then_raises():
    m, log, _ = make_model([text_response("not json"), text_response('{"ok": true}')])
    r = m.generate(
        ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"})
    )
    assert r.parsed == {"ok": True} and len(log) == 2
    m2, _, _ = make_model([text_response("nope")] * 6)
    with pytest.raises(ModelError) as ei:
        m2.generate(
            ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"})
        )
    assert ei.value.retryable


def test_429_rotates_keys_and_cools_down():
    err = genai_errors.APIError(
        429,
        {
            "error": {
                "message": "quota",
                "status": "RESOURCE_EXHAUSTED",
                "details": [{"retryDelay": "7s"}],
            }
        },
    )
    m, log, pool = make_model([err, err, text_response("third time lucky")])
    r = m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert r.text == "third time lucky"
    assert [e["key"] for e in log] == ["k1", "k2", "k3"]
    st = pool.stats()
    assert st["429"] == 2 and st["ok"] == 1 and st["n_cooling"] == 2
    cooling = {k["key"]: k["cooldown_s"] for k in st["keys"]}
    assert 0 < cooling["…k1"] <= 7.0  # retryDelay honoured


def _api_error(code: int, message: str, status: str) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": message, "status": status}})


class _KeyedClient:
    """Client whose behaviour depends on the key: ``fail[key]`` raises, others answer."""

    def __init__(self, key: str, fail: dict[str, BaseException], log: list[dict]):
        self.key, self.fail, self.log = key, fail, log
        self.models = self

    def generate_content(self, *, model: str, contents, config):
        self.log.append({"key": self.key})
        if self.key in self.fail:
            raise self.fail[self.key]
        return text_response(f"ok from {self.key}")

    def generate_content_stream(self, *, model: str, contents, config):
        yield self.generate_content(model=model, contents=contents, config=config)


def _keyed_model(
    keys: list[str], fail: dict[str, BaseException], pool: KeyPool | None = None, **kw
):
    log: list[dict] = []
    pool = pool or KeyPool(keys, cooldown_s=30)
    m = GeminiModel(
        "gemini-3.7-flash",
        pool=pool,
        sleep=lambda s: None,
        client_factory=lambda key: _KeyedClient(key, fail, log),
        **kw,
    )
    return m, log, pool


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


def test_429_rotation_does_not_consume_the_retry_budget():
    # 8 throttled keys > max_attempts=6, yet fresh keys remain → must still succeed
    keys = [f"k{i}" for i in range(1, 23)]
    m, log, pool = _keyed_model(keys, {k: THROTTLED for k in keys[:8]}, max_attempts=6)
    r = m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert r.text == "ok from k9" and [e["key"] for e in log] == keys[:9]
    assert pool.stats()["n_cooling"] == 8


def test_429_on_every_key_waits_for_cooldown_then_counts_against_budget():
    clock = {"t": 1000.0}
    pool = KeyPool(
        ["k1", "k2", "k3"],
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    keys = ["k1", "k2", "k3"]
    m, log, _ = _keyed_model(keys, {k: THROTTLED for k in keys}, pool=pool, max_attempts=3)
    with pytest.raises(ModelError) as ei:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert ei.value.retryable and ei.value.status == 429
    # 2 free rotations + 3 budgeted attempts; the pool waited out the cooldowns in between
    assert len(log) == 5
    # …but no single wait exceeded the house limit, even though the provider's
    # own retryDelay in THROTTLED asks for 7 s (MAX_WAIT_S caps it).
    assert 1000.0 < clock["t"] <= 1000.0 + 5 * MAX_WAIT_S


def test_no_single_wait_exceeds_the_house_limit():
    """Every backoff path — 429 cooldown, provider retryDelay, 5xx, capacity
    storm — sleeps at most MAX_WAIT_S at a time (owner rule, 2026-08-24)."""
    naps: list[float] = []
    pool = KeyPool(["k1", "k2"], sleep=naps.append)
    m, _log, _ = _keyed_model(["k1", "k2"], {k: THROTTLED for k in ("k1", "k2")}, pool=pool, max_attempts=3)
    m._sleep = naps.append
    with pytest.raises(ModelError):
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert naps, "expected the throttled path to sleep at least once"
    assert max(naps) <= MAX_WAIT_S, f"a wait exceeded {MAX_WAIT_S}s: {sorted(naps)[-3:]}"


def test_dead_key_is_rotated_past_and_benched():
    m, log, pool = _keyed_model(["k1", "k2", "k3"], {"k1": SUSPENDED})
    r = m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert r.text == "ok from k2" and [e["key"] for e in log] == ["k1", "k2"]
    st = {k["key"]: k for k in pool.stats()["keys"]}
    assert st["…k1"]["dead"] == 1 and st["…k1"]["cooldown_s"] > 600 and pool.stats()["n_dead"] == 1
    # the dead key never comes back round-robin while benched
    log.clear()
    for _ in range(10):
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert {e["key"] for e in log} == {"k2", "k3"}


def test_api_key_invalid_400_is_treated_as_dead_key():
    bad = _api_error(400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT")
    m, log, pool = _keyed_model(["k1", "k2"], {"k1": bad})
    assert m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "ok from k2"
    assert pool.stats()["dead"] == 1
    assert failure_outcome(classify_exception(bad)) == "dead"
    assert failure_outcome(classify_exception(SUSPENDED)) == "dead"
    assert failure_outcome(classify_exception(THROTTLED)) == "429"
    assert failure_outcome(ModelError("bad json", retryable=True)) == "ok"


def test_every_key_dead_raises_without_benching():
    keys = ["k1", "k2", "k3"]
    m, log, pool = _keyed_model(keys, {k: SUSPENDED for k in keys})
    with pytest.raises(ModelError) as ei:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert not ei.value.retryable and ei.value.status == 403
    assert [e["key"] for e in log] == keys  # each tried once, no sleeps, no budget burnt
    # the request (not the keys) is suspect: nothing benched, next call is not blocked
    assert pool.stats()["dead"] == 0 and pool.stats()["n_cooling"] == 0
    assert pool.acquire(timeout_s=0.0) in keys


def test_5xx_retries_then_gives_up_with_retryable_error():
    # 500 = plain 5xx (no storm budget): the classic 6-attempt backoff applies
    err = genai_errors.APIError(500, {"error": {"message": "internal", "status": "INTERNAL"}})
    slept: list[float] = []
    m, log, _ = make_model([err] * 6)
    m._sleep = slept.append
    with pytest.raises(ModelError) as ei:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert ei.value.retryable and ei.value.status == 500
    assert len(log) == 6 and len(slept) == 5 and slept[1] > slept[0] / 2


def test_503_storm_survives_beyond_the_attempt_budget():
    # 503 = capacity storm: its own patience budget on top of max_attempts
    err = genai_errors.APIError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})
    m, log, _ = make_model([err] * 8 + [text_response("recovered")])
    m._sleep = lambda d: None
    resp = m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert resp.text == "recovered" and len(log) == 9


def test_400_is_not_retried():
    err = genai_errors.APIError(
        400, {"error": {"message": "Invalid argument", "status": "INVALID_ARGUMENT"}}
    )
    m, log, _ = make_model([err, text_response("never")])
    with pytest.raises(ModelError) as ei:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert not ei.value.retryable and len(log) == 1


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
    m.pool  # noqa: B018
    log.clear()
    m._client_factory = lambda key: FakeClient([text_response("again")], key, log)
    m.generate(ChatRequest(messages=[ChatMessage.user("y")]))
    assert log[0]["config"].thinking_config is None


def test_tool_call_roundtrip_and_signature_cache():
    tool = MEASURE_TOOL
    m, log, _ = make_model([call_response("measure", {"part": "all"}), text_response("45 cm")])
    req = ChatRequest(messages=[ChatMessage.user("measure it")], tools=[tool])
    r = m.generate(req)
    assert (
        r.tool_calls
        and r.tool_calls[0].name == "measure"
        and r.tool_calls[0].arguments == {"part": "all"}
    )
    assert r.usage.tool_calls == 1 and r.text == ""
    cfg = log[0]["config"]
    assert cfg.tools[0].function_declarations[0].name == "measure"
    assert cfg.response_mime_type is None
    call = r.tool_calls[0]
    assert SIGNATURES.get(call.id) == b"sig-bytes"
    msgs = [
        *req.messages,
        ChatMessage(role="assistant", parts=[call]),
        ChatMessage(
            role="tool",
            parts=[
                ToolResultPart(call_id=call.id, name="measure", content=json.dumps({"h": 0.45}))
            ],
        ),
    ]
    r2 = m.generate(ChatRequest(messages=msgs, tools=[tool]))
    assert r2.text == "45 cm" and not r2.tool_calls
    contents = log[1]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    fc_part = contents[1].parts[0]
    assert fc_part.function_call.name == "measure" and fc_part.thought_signature == b"sig-bytes"
    fr = contents[2].parts[0].function_response
    assert fr.name == "measure" and fr.response == {"h": 0.45}


def test_tools_plus_schema_warns_and_drops_json_mode():
    tool = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})
    m, log, _ = make_model([text_response('{"a": 1}')])
    r = m.generate(
        ChatRequest(
            messages=[ChatMessage.user("x")], tools=[tool], response_schema={"type": "object"}
        )
    )
    assert r.parsed == {"a": 1}
    assert log[0]["config"].response_mime_type is None
    assert any("response_schema ignored" in w for w in r.raw["warnings"])


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


def test_build_config_thinking_off_budget_zero():
    cfg = build_config(
        ChatRequest(messages=[ChatMessage.user("x")], thinking="off"), timeout_ms=1000
    )
    assert cfg.thinking_config.thinking_budget == 0
    cfg = build_config(
        ChatRequest(messages=[ChatMessage.user("x")]), timeout_ms=1000, use_thinking=False
    )
    assert cfg.thinking_config is None


def test_no_keys_is_loud():
    with pytest.raises(ModelError):
        GeminiModel("gemini-3.7-flash", keys=[])


def test_truncated_json_is_not_retried():
    m, log, _ = make_model(
        [text_response('{"a": [1, 2', finish="MAX_TOKENS"), text_response("never")]
    )
    with pytest.raises(ModelError) as ei:
        m.generate(
            ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"})
        )
    assert not ei.value.retryable and len(log) == 1 and "max_output_tokens" in str(ei.value)


def test_empty_max_tokens_is_not_retried():
    m, log, _ = make_model(
        [text_response("", finish="MAX_TOKENS", thoughts=49), text_response("never")]
    )
    with pytest.raises(ModelError) as ei:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_output_tokens=50))
    assert not ei.value.retryable and len(log) == 1 and "thinking" in str(ei.value)
def test_a_charged_but_invalid_reply_carries_its_usage_on_the_error():
    """``ModelError.usage``: what the provider billed for the failed call — a
    bad-JSON reply is charged like a success (mirrors tracks.planner.PlanningError),
    so the ledger's error row and the pool's TPM accounting can see real money."""
    m, _log, _ = make_model([text_response("nope")] * 6)
    with pytest.raises(ModelError) as ei:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"}))
    assert ei.value.usage.input_tokens == 100 and ei.value.usage.output_tokens == 20
    assert ei.value.usage.cost_usd > 0


def _resp_with_calls(parts: list[types.Part]) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=parts), finish_reason="STOP")
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5
        ),
    )


def test_synthetic_call_ids_are_never_echoed_to_the_provider():
    """When the provider sent NO id we mint one for local pairing — and it must
    never travel back to Gemini (an id the provider did not issue)."""
    from codeverse.models.gemini import is_synthetic_call_id

    tool = ToolSpec(name="measure", description="m", parameters={"type": "object", "properties": {}})
    m, log, _ = make_model([call_response("measure", {"part": "all"}), text_response("ok")])
    req = ChatRequest(messages=[ChatMessage.user("x")], tools=[tool])
    r = m.generate(req)
    call = r.tool_calls[0]
    assert is_synthetic_call_id(call.id)
    msgs = [
        *req.messages,
        ChatMessage(role="assistant", parts=[call]),
        ChatMessage(role="tool", parts=[ToolResultPart(call_id=call.id, name="measure", content="{}")]),
    ]
    m.generate(ChatRequest(messages=msgs, tools=[tool]))
    contents = log[1]["contents"]
    assert contents[1].parts[0].function_call.id is None, "an invented id must not reach Gemini"
    assert contents[2].parts[0].function_response.id is None


def test_two_parallel_same_name_calls_keep_their_provider_ids():
    """Two calls to the SAME tool in one turn: a GENUINE provider id is echoed verbatim on
    BOTH the function_call replay and the function_response (both used to drop it), and only
    those ids pair a response with its call — each keeping its own thought_signature."""
    from codeverse.models.gemini import is_synthetic_call_id

    parts = []
    for cid, target in (("id-a", "seat"), ("id-b", "leg")):
        p = types.Part(function_call=types.FunctionCall(name="measure", args={"part": target}, id=cid))
        p.thought_signature = f"sig-{cid}".encode()
        parts.append(p)
    tool = MEASURE_TOOL
    m, log, _ = make_model([_resp_with_calls(parts), text_response("done")])
    req = ChatRequest(messages=[ChatMessage.user("measure both")], tools=[tool])
    r = m.generate(req)
    a, b = r.tool_calls
    assert (a.id, b.id) == ("id-a", "id-b") and a.name == b.name == "measure"
    assert not is_synthetic_call_id(a.id), "a provider id must not be mistaken for a minted one"
    assert SIGNATURES.get("id-a") == b"sig-id-a" and SIGNATURES.get("id-b") == b"sig-id-b"
    msgs = [
        *req.messages,
        ChatMessage(role="assistant", parts=[a, b]),
        ChatMessage(
            role="tool",
            parts=[  # results deliberately out of order: the ids do the pairing
                ToolResultPart(call_id=b.id, name="measure", content=json.dumps({"part": "leg"})),
                ToolResultPart(call_id=a.id, name="measure", content=json.dumps({"part": "seat"})),
            ],
        ),
    ]
    m.generate(ChatRequest(messages=msgs, tools=[tool]))
    model_parts = log[1]["contents"][1].parts
    assert [fp.function_call.id for fp in model_parts] == ["id-a", "id-b"]
    assert [fp.thought_signature for fp in model_parts] == [b"sig-id-a", b"sig-id-b"]
    responses = log[1]["contents"][2].parts
    assert [(fp.function_response.id, fp.function_response.response["part"]) for fp in responses] == [
        ("id-b", "leg"),
        ("id-a", "seat"),
    ]


def test_max_output_tokens_eaten_by_thinking_carries_the_thought_tokens():
    """The most expensive failure on Gemini 3: the reply is empty because thinking ate
    the whole budget, and every thought token was billed.  ``extract_candidate`` read
    ``thoughts_token_count`` only to put it in the message and dropped the usage."""
    resp = types.GenerateContentResponse(
        model_version="gemini-3.7-flash",
        candidates=[types.Candidate(content=types.Content(role="model", parts=[]),
                                    finish_reason="MAX_TOKENS")],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1000, candidates_token_count=0, thoughts_token_count=8000),
    )
    m, _log, _ = make_model([resp])
    with pytest.raises(ModelError) as e:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert "exhausted by thinking" in str(e.value)
    assert e.value.usage.thoughts_tokens == 8000 and e.value.usage.cost_usd > 0



# --------------------------------------------------------------------- streaming
def test_merge_stream_chunks_reassembles_the_reply():
    from codeverse.models.gemini import _merge_stream_chunks, extract_candidate

    c1 = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="hel")]))])
    c2 = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="lo")]),
                                    finish_reason="STOP")],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=5, candidates_token_count=2))
    merged = _merge_stream_chunks([c1, c2])
    text, calls, finish = extract_candidate(merged)
    assert (text, calls, finish) == ("hello", [], "STOP")
    assert merged.usage_metadata.prompt_token_count == 5


def test_merge_stream_chunks_empty_stream_is_retryable():
    from codeverse.models.gemini import _merge_stream_chunks

    with pytest.raises(ModelError) as e:
        _merge_stream_chunks([])
    assert e.value.retryable


def test_drain_stream_cuts_a_stream_past_its_attempt_budget():
    """The wire timeout under streaming bounds only inter-chunk silence, so a
    healthy-but-endless stream must be cut by the attempt deadline instead."""
    from codeverse.models.gemini import _drain_stream

    ticks = iter([1.0, 10.0, 20.0])
    chunk = types.GenerateContentResponse(candidates=[])
    with pytest.raises(ModelError) as e:
        _drain_stream(iter([chunk, chunk, chunk]), deadline=5.0, clock=lambda: next(ticks))
    assert e.value.retryable and "attempt budget" in str(e.value)


def test_streaming_toggle(monkeypatch):
    from codeverse.models.gemini import _streaming_enabled

    monkeypatch.delenv("CV3D_STREAM", raising=False)
    assert _streaming_enabled()
    monkeypatch.setenv("CV3D_STREAM", "0")
    assert not _streaming_enabled()


def test_ipv4_transport_toggle(monkeypatch):
    import httpx

    from codeverse.models.gemini import _ipv4_client_args

    monkeypatch.delenv("CV3D_IPV4", raising=False)
    args = _ipv4_client_args()
    assert isinstance(args["transport"], httpx.HTTPTransport)
    monkeypatch.setenv("CV3D_IPV4", "0")
    assert _ipv4_client_args() == {}


def test_merge_stream_chunks_carries_tool_calls_and_signatures():
    """Tool calls stream as function_call parts; the merge must keep them in arrival
    order next to the text parts, and extract_candidate must still mint/pair ids and
    stash thought signatures — the text-only tests above never exercised this."""
    from codeverse.models.gemini import _merge_stream_chunks

    fc = types.Part(function_call=types.FunctionCall(name="measure", args={"part": "Hull"}))
    fc.thought_signature = b"sig-bytes"
    c1 = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="checking ")]))])
    c2 = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[fc]), finish_reason="STOP")],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=9, candidates_token_count=4))
    merged = _merge_stream_chunks([c1, c2])
    text, calls, finish = extract_candidate_for_test(merged)
    assert text == "checking " and finish == "STOP"
    assert len(calls) == 1 and calls[0].name == "measure" and calls[0].arguments == {"part": "Hull"}
    assert SIGNATURES.get(calls[0].id) == b"sig-bytes"


def extract_candidate_for_test(resp):
    from codeverse.models.gemini import extract_candidate

    return extract_candidate(resp)
