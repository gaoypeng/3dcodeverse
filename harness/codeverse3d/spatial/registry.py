"""Tool registry: one ``@tool(name, ArgsModel, description)`` definition → python fn + typed args + a ``3dcode`` subcommand.

``ToolDef.call`` is the one error boundary: tool bodies never need try/except.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeverse3d.workspace import Workspace


class NoArgs(BaseModel):
    """This tool takes no arguments."""


class Observation(BaseModel):
    """A tool's answer: ``ok`` is the VERDICT, ``failed`` says the tool could not run.

    Only ``failed`` makes ``3dcode`` exit non-zero; a negative verdict is a result, not a broken call.
    """

    # validate_assignment: the failed-implies-not-ok invariant must hold on later assignment too
    model_config = ConfigDict(validate_assignment=True)

    ok: bool = True
    failed: bool = Field(default=False, description="the tool could not run: exception, missing "
                                                    "artefact, unusable arguments (NOT a negative verdict)")
    text: str = Field(description="human/LLM-readable summary (verdict first)")
    numbers: dict[str, Any] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, description="PNG paths the agent opens with its own viewer")
    duration_ms: int = 0

    @model_validator(mode="after")
    def _a_failure_is_never_a_pass(self) -> Observation:
        """``failed`` implies ``not ok``."""
        if self.failed and self.ok:
            raise ValueError("Observation(ok=True, failed=True): a tool that could not run has no verdict")
        return self

    @classmethod
    def error(cls, text: str, **numbers: Any) -> Observation:
        """The tool could not run (caught exceptions, missing artefacts).

        Only ``build``, ``scene_probe`` and ``observe.render_observation`` also set ``failed`` themselves.
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
        """JSON schema of the arguments object."""
        s = self.args_model.model_json_schema()
        s.pop("title", None)
        return s

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
        except Exception as e:  # the CLI reports it; it never tracebacks at the agent
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


def _ensure_loaded() -> None:
    """Import the modules that register every tool."""
    import codeverse3d.spatial.tools  # noqa: F401 — its @tool decorators fill _REGISTRY
    import codeverse3d.workflow  # noqa: F401 — init / validate_plan / check / judge / judge_pack / score
