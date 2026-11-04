"""Schema sanitisers + lenient JSON parsing."""

from __future__ import annotations

import json

from pydantic import BaseModel

from codeverse3d.contracts.plan import ArticulatedPlan, ScenePlan, StaticPlan
from codeverse3d.models.schema_utils import (
    inline_refs,
    to_gemini_schema,
    to_openai_strict_schema,
)


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
