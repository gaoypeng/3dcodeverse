"""JSON-schema adapters for the three providers, a fence-tolerant JSON parser and
``ask_structured`` (one schema-validated call → object | None, usage, error).

Pydantic v2 emits ``$defs``/``$ref``, ``title``, ``default``, ``const``,
``prefixItems`` (tuples), ``anyOf [.., {"type":"null"}]`` and so on.  Each
provider accepts a different subset:

* Gemini ``response_schema`` / function ``parameters``: OpenAPI-ish subset —
  no ``$ref``, no ``additionalProperties``, no ``const``, no ``prefixItems``;
  ``nullable`` instead of null unions; ``propertyOrdering`` controls key order.
* OpenAI strict ``json_schema``: every object needs ``additionalProperties:
  false`` and ``required`` listing every property; ``$defs`` are fine.  Optional
  fields keep their own type — null is allowed only where the source schema
  already allows it (``anyOf [T, null]``); a non-null ``default`` is hinted in
  the description.  So the wire contract equals the pydantic one and the model
  never returns ``null`` where the contract wants ``""`` / ``1`` / ``[]``.
* Anthropic tool ``input_schema`` / ``output_config.format``: standard JSON
  schema; we inline refs to be safe and set ``additionalProperties: false``.

All functions are pure and return new dicts (inputs are never mutated).
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.common import Usage
from codeverse.models.base import ModelError

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
    ``additionalProperties: false`` and ``required`` = all properties.  Optional
    fields keep their type: null is accepted only where the source already says
    so (``x: T | None``), never added — the consumer validates with the original
    pydantic model, which rejects ``null`` for ``x: str = ""`` / ``list = []``.
    A non-null ``default`` becomes a ``[default: …]`` hint.  ``$defs`` are inlined."""
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
                original = node["properties"][name]
                if name in required or not isinstance(original, dict):
                    continue
                if original.get("default") is not None:
                    props[name] = _with_default_hint(sub, original["default"])
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


def _with_default_hint(sub: dict[str, Any], default: Any) -> dict[str, Any]:
    """Keep the field non-null; tell the model what to emit when it has nothing."""
    hint = f"[default: {json.dumps(default)}]"
    desc = str(sub.get("description") or "").strip()
    return {**sub, "description": f"{desc} {hint}".strip()}


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


def _first_balanced(text: str, opener: str = "{", closer: str = "}") -> str | None:
    """The first depth-balanced ``{...}`` span (naive counting; braces inside JSON
    strings can fool it, which is why it is only one candidate among several)."""
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


#: C0 control characters that are never legitimate inside a model's text field.  TAB,
#: LF and CR are kept — they carry meaning in a description or a code snippet.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: what a stripped control character is replaced BY.  A space, not "": the observed case
#: was a model writing \u0000 where it meant a glyph it could not encode ("0.078 × 0.300"
#: -> "0.078 \x00 0.300"), and deleting the byte would silently fuse "0.078" and "0.300"
#: into a different number.  A space keeps the text honest and readable.
_CONTROL_SUB = " "


def strip_control_chars(value: Any) -> Any:
    """Recursively replace C0 control characters in every string of a parsed JSON value.

    A model can emit ``\u0000`` — it is legal JSON, pydantic accepts it, and the harness
    persists it happily.  It then detonates far away: on 2026-08-24 a gemini-3.7-flash
    plan carried five of them (written where the model meant ``×`` and ``±``), survived
    validation and ``plan.json``, and killed all four generation tasks of a codex cell
    four stages later with ``ValueError: embedded null byte`` out of ``subprocess.Popen``
    — an error naming no file, no field and no value.  Sanitising at ingestion is the
    only place that covers every model, every track and every downstream consumer.
    """
    if isinstance(value, str):
        return _CONTROL_RE.sub(_CONTROL_SUB, value)
    if isinstance(value, dict):
        return {k: strip_control_chars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_control_chars(v) for v in value]
    return value


def parse_json_lenient(text: str) -> Any:
    """Parse JSON from model text tolerating code fences, leading prose and
    trailing chatter.  Tries: whole text → fenced block → first balanced {...}
    → outermost {...} / [...] span.  Raises ``JsonParseError`` when nothing
    parses."""
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
            # tolerate trailing commas
            fixed = re.sub(r",\s*([}\]])", r"\1", cand)
            if fixed != cand:
                try:
                    return strip_control_chars(json.loads(fixed))
                except json.JSONDecodeError:
                    pass
    raise JsonParseError(f"no JSON value found: {last_err} — head={s[:120]!r}")


def ask_structured(model: Any, schema: type[BaseModel], *, system: str, text: str,
                   images: Sequence[ImagePart] = (), temperature: float,
                   label: str) -> tuple[Any, Usage, str]:
    """One structured call → ``(validated object | None, usage, error)``.

    Five callers (reference gate / image-prompt plan / mismatch diff, the texture
    material plan and the scene texture pack) built the same ChatRequest
    (thinking="low", the 65 536 ceiling, the 900 s wait), caught the same two failure
    families and parsed the same two ways.  They differ only in what they RETURN on
    failure, which is why this hands the error back rather than raising or deciding.
    Not for callers that need the failed call's own ``Usage`` or separate parse
    errors from call errors (``judges.pairwise``, ``tracks.planner``).
    """
    req = ChatRequest(messages=[ChatMessage.user(text, images=list(images) or None)], system=system,
                      response_schema=schema.model_json_schema(), temperature=temperature,
                      thinking="low", max_output_tokens=65_536, max_wait_s=900.0, label=label)
    try:
        resp = model.generate(req)
    except ModelError as e:
        return None, Usage(), f"call failed: {e}"
    try:  # the parse is inside: every provider raises ModelError first today, but this
        # function must not depend on all three keeping that half of the ChatModel contract
        payload = resp.parsed if resp.parsed is not None else parse_json_lenient(resp.text)
        return schema.model_validate(payload), resp.usage, ""
    except (JsonParseError, ValidationError, TypeError) as e:
        return None, resp.usage, f"answer unparsable: {e}"
