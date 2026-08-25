"""Harness settings: keys, binaries, directories, defaults.

Resolution order (later wins): built-in defaults < ``~/.config/codeverse/config.yaml``
< ``./codeverse.yaml`` < environment variables (``CV3D_*``).  Secrets are never
written to run records.

Gemini keys: ``GEMINI_API_KEYS`` (csv) or ``GEMINI_API_KEY`` or the owner's
``~/.config/astra3d/gemini_keys.env`` file (read-only compatibility).
"""

from __future__ import annotations

import os
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeverse.contracts.common import Backends

_LEGACY_KEYS_FILE = Path.home() / ".config" / "astra3d" / "gemini_keys.env"
_USER_CONFIG = Path.home() / ".config" / "codeverse" / "config.yaml"


def _lower_bound(model: type[BaseModel], field: str) -> int | None:
    """The ``ge=`` declared on ``model.field`` (None when unbounded).  Lets a flat env
    alias enforce exactly the bound its target field declares, in one place."""
    for meta in model.model_fields[field].metadata:
        lo = getattr(meta, "ge", None)
        if lo is not None:
            return int(lo)
    return None


class Binaries(BaseModel):
    blender: str = Field(default="", description="path to a Blender 4.2+/5.x binary (headless capable)")
    node: str = "node"
    gemini_cli: str = "gemini"
    claude_cli: str = "claude"
    codex_cli: str = "codex"
    agy_cli: str = "agy"
    ffmpeg: str = "ffmpeg"


class Agents(BaseModel):
    """Knobs for the CLI-backed coding agents (the subscription CLIs)."""

    codex_reasoning_effort: str = Field(
        default="high",
        description="`-c model_reasoning_effort=` passed to every `codex:` call (agent and one-shot).  "
        "One of minimal|low|medium|high|xhigh, or '' to leave the choice to ~/.codex/config.toml.  "
        "Override per id with `codex:<model>@<effort>`.")


class Render(BaseModel):
    width: int = 768
    height: int = 768
    scene_width: int = 1024
    scene_height: int = 576
    gpu: str = Field(default="auto", description="auto | on | off (headless Chrome WebGL backend)")
    sheet_cols: int = 4
    sheet_tile: int = 384


class Limits(BaseModel):
    build_timeout_s: int = 300
    render_timeout_s: int = 300
    agent_timeout_s: int = 1800
    bpy_rlimit_gb: int = 12
    # Both measured, not guessed (docs/COST.md Part III).  These size the
    # *subprocess* side (blender / node / chrome), which is bound by cores — 24
    # here — and NOT by the provider: the model-call ceiling is Rate.max_in_flight.
    # 32 concurrent blender builds of a recorded model.py peaked at 16 workers
    # (1 392 builds/min; 24 workers gave 1 168, 32 gave 1 019), so a fan-out of 12
    # agent tasks x their own builds stays inside the knee and leaves cores for the
    # render pass, whose tail is minutes long.
    max_parallel_agents: int = Field(default=12, ge=1, description="thread fan-out for agent tasks (>= 1)")
    max_parallel_builds: int = Field(default=8, ge=1, description="concurrent build subprocesses (>= 1)")
    agent_max_turns: int = Field(
        default=0,
        ge=0,
        description="hard cap on model turns per agent session (0 = the backend's own default, "
        "which is what ships: a 28-turn cap cost $0.02 more and 0.205 of a score point in its "
        "own A/B — docs/COST.md §17 — so no profile sets one; name it here if you want one).",
    )


class Rate(BaseModel):
    """Provider quota the key pool schedules against, and the model-call ceiling.

    Measured, not guessed — ``docs/COST.md`` Part III.  On this box (22 keys,
    ``gemini-3.7-flash``) ``tpm_per_key`` is the binding limit, not ``rpm_per_key``:
    a generator call averages ~42 k prompt tokens, so 1 M TPM is ~24 calls/min per
    key (528 pool-wide) while the RPM quota would allow 1 000.

    ``max_in_flight`` is a process-wide ceiling on *concurrent model calls* and is
    deliberately separate from ``Limits.max_parallel_agents`` /
    ``max_parallel_builds``: blender / node / chrome are CPU-bound and sized by
    cores, model calls are network-bound and sized by the provider.  0 = unlimited.
    """

    rpm_per_key: int = Field(default=1000, ge=1, description="requests/minute allowed per API key")
    tpm_per_key: int = Field(default=1_000_000, ge=0, description="prompt tokens/minute allowed per API key (0 = no TPM bucket)")
    max_in_flight: int = Field(
        default=64,
        ge=0,  # NEGATIVE is not "unlimited" here: 0 is.  Without this bound -5 survived config,
               # was reported as FITTING the pool budget by `3dcv doctor`, poisoned the shared
               # in-flight accounting, and finally died as a bare ValueError from
               # threading.BoundedSemaphore inside KeyPool -- at the first model call, long
               # after the workspace and spec.json were written.
        description="process-wide cap on concurrent model calls (0 = off).  Measured knee: a "
        "128-call burst of 12k-token prompts ran 29.6 calls/min at 16 in-flight, 43.9 at 32, "
        "72.9 at 64 and fell back to 47.2 at 128 (docs/COST.md Part III).")
    storm_gate: bool = Field(
        default=False,
        description="share 503 capacity-storm back-pressure across workers.  OFF: measured and it "
        "LOST — 30.0/45.4 calls/min without it vs 18.2/20.0 with it, because Gemini's 503s are "
        "intermittent rather than a clean outage, so parking every worker starves the unlucky call "
        "(docs/COST.md §21).  The mechanism and its counters are kept so the experiment is "
        "reproducible: CV3D_RATE__STORM_GATE=1, or bench/concurrency_probe.py --storm-gate.")


class Judge(BaseModel):
    """Judge payload size — what one verdict is allowed to send (docs/COST.md §3)."""

    max_px: int = Field(default=1024, description="longest edge of a montage / crop sent to the judge")
    montages: int = Field(default=3, description="max 2x2 montages per verdict")
    detail_crops: int = Field(default=2, description="max zoomed detail crops per verdict")
    samples: int = Field(default=1, description="default VLM samples per verdict")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CV3D_", env_nested_delimiter="__", extra="ignore")

    #: Flat aliases for nested knobs people actually type.  ``CV3D_MAX_IN_FLIGHT=16`` was
    #: written into three launch commands and two workflow briefs on 2026-08-24 before anyone
    #: noticed pydantic-settings only reads ``CV3D_RATE__MAX_IN_FLIGHT`` — every one of them
    #: silently ran at the default 64.  An env knob that is read by nothing is worse than no
    #: knob; both spellings now work and the doctor prints the short one.
    _FLAT_ALIASES: ClassVar[dict[str, tuple[str, str]]] = {
        "CV3D_MAX_IN_FLIGHT": ("rate", "max_in_flight"),
    }

    @model_validator(mode="after")
    def _apply_flat_aliases(self) -> Settings:
        for env, (section, field) in self._FLAT_ALIASES.items():
            raw = os.environ.get(env)
            if raw is None or raw.strip() == "":
                continue
            try:
                value = int(raw)
            except ValueError as e:
                raise ValueError(f"{env}={raw!r}: expected an integer") from e
            # plain assignment does NOT re-validate (no validate_assignment), so the target
            # field's own bound is enforced here — and the message names the variable the
            # operator actually typed, not the nested field they never heard of.
            sub = getattr(self, section)
            lo = _lower_bound(type(sub), field)
            if lo is not None and value < lo:
                raise ValueError(f"{env}={raw!r}: must be >= {lo}")
            setattr(sub, field, value)
        return self

    runs_dir: Path = Field(default=Path("runs"))
    cache_dir: Path = Field(default=Path.home() / ".cache" / "codeverse")
    binaries: Binaries = Field(default_factory=Binaries)
    agents: Agents = Field(default_factory=Agents)
    render: Render = Field(default_factory=Render)
    limits: Limits = Field(default_factory=Limits)
    rate: Rate = Field(default_factory=Rate)
    judge: Judge = Field(default_factory=Judge)

    gemini_api_keys: list[str] = Field(default_factory=list)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""

    # Backend-role defaults: the literals live in ONE place — contracts/common.py::Backends
    # (which also documents why the judge default is the pro tier).
    default_planner: str = Field(default_factory=lambda: Backends().planner)
    default_generator: str = Field(default_factory=lambda: Backends().generator)
    default_judge: str = Field(default_factory=lambda: Backends().judge)
    default_captioner: str = Field(default_factory=lambda: Backends().captioner)
    default_candidates: int = Field(default=1, description="best-of-N baseline candidates (tracks read it)")

    # Cost dial: one name that sets model-per-role, judge samples, rounds, candidates,
    # turn cap, montage size and the texture pass together (codeverse/cost/profiles.py).
    model_timeout_s: float = Field(
        default=300.0,
        description="HTTP read timeout for ONE API model call (per attempt; retries multiply it).  "
        "Raise it when the provider is degraded and big structured calls (planner, judge) time out "
        "before they answer: CV3D_MODEL_TIMEOUT_S=900.")
    profile: str = Field(default="balanced", description="economy | balanced | quality")
    cost_ledger: bool = Field(default=True, description="append one priced row per model call "
                              "to the run's telemetry/cost.jsonl (or a per-process log)")

    # ------------------------------------------------------------------ helpers
    def backends(self, **overrides: str | None) -> Backends:
        """The ``Backends`` for a new Spec from the settings defaults.  Keyword
        overrides (``planner`` / ``generator`` / ``judge`` / ``captioner``) win
        when truthy; ``None`` / ``""`` falls through to the default."""
        base: dict[str, str] = {
            "planner": self.default_planner,
            "generator": self.default_generator,
            "judge": self.default_judge,
            "captioner": self.default_captioner,
        }
        unknown = set(overrides) - set(base)
        if unknown:
            raise TypeError(f"unknown backend role(s): {sorted(unknown)} (roles: {sorted(base)})")
        for role, value in overrides.items():
            if value:
                base[role] = value
        return Backends(**base)

    def apply_profile(self, name: str | None = None, *, force: bool = False) -> Any:
        """Set every profile-controlled default on this Settings object and return
        the :class:`~codeverse.cost.profiles.Profile`.

        A field the user stated themselves (config file / ``CV3D_*`` env) is left
        alone unless ``force`` — so a profile is a *default* dial, while
        ``3dcv make --profile X`` (which forces) is an instruction.  The CLI's own
        flags are applied after this and always win."""
        from codeverse.cost.profiles import get_profile

        p = get_profile(name or self.profile)
        stated = set() if force else set(self.model_fields_set)

        def put(field: str, value: Any) -> None:
            if field not in stated and value not in (None, ""):
                setattr(self, field, value)

        def put_section(section: str, values: dict[str, Any]) -> None:
            """Per-FIELD statedness inside a sub-model.

            ``Settings.model_fields_set`` is SECTION-granular — pydantic marks the whole
            ``judge`` sub-model as set when any ``CV3D_JUDGE__*`` is present — so testing
            it here let one stated field suppress the profile's every other dial:
            ``CV3D_PROFILE=quality CV3D_JUDGE__MAX_PX=800`` judged at n=1 instead of n=3,
            silently, while `3dcv make` printed "judge sigma 0.017 at n=3".  The sub-model
            has its own ``model_fields_set``, which is the per-field answer.  The values a
            profile writes are DEFAULTS, not statements, so the field-set is restored
            afterwards and re-applying a profile stays idempotent.
            """
            sub = getattr(self, section)
            keep = set() if force else set(sub.model_fields_set)
            update = {k: v for k, v in values.items() if k not in keep}
            if not update:
                return
            new_sub = sub.model_copy(update=update)
            new_sub.__pydantic_fields_set__ = keep
            setattr(self, section, new_sub)

        self.profile = p.name
        put("default_generator", p.generator)
        put("default_planner", p.planner)
        put("default_judge", p.judge)
        put("default_captioner", p.captioner)
        put("default_candidates", p.candidates)
        put_section("judge", {"max_px": p.judge_max_px, "montages": p.judge_montages,
                              "detail_crops": p.judge_detail_crops, "samples": p.judge_samples})
        put_section("limits", {"agent_max_turns": p.max_turns})
        return p

    def resolve_blender(self) -> str:
        if self.binaries.blender and Path(self.binaries.blender).exists():
            return self.binaries.blender
        for cand in ("blender-5.0", "blender", "blender-5.1", "blender-4.2"):
            p = shutil.which(cand)
            if p:
                return p
        return ""

    def runtime_js_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "runtime_js"


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _legacy_gemini_keys() -> list[str]:
    """Read ``GEMINI_API_KEYS`` from the owner's astra3d keys file (csv, quoted)."""
    if not _LEGACY_KEYS_FILE.is_file():
        return []
    m = re.search(r'GEMINI_API_KEYS="?([^"\n]+)"?', _LEGACY_KEYS_FILE.read_text())
    if not m:
        return []
    return [k.strip() for k in m.group(1).split(",") if k.strip()]


def _env_gemini_keys() -> list[str]:
    csv = os.environ.get("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in csv.split(",") if k.strip()]
    single = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if single and single not in keys:
        keys.append(single)
    return keys


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the singleton Settings (cached; call ``get_settings.cache_clear()`` in tests)."""
    data: dict = {}
    for p in (_USER_CONFIG, Path("codeverse.yaml")):
        deep = _load_yaml(p)
        data.update(deep)
    s = Settings(**data)
    if not s.gemini_api_keys:
        s.gemini_api_keys = _env_gemini_keys() or _legacy_gemini_keys()
    if not s.anthropic_api_key:
        s.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not s.openai_api_key:
        s.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    # de-dupe keys preserving order
    seen: set[str] = set()
    s.gemini_api_keys = [k for k in s.gemini_api_keys if not (k in seen or seen.add(k))]
    # the dial named in config.yaml / CV3D_PROFILE, applied as a *default*: a value the
    # user stated themselves survives it.  `3dcv make --profile X` forces the same dial
    # (codeverse.cli._common.resolve_dial is the one resolver both paths go through).
    s.apply_profile(s.profile)
    return s
