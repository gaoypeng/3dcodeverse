"""JSON-schema adapters for the three providers + a fence-tolerant JSON parser.

Pydantic v2 emits ``$defs``/``$ref``, ``title``, ``default``, ``const``,
``prefixItems`` (tuples), ``anyOf [.., {"type":"null"}]`` and so on.  Each
provider accepts a different subset:

* Gemini ``response_schema`` / function ``parameters``: OpenAPI-ish subset —
  no ``$ref``, no ``additionalProperties``, no ``const``, no ``prefixItems``;
  ``nullable`` instead of null unions; ``propertyOrdering`` controls key order.
* OpenAI strict ``json_schema``: every object needs ``additionalProperties:
  false`` and ``required`` listing every property; ``$defs`` are fine.
* Anthropic tool ``input_schema`` / ``output_config.format``: standard JSON
  schema; we inline refs to be safe and set ``additionalProperties: false``.

All functions are pure and return new dicts (inputs are never mutated).
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

# keys Gemini's Schema type rejects or ignores
_GEMINI_DROP = {
    "title",
    "default",
    "examples",
    "example",
    "$schema",
    "$id",
    "additionalProperties",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "discriminator",
    "readOnly",
    "writeOnly",
    "deprecated",
    "uniqueItems",
    "multipleOf",
    "contentMediaType",
    "contentEncoding",
}


# ----------------------------------------------------------------- $ref inline
def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with every local ``$ref`` (``#/$defs/X`` / ``#/definitions/X``)
    replaced by the referenced definition and the defs tables removed.
    Recursive schemas raise ``ValueError`` (providers can't express them anyway)."""
    root = copy.deepcopy(schema)
    defs: dict[str, Any] = {}
    for table in ("$defs", "definitions"):
        defs.update(root.pop(table, {}) or {})

    def resolve(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [resolve(n, stack) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            ref = node["$ref"]
            name = ref.rsplit("/", 1)[-1]
            if name not in defs:
                raise ValueError(f"unresolvable $ref {ref!r}")
            if name in stack:
                raise ValueError(f"recursive $ref {ref!r} cannot be inlined")
            merged = dict(defs[name])
            # siblings of $ref (e.g. description) override the definition's
            for k, v in node.items():
                if k != "$ref":
                    merged[k] = v
            return resolve(merged, stack + (name,))
        return {k: resolve(v, stack) for k, v in node.items()}

    return resolve(root, ())


# ------------------------------------------------------------------- helpers
def _is_null(s: Any) -> bool:
    return (
        isinstance(s, dict) and s.get("type") == "null" and len([k for k in s if k != "type"]) == 0
    )


def _merge_prefix_items(node: dict[str, Any]) -> dict[str, Any]:
    """Tuples → arrays: ``prefixItems`` becomes ``items`` (union if heterogeneous)."""
    items = node.pop("prefixItems")
    if not isinstance(items, list) or not items:
        return node
    uniq: list[dict[str, Any]] = []
    for it in items:
        if it not in uniq:
            uniq.append(it)
    node["items"] = uniq[0] if len(uniq) == 1 else {"anyOf": uniq}
    node.setdefault("minItems", len(items))
    node.setdefault("maxItems", len(items))
    return node


# --------------------------------------------------------------------- gemini
def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Sanitise a pydantic JSON schema for ``GenerateContentConfig.response_schema``
    / ``FunctionDeclaration.parameters`` (see module docstring)."""
    root = inline_refs(schema)

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        node = {k: v for k, v in node.items() if k not in _GEMINI_DROP}
        # const → enum
        if "const" in node:
            node["enum"] = [node.pop("const")]
            node.setdefault("type", _json_type(node["enum"][0]))
        # type: ["string","null"] → type + nullable
        if isinstance(node.get("type"), list):
            types = [t for t in node["type"] if t != "null"]
            if len(types) != len(node["type"]):
                node["nullable"] = True
            node["type"] = types[0] if len(types) == 1 else types
        # anyOf/oneOf with null → nullable
        for key in ("anyOf", "oneOf"):
            if key in node:
                variants = [v for v in node[key] if not _is_null(v)]
                if len(variants) != len(node[key]):
                    node["nullable"] = True
                if len(variants) == 1:
                    inner = walk(variants[0])
                    merged = {**inner, **{k: v for k, v in node.items() if k != key}}
                    node = merged
                else:
                    node.pop(key)
                    node["anyOf"] = [walk(v) for v in variants]
        if "allOf" in node and len(node["allOf"]) == 1:
            inner = walk(node.pop("allOf")[0])
            node = {**inner, **node}
        if "prefixItems" in node:
            node = _merge_prefix_items(node)
        # enum values must be strings for Gemini; keep type consistent
        if "enum" in node:
            node["enum"] = [str(v) if not isinstance(v, str) else v for v in node["enum"]]
            node["type"] = "string"
            node.pop("format", None)
        # format: only a few are accepted
        if (
            "format" in node
            and node.get("type") == "string"
            and node["format"] not in ("date-time", "enum")
        ):
            node.pop("format")
        if "properties" in node:
            props = {k: walk(v) for k, v in node["properties"].items()}
            node["properties"] = props
            node.setdefault("type", "object")
            if props:
                node["propertyOrdering"] = list(props.keys())
            else:
                # Gemini rejects empty OBJECT properties; describe as free-form
                node.pop("properties")
                node.pop("propertyOrdering", None)
        if "items" in node:
            node["items"] = walk(node["items"])
        return node

    return walk(root)


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


# --------------------------------------------------------------------- openai
def to_openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI *strict* structured outputs: every object gets
    ``additionalProperties: false`` and ``required`` = all properties; optional
    fields become nullable (``anyOf [T, null]``) so the contract still holds.
    ``$defs`` are kept (supported) but inlined when a sibling ``$ref`` exists."""
    root = inline_refs(schema)

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        node = {
            k: v for k, v in node.items() if k not in ("title", "default", "examples", "example")
        }
        if "prefixItems" in node:
            node = _merge_prefix_items(node)
        if "properties" in node:
            props = {k: walk(v) for k, v in node["properties"].items()}
            required = set(node.get("required", []))
            for name, sub in props.items():
                if name not in required:
                    props[name] = _nullable_union(sub)
            node["properties"] = props
            node["required"] = list(props.keys())
            node["additionalProperties"] = False
            node.setdefault("type", "object")
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                node[key] = [walk(v) for v in node[key]]
        if "items" in node:
            node["items"] = walk(node["items"])
        return node

    return walk(root)


def _nullable_union(sub: dict[str, Any]) -> dict[str, Any]:
    if "anyOf" in sub:
        if any(_is_null(v) for v in sub["anyOf"]):
            return sub
        return {**sub, "anyOf": sub["anyOf"] + [{"type": "null"}]}
    if isinstance(sub.get("type"), list):
        return sub if "null" in sub["type"] else {**sub, "type": sub["type"] + ["null"]}
    if "type" in sub:
        return {**sub, "type": [sub["type"], "null"]}
    return {"anyOf": [sub, {"type": "null"}]}


# ------------------------------------------------------------------ anthropic
def to_anthropic_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Standard JSON schema with refs inlined, tuples flattened and
    ``additionalProperties: false`` on objects (tool ``input_schema`` / format)."""
    root = inline_refs(schema)

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        node = {
            k: v for k, v in node.items() if k not in ("title", "default", "examples", "example")
        }
        if "prefixItems" in node:
            node = _merge_prefix_items(node)
        if "properties" in node:
            node["properties"] = {k: walk(v) for k, v in node["properties"].items()}
            node.setdefault("type", "object")
            node.setdefault("additionalProperties", False)
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                node[key] = [walk(v) for v in node[key]]
        if "items" in node:
            node["items"] = walk(node["items"])
        return node

    return walk(root)


# ------------------------------------------------------------------ JSON parse
_FENCE_RE = re.compile(r"```(?:json|JSON|javascript|js)?\s*\n?(.*?)```", re.DOTALL)


class JsonParseError(ValueError):
    """The model's text does not contain a parseable JSON value."""


def parse_json_lenient(text: str) -> Any:
    """Parse JSON from model text tolerating code fences, leading prose and
    trailing chatter.  Tries: whole text → fenced block → outermost {...} / [...]
    span.  Raises ``JsonParseError`` when nothing parses."""
    if text is None:
        raise JsonParseError("empty response")
    s = text.strip()
    if not s:
        raise JsonParseError("empty response")
    candidates: list[str] = [s]
    for m in _FENCE_RE.finditer(s):
        candidates.append(m.group(1).strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = s.find(opener), s.rfind(closer)
        if i != -1 and j > i:
            candidates.append(s[i : j + 1])
    last_err: Exception | None = None
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError as exc:
            last_err = exc
            # tolerate trailing commas
            fixed = re.sub(r",\s*([}\]])", r"\1", cand)
            if fixed != cand:
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    pass
    raise JsonParseError(f"no JSON value found: {last_err} — head={s[:120]!r}")
