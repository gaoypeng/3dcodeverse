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
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeverse.contracts.common import Backends

_LEGACY_KEYS_FILE = Path.home() / ".config" / "astra3d" / "gemini_keys.env"
_USER_CONFIG = Path.home() / ".config" / "codeverse" / "config.yaml"


class Binaries(BaseModel):
    blender: str = Field(default="", description="path to a Blender 4.2+/5.x binary (headless capable)")
    node: str = "node"
    gemini_cli: str = "gemini"
    claude_cli: str = "claude"
    codex_cli: str = "codex"
    agy_cli: str = "agy"
    ffmpeg: str = "ffmpeg"


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
    max_parallel_agents: int = 6
    max_parallel_builds: int = 3
    agent_max_turns: int = Field(
        default=0,
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

    rpm_per_key: int = Field(default=1000, description="requests/minute allowed per API key")
    tpm_per_key: int = Field(default=1_000_000, description="prompt tokens/minute allowed per API key")
    max_in_flight: int = Field(default=32, description="process-wide cap on concurrent model calls (0 = off)")
    storm_gate: bool = Field(default=True, description="share 503 capacity-storm back-pressure across workers")


class Judge(BaseModel):
    """Judge payload size — what one verdict is allowed to send (docs/COST.md §3)."""

    max_px: int = Field(default=1024, description="longest edge of a montage / crop sent to the judge")
    montages: int = Field(default=3, description="max 2x2 montages per verdict")
    detail_crops: int = Field(default=2, description="max zoomed detail crops per verdict")
    samples: int = Field(default=1, description="default VLM samples per verdict")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CV3D_", env_nested_delimiter="__", extra="ignore")

    runs_dir: Path = Field(default=Path("runs"))
    cache_dir: Path = Field(default=Path.home() / ".cache" / "codeverse")
    binaries: Binaries = Field(default_factory=Binaries)
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

        self.profile = p.name
        put("default_generator", p.generator)
        put("default_planner", p.planner)
        put("default_judge", p.judge)
        put("default_captioner", p.captioner)
        put("default_candidates", p.candidates)
        if "judge" not in stated:
            self.judge = self.judge.model_copy(update={
                "max_px": p.judge_max_px, "montages": p.judge_montages,
                "detail_crops": p.judge_detail_crops, "samples": p.judge_samples})
        if "limits" not in stated:
            self.limits = self.limits.model_copy(update={"agent_max_turns": p.max_turns})
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
