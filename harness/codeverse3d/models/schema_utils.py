"""JSON-schema adapters for the three providers, a fence-tolerant JSON parser and
``ask_structured`` (one schema-validated call → object | None, usage, error).

Pydantic schemas are rewritten per provider: Gemini takes an OpenAPI-ish subset, OpenAI strict
needs closed objects with every property required, Anthropic takes inlined standard JSON schema.
All functions are pure and return new dicts.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse3d.contracts.common import Usage
from codeverse3d.models.base import ModelError

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
    """Copy with every local ``$ref`` replaced by its definition; recursive schemas raise ``ValueError``."""
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
    """``prefixItems`` becomes ``items`` (union if heterogeneous)."""
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
    """Sanitise a pydantic JSON schema for Gemini ``response_schema`` / function ``parameters``."""
    root = inline_refs(schema)

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        node = {k: v for k, v in node.items() if k not in _GEMINI_DROP}
        if "const" in node:
            node["enum"] = [node.pop("const")]
            node.setdefault("type", _json_type(node["enum"][0]))
        if isinstance(node.get("type"), list):
            types = [t for t in node["type"] if t != "null"]
            if len(types) != len(node["type"]):
                node["nullable"] = True
            node["type"] = types[0] if len(types) == 1 else types
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
        # Gemini enums are strings only
        if "enum" in node:
            node["enum"] = [str(v) if not isinstance(v, str) else v for v in node["enum"]]
            node["type"] = "string"
            node.pop("format", None)
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
                # Gemini rejects empty OBJECT properties
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


# ------------------------------------------------------------ openai / anthropic
def _walk_schema(root: Any, on_object: Callable[[dict[str, Any], Callable[[Any], Any]], None]) -> Any:
    """Walk shared by the openai-strict and anthropic schemas; ``on_object(node, walk)`` rewrites ``properties``."""

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
            on_object(node, walk)
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                node[key] = [walk(v) for v in node[key]]
        if "items" in node:
            node["items"] = walk(node["items"])
        return node

    return walk(root)


def to_openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI strict schema: closed objects, every property required.  Null is allowed only
    where the source allows it; a non-null ``default`` becomes a ``[default: …]`` hint."""

    def strict_object(node: dict[str, Any], walk: Callable[[Any], Any]) -> None:
        props = {k: walk(v) for k, v in node["properties"].items()}
        required = set(node.get("required", []))
        for name, sub in props.items():
            original = node["properties"][name]
            if name in required or not isinstance(original, dict):
                continue
            if original.get("default") is not None:
                props[name] = _with_default_hint(sub, original["default"])
        node["properties"] = props
        node["required"] = list(props.keys())
        node["additionalProperties"] = False
        node.setdefault("type", "object")

    return _walk_schema(inline_refs(schema), strict_object)


def _with_default_hint(sub: dict[str, Any], default: Any) -> dict[str, Any]:

    hint = f"[default: {json.dumps(default)}]"
    desc = str(sub.get("description") or "").strip()
    return {**sub, "description": f"{desc} {hint}".strip()}


def to_anthropic_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Standard JSON schema, refs inlined, objects closed."""

    def closed_object(node: dict[str, Any], walk: Callable[[Any], Any]) -> None:
        node["properties"] = {k: walk(v) for k, v in node["properties"].items()}
        node.setdefault("type", "object")
        node.setdefault("additionalProperties", False)

    return _walk_schema(inline_refs(schema), closed_object)


# ------------------------------------------------------------------ JSON parse
_FENCE_RE = re.compile(r"```(?:json|JSON|javascript|js)?\s*\n?(.*?)```", re.DOTALL)


class JsonParseError(ValueError):
    """The model's text does not contain a parseable JSON value."""


def _first_balanced(text: str, opener: str = "{", closer: str = "}") -> str | None:
    """The first depth-balanced ``{...}`` span (naive: braces inside strings can fool it)."""
    start = text.find(opener)
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


#: C0 control characters except TAB, LF and CR
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: replaced by a space, not "": deleting could fuse adjacent numbers
_CONTROL_SUB = " "


def strip_control_chars(value: Any) -> Any:
    """Replace C0 control characters in every string of a parsed JSON value (a NUL later
    crashes ``subprocess.Popen`` far from its source)."""
    if isinstance(value, str):
        return _CONTROL_RE.sub(_CONTROL_SUB, value)
    if isinstance(value, dict):
        return {k: strip_control_chars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_control_chars(v) for v in value]
    return value


def parse_json_lenient(text: str) -> Any:
    """Parse JSON from model text tolerating fences, leading prose and trailing chatter."""
    if text is None:
        raise JsonParseError("empty response")
    s = text.strip()
    if not s:
        raise JsonParseError("empty response")
    candidates: list[str] = [s]
    for m in _FENCE_RE.finditer(s):
        candidates.append(m.group(1).strip())
    balanced = _first_balanced(s)
    if balanced is not None:
        candidates.append(balanced)
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = s.find(opener), s.rfind(closer)
        if i != -1 and j > i:
            candidates.append(s[i : j + 1])
    last_err: Exception | None = None
    for cand in candidates:
        try:
            return strip_control_chars(json.loads(cand))
        except json.JSONDecodeError as exc:
            last_err = exc
            fixed = re.sub(r",\s*([}\]])", r"\1", cand)
            if fixed != cand:
                try:
                    return strip_control_chars(json.loads(fixed))
                except json.JSONDecodeError:
                    pass
    raise JsonParseError(f"no JSON value found: {last_err} — head={s[:120]!r}")


def ask_structured(model: Any, schema: type[BaseModel], *, system: str, text: str,
                   images: Sequence[ImagePart] = (), temperature: float,
                   label: str, thinking: Literal["off", "low", "medium", "high"] = "low") -> tuple[Any, Usage, str]:
    """One structured call → ``(validated object | None, usage, error)``; failures are returned, not raised."""
    req = ChatRequest(messages=[ChatMessage.user(text, images=list(images) or None)], system=system,
                      response_schema=schema.model_json_schema(), temperature=temperature,
                      thinking=thinking, max_output_tokens=65_536, max_wait_s=900.0, label=label)
    try:
        resp = model.generate(req)
    except ModelError as e:  # e.usage is what the provider ALREADY billed for the failure
        return None, e.usage, f"call failed: {e}"
    try:
        payload = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text)
        return schema.model_validate(payload), resp.usage, ""
    except (JsonParseError, ValidationError, TypeError) as e:
        return None, resp.usage, f"answer unparsable: {e}"
