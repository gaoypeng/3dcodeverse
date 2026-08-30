"""Schema sanitisers + lenient JSON parsing."""

from __future__ import annotations

import json

import pytest
from google.genai import types
from pydantic import BaseModel, ValidationError

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


def test_openai_strict_schema_shape():
    s = to_openai_strict_schema(StaticPlan.model_json_schema())

    def check(n):
        if isinstance(n, dict) and "properties" in n:
            assert n["additionalProperties"] is False
            assert set(n["required"]) == set(n["properties"].keys())

    _walk(s, check)
    part = s["properties"]["parts"]["items"]["properties"]
    # optional-without-default (``str | None = None``) is nullable on the wire …
    assert {"type": "null"} in part["attach_to"]["anyOf"]
    # … but defaulted fields stay non-null (pydantic rejects null there) with a hint
    assert (
        part["material"]["type"] == "string" and '[default: ""]' in part["material"]["description"]
    )
    assert (
        part["instances"]["type"] == "integer"
        and "[default: 1]" in part["instances"]["description"]
    )
    assert part["symmetry"]["type"] == "string" and "null" not in json.dumps(part["symmetry"])
    assert s["properties"]["style_notes"]["type"] == "string"


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


def test_openai_strict_schema_rejects_nulls_that_pydantic_rejects():
    jsonschema = pytest.importorskip("jsonschema")
    strict = to_openai_strict_schema(StaticPlan.model_json_schema())
    bbox = {"center": [0, 0, 0.5], "extents": [1, 1, 1]}
    part = {
        "name": "Seat",
        "role": "r",
        "description": "d",
        "bbox": bbox,
        "material": "",
        "attach_to": None,
        "symmetry": "none",
        "instances": 1,
        "children": [],
        "detail_hint": "",
    }
    good = {
        "object_name": "Chair",
        "summary": "x",
        "overall_bbox": bbox,
        "style_notes": "",
        "parts": [part],
        "acceptance": [],
    }
    jsonschema.validate(good, strict)
    StaticPlan.model_validate(good)
    bad = {**good, "style_notes": None, "parts": [{**part, "material": None, "instances": None}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, strict)  # the model is not allowed to answer like this
    with pytest.raises(ValidationError):
        StaticPlan.model_validate(bad)  # … because the contract would reject it


def test_anthropic_schema_shape():
    s = to_anthropic_schema(ScenePlan.model_json_schema())
    assert "$defs" not in s and s["additionalProperties"] is False
    assert s["properties"]["bounds"]["properties"]["center"]["items"] == {"type": "number"}


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


def test_ask_structured_returns_the_triple_for_prose_it_cannot_parse():
    """The five callers branch on the error string; none of them catches.  Every shipped
    provider raises ModelError on unparseable structured output, but this must not depend
    on all three keeping that half of the ChatModel contract."""
    from codeverse.contracts.chat import ChatResponse
    from codeverse.contracts.common import Usage
    from codeverse.models.schema_utils import ask_structured

    class Answer(BaseModel):
        ok: bool

    class Prose:
        """A ChatModel that hands back text instead of raising (base.py:22-30 says it should)."""

        def generate(self, request):
            return ChatResponse(text="I refuse.", usage=Usage(cost_usd=0.01))

    out, usage, err = ask_structured(Prose(), Answer, system="s", text="t", temperature=0.2, label="l")
    assert out is None and usage.cost_usd == 0.01 and err.startswith("answer unparsable:")
