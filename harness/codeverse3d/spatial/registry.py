"""Tool registry: one definition → python fn + JSON schema + MCP + prompt card.

Usage::

    class MeasureArgs(BaseModel):
        parts: list[str] = Field(default_factory=list, description="limit to these part names")

    @tool("measure", MeasureArgs, "Measure bbox/extents/islands of the built object (and per part).")
    def measure(ctx: ToolContext, args: MeasureArgs) -> Observation: ...

``ToolContext`` carries the workspace and settings; tools never touch globals.
``ToolDef.call`` is the one error boundary: ``ToolUsageError`` becomes a usage
Observation, anything else a failed Observation — a tool body never needs try/except.
Observations: ``text`` (what the agent reads), ``numbers`` (machine-readable),
``images`` (paths, small PNGs the agent may view), ``ok`` (the VERDICT) and
``failed`` (the tool could not run).  The two are different answers and only
``failed`` may reach the model as a protocol error — see :class:`Observation`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeverse3d.workspace import Workspace


class NoArgs(BaseModel):
    """This tool takes no arguments."""


class Observation(BaseModel):
    """A tool's answer: ``ok`` is the VERDICT, ``failed`` says the tool could not run.

    Only ``failed`` becomes MCP ``is_error``; a negative verdict is a result, not a
    broken call (what that cost when the two were one flag: docs/COST.md §30).
    """

    # validate_assignment: the invariant below has to hold for `obs.failed = ...` at a call
    # site as well, not only at construction (spatial/tools.scene_probe did exactly that)
    model_config = ConfigDict(validate_assignment=True)

    ok: bool = True
    failed: bool = Field(default=False, description="the tool could not run: exception, missing "
                                                    "artefact, unusable arguments (NOT a negative verdict)")
    text: str = Field(description="human/LLM-readable summary (≤ ~2k chars)")
    numbers: dict[str, Any] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, description="PNG paths (small, labelled)")
    duration_ms: int = 0

    @model_validator(mode="after")
    def _a_failure_is_never_a_pass(self) -> Observation:
        """``failed`` implies ``not ok``: a tool that could not run has no verdict to
        report, and ``Observation(ok=True, failed=True)`` was representable."""
        if self.failed and self.ok:
            raise ValueError("Observation(ok=True, failed=True): a tool that could not run has no verdict")
        return self

    @classmethod
    def error(cls, text: str, **numbers: Any) -> Observation:
        """The tool could not run: every failure caught at the :meth:`ToolDef.call`
        boundary and every missing / unreadable artefact is built here.

        It is not the only thing that sets ``failed``.  Three results are failures
        the tool computed rather than exceptions it caught, and they set the flag on
        an observation they compose themselves: ``build`` (the runtime reported
        success and left no readable GLB), ``scene_probe`` (the probe driver died)
        and ``observe.render_observation`` (no view AND no console error — with one
        it is a verdict).  Those four places are the whole list; nothing else may set
        ``failed``.
        """
        return cls(ok=False, failed=True, text=text, numbers=numbers)


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
    extra: dict[str, Any] = field(default_factory=dict)


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
    """Import the module that registers every tool (idempotent)."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    import codeverse3d.spatial.tools  # noqa: F401 — its @tool decorators fill _REGISTRY
