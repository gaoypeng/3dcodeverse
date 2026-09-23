"""Schema sanitisers + lenient JSON parsing."""

from __future__ import annotations

import json

import pytest
from google.genai import types
from pydantic import BaseModel

from codeverse3d.contracts.plan import ArticulatedPlan, ScenePlan, StaticPlan
from codeverse3d.models.schema_utils import (
    JsonParseError,
    inline_refs,
    parse_json_lenient,
    to_gemini_schema,
    to_openai_strict_schema,
)


def _walk(node, fn):
    """Visit schema nodes only (values of a ``properties`` map are schemas, the map is not)."""
    fn(node)
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "properties" and isinstance(v, dict):
                for sub in v.values():
                    _walk(sub, fn)
            else:
                _walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            _walk(v, fn)


def test_gemini_schemas_are_clean_and_sdk_valid():
    def check(node):
        if not isinstance(node, dict):
            return
        for bad in (
            "$ref",
            "$defs",
            "title",
            "default",
            "const",
            "prefixItems",
            "additionalProperties",
        ):
            assert bad not in node, (bad, node)
        if "anyOf" in node:
            assert all(value.get("type") != "null" for value in node["anyOf"])
        if node.get("type") == "object" and "properties" in node:
            assert node["propertyOrdering"] == list(node["properties"])

    for model in (StaticPlan, ArticulatedPlan, ScenePlan):
        schema = to_gemini_schema(model.model_json_schema())
        _walk(schema, check)
        types.Schema.model_validate(schema)


def test_gemini_specifics():
    class M(BaseModel):
        vec: tuple[float, float, float]
        opt: str | None = None
        kind: str = "x"

    g = to_gemini_schema(M.model_json_schema())
    assert g["properties"]["vec"] == {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 3,
        "maxItems": 3,
    }
    assert g["properties"]["opt"] == {"type": "string", "nullable": True}
    assert g["required"] == ["vec"]
    # const → enum, int enums → strings
    g2 = to_gemini_schema(
        {"type": "object", "properties": {"a": {"const": "only"}, "n": {"enum": [1, 2]}}}
    )
    assert g2["properties"]["a"] == {"enum": ["only"], "type": "string"}
    assert g2["properties"]["n"] == {"enum": ["1", "2"], "type": "string"}


def test_inline_refs_and_recursion_guard():
    s = {
        "$defs": {"A": {"type": "string"}},
        "properties": {"x": {"$ref": "#/$defs/A", "description": "d"}},
    }
    assert inline_refs(s) == {"properties": {"x": {"type": "string", "description": "d"}}}
    rec = {"$defs": {"N": {"properties": {"child": {"$ref": "#/$defs/N"}}}}, "$ref": "#/$defs/N"}
    with pytest.raises(ValueError):
        inline_refs(rec)


def test_openai_strict_schemas_never_add_null():
    """Wire contract == pydantic contract: a field accepts null on the wire iff the
    source schema does (``x: T | None``) — never because it merely has a default /
    default_factory, else the model's nulls fail ``model_validate``."""

    def pairs(o, st):
        if isinstance(o, dict) and "properties" in o and isinstance(st, dict):
            for name, sub in o["properties"].items():
                yield sub, st["properties"][name]
                yield from pairs(sub, st["properties"][name])
        if isinstance(o, dict) and "items" in o and isinstance(st, dict):
            yield from pairs(o["items"], st["items"])
        if isinstance(o, dict) and "anyOf" in o and isinstance(st, dict):
            for a, b in zip(o["anyOf"], st["anyOf"], strict=True):
                yield from pairs(a, b)

    for model in (StaticPlan, ArticulatedPlan, ScenePlan):
        original = inline_refs(model.model_json_schema())
        strict = to_openai_strict_schema(model.model_json_schema())
        compared = 0
        for orig, strict_sub in pairs(original, strict):
            if isinstance(orig, dict):
                compared += orig.get("default") is not None
                assert _accepts_null(strict_sub) == _accepts_null(orig), (orig, strict_sub)
        assert compared > 0


def _accepts_null(sub) -> bool:
    return "null" in json.dumps(sub.get("type")) or any(
        isinstance(v, dict) and v.get("type") == "null" for v in sub.get("anyOf", [])
    )


def test_parse_json_lenient_accepts_wrappers_and_rejects_non_json():
    for text in (
        '{"a": 1}',
        'Sure! ```json\n{"a": 1}\n```',
        'prefix text {"a": 1} trailing',
        '{"a": 1,}',
        '```\n{"a": 1}\n```',
        '{"a": 1} and later {broken',
        'note {"a": 1} ps: see {figure 2}',
    ):
        assert parse_json_lenient(text) == {"a": 1}
    assert parse_json_lenient("[1, 2]") == [1, 2]
    for text in ("no json here", ""):
        with pytest.raises(JsonParseError):
            parse_json_lenient(text)
    assert json.dumps(parse_json_lenient('{"n": {"x": [1]}}')) == '{"n": {"x": [1]}}'


def test_ask_structured_returns_the_triple_and_the_billed_usage_on_failure():
    """The callers branch on the error string, so a provider that returns prose must not
    raise; a bad-JSON reply is charged like a good one: the caller's tally gets the usage."""
    from codeverse3d.contracts.chat import ChatResponse
    from codeverse3d.contracts.common import Usage
    from codeverse3d.models.base import ModelError
    from codeverse3d.models.schema_utils import ask_structured

    class Answer(BaseModel):
        ok: bool

    class Prose:
        def generate(self, request):
            return ChatResponse(text="I refuse.", usage=Usage(cost_usd=0.01))

    out, usage, err = ask_structured(Prose(), Answer, system="s", text="t", temperature=0.2, label="l")
    assert out is None and usage.cost_usd == 0.01 and err.startswith("answer unparsable:")

    billed = Usage(input_tokens=42_000, output_tokens=100, cost_usd=0.123, backend="gemini")

    class Failing:
        def generate(self, request):
            raise ModelError("bad JSON reply, still billed", usage=billed)

    out, usage, err = ask_structured(Failing(), Answer, system="s", text="t", temperature=0.0, label="l")
    assert out is None and err.startswith("call failed:")
    assert usage.cost_usd == 0.123 and usage.input_tokens == 42_000
