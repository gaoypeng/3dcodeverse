"""Tool registry: one definition → python fn + JSON schema + MCP + prompt card.

Usage::

    class MeasureArgs(BaseModel):
        parts: list[str] = Field(default_factory=list, description="limit to these part names")

    @tool("measure", MeasureArgs, "Measure bbox/extents/islands of the built object (and per part).")
    def measure(ctx: ToolContext, args: MeasureArgs) -> Observation: ...

``ToolContext`` carries the workspace and settings; tools never touch globals.
Observations: ``text`` (what the agent reads), ``numbers`` (machine-readable),
``images`` (paths, small PNGs the agent may view), ``ok``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.workspace import Workspace


class Observation(BaseModel):
    ok: bool = True
    text: str = Field(description="human/LLM-readable summary (≤ ~2k chars)")
    numbers: dict[str, Any] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, description="PNG paths (small, labelled)")
    duration_ms: int = 0

    @classmethod
    def error(cls, text: str, **numbers: Any) -> Observation:
        return cls(ok=False, text=text, numbers=numbers)


class ToolUsageError(ValueError):
    """Raised by tools on caller mistakes; ``fix_example`` is shown to the agent."""

    def __init__(self, message: str, fix_example: str = ""):
        super().__init__(message)
        self.fix_example = fix_example


@dataclass
class ToolContext:
    workspace: Workspace
    round_index: int = 0
    language: str = ""
    track: str = ""
    scratch: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def scratch_dir(self) -> Path:
        d = self.scratch or (self.workspace.artifacts / "tool_scratch")
        d.mkdir(parents=True, exist_ok=True)
        return d


@dataclass(frozen=True)
class ToolDef:
    name: str
    args_model: type[BaseModel]
    description: str
    fn: Callable[[ToolContext, BaseModel], Observation]
    tracks: tuple[str, ...] = ()  # empty = all
    languages: tuple[str, ...] = ()  # empty = all
    cost_hint: str = "fast"  # fast | slow

    def schema(self) -> dict[str, Any]:
        """JSON schema of the arguments object (for native tool calling / MCP)."""
        s = self.args_model.model_json_schema()
        s.pop("title", None)
        return s

    def card(self) -> str:
        """Prompt card: name, one-line purpose, args with descriptions."""
        props = self.schema().get("properties", {})
        req = set(self.schema().get("required", []))
        lines = [f"- `{self.name}` ({self.cost_hint}): {self.description}"]
        for k, v in props.items():
            typ = v.get("type", "any")
            d = v.get("description", "")
            mark = "required" if k in req else f"default={v.get('default')!r}"
            lines.append(f"    · {k}: {typ} — {d} ({mark})")
        return "\n".join(lines)

    def call(self, ctx: ToolContext, arguments: dict[str, Any] | BaseModel | None = None) -> Observation:
        import time

        t0 = time.time()
        try:
            args = arguments if isinstance(arguments, BaseModel) else self.args_model(**(arguments or {}))
        except Exception as e:  # pydantic ValidationError → usage error for the agent
            return Observation.error(f"{self.name}: invalid arguments: {e}\nSchema: {self.schema()}")
        try:
            obs = self.fn(ctx, args)
        except ToolUsageError as e:
            obs = Observation.error(f"{self.name}: {e}" + (f"\nExample: {e.fix_example}" if e.fix_example else ""))
        except Exception as e:  # never crash the agent loop
            obs = Observation.error(f"{self.name} failed: {type(e).__name__}: {e}")
        obs.duration_ms = int((time.time() - t0) * 1000)
        return obs


_REGISTRY: dict[str, ToolDef] = {}


def tool(
    name: str,
    args_model: type[BaseModel],
    description: str,
    *,
    tracks: tuple[str, ...] = (),
    languages: tuple[str, ...] = (),
    cost_hint: str = "fast",
) -> Callable[[Callable[[ToolContext, Any], Observation]], Callable[[ToolContext, Any], Observation]]:
    def deco(fn: Callable[[ToolContext, Any], Observation]) -> Callable[[ToolContext, Any], Observation]:
        sig = inspect.signature(fn)
        if len(sig.parameters) != 2:
            raise TypeError(f"tool {name}: fn must be fn(ctx, args)")
        if name in _REGISTRY:
            raise ValueError(f"tool {name} registered twice")
        _REGISTRY[name] = ToolDef(name, args_model, description, fn, tracks, languages, cost_hint)
        return fn

    return deco


def get_tool(name: str) -> ToolDef:
    _ensure_loaded()
    if name not in _REGISTRY:
        raise KeyError(f"unknown tool {name!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def list_tools(*, track: str = "", language: str = "") -> list[ToolDef]:
    _ensure_loaded()
    out = []
    for t in _REGISTRY.values():
        if track and t.tracks and track not in t.tracks:
            continue
        if language and t.languages and language not in t.languages:
            continue
        out.append(t)
    return sorted(out, key=lambda t: t.name)


def tool_cards(*, track: str = "", language: str = "") -> str:
    return "\n".join(t.card() for t in list_tools(track=track, language=language))


_LOADED = False


def _ensure_loaded() -> None:
    """Import the modules that register tools (idempotent)."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    import importlib

    for mod in ("codeverse.spatial.tools",):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as e:  # during bootstrap only
            if mod not in str(e):
                raise
