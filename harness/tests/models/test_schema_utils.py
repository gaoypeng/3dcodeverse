"""Schema sanitisers + lenient JSON parsing."""

from __future__ import annotations

import json

import pytest
from google.genai import types
from pydantic import BaseModel

from codeverse.contracts.plan import ArticulatedPlan, ScenePlan, StaticPlan
from codeverse.models.schema_utils import (
    JsonParseError,
    inline_refs,
    parse_json_lenient,
    to_anthropic_schema,
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


@pytest.mark.parametrize("model", [StaticPlan, ArticulatedPlan, ScenePlan])
def test_gemini_schema_is_clean_and_sdk_valid(model):
    g = to_gemini_schema(model.model_json_schema())

    def check(n):
        if isinstance(n, dict):
            for bad in (
                "$ref",
                "$defs",
                "title",
                "default",
                "const",
                "prefixItems",
                "additionalProperties",
            ):
                assert bad not in n, (bad, n)
            if "anyOf" in n:
                assert all(v.get("type") != "null" for v in n["anyOf"])
            if n.get("type") == "object" and "properties" in n:
                assert n["propertyOrdering"] == list(n["properties"].keys())

    _walk(g, check)
    types.Schema.model_validate(g)  # the SDK accepts it


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


def test_openai_strict_schema_shape():
    s = to_openai_strict_schema(StaticPlan.model_json_schema())

    def check(n):
        if isinstance(n, dict) and "properties" in n:
            assert n["additionalProperties"] is False
            assert set(n["required"]) == set(n["properties"].keys())

    _walk(s, check)
    # optional field became nullable
    part = s["properties"]["parts"]["items"]["properties"]
    assert part["material"]["type"] == ["string", "null"]
    assert {"type": "null"} in part["attach_to"]["anyOf"]


def test_anthropic_schema_shape():
    s = to_anthropic_schema(ScenePlan.model_json_schema())
    assert "$defs" not in s and s["additionalProperties"] is False
    assert s["properties"]["bounds"]["properties"]["center"]["items"] == {"type": "number"}


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        'Sure! ```json\n{"a": 1}\n```',
        'prefix text {"a": 1} trailing',
        '{"a": 1,}',
        '```\n{"a": 1}\n```',
    ],
)
def test_parse_json_lenient(text):
    assert parse_json_lenient(text) == {"a": 1}


def test_parse_json_lenient_array_and_failure():
    assert parse_json_lenient("[1, 2]") == [1, 2]
    with pytest.raises(JsonParseError):
        parse_json_lenient("no json here")
    with pytest.raises(JsonParseError):
        parse_json_lenient("")
    assert json.dumps(parse_json_lenient('{"n": {"x": [1]}}')) == '{"n": {"x": [1]}}'
