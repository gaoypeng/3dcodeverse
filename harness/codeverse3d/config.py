"""Harness settings: keys, binaries, directories, defaults.

Resolution order (later wins): built-in defaults < ``~/.config/3dcodeverse/config.yaml``
< ``./3dcodeverse.yaml`` < environment variables (``C3D_*``).  Secrets are never
written to run records.

Gemini keys: ``GEMINI_API_KEYS`` (csv), ``GEMINI_API_KEY`` or ``~/.config/astra3d/gemini_keys.env``.

Every ``C3D_*`` knob is a field here, read through ``get_settings()``.  A field ``x`` is
``C3D_X`` (nested ``section.x`` is ``C3D_SECTION__X``, plus :data:`Settings.FLAT`); an on/off
switch is a :data:`Flag`.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    model_validator,
)
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeverse3d.contracts.agent import AGENT_TIMEOUT_S
from codeverse3d.contracts.common import Backends

_LEGACY_KEYS_FILE = Path.home() / ".config" / "astra3d" / "gemini_keys.env"
_USER_CONFIG = Path.home() / ".config" / "3dcodeverse" / "config.yaml"


log = logging.getLogger(__name__)


def _or_default(value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo) -> Any:
    try:
        return handler(value.strip() if isinstance(value, str) else value)
    except ValidationError:
        log.warning("C3D_%s=%r does not parse; using the default", (info.field_name or "?").upper(), value)
        raise PydanticUseDefault() from None


#: an on/off switch (on/off, 1/0, true/false, yes/no); anything else keeps the default
Flag = Annotated[bool, WrapValidator(_or_default)]
Count = Annotated[int, Field(ge=0), WrapValidator(_or_default)]


#: the supported Blender (5.2 LTS); ``3dcodeverse doctor`` fails anything older.
BLENDER_MIN: tuple[int, int] = (5, 2)


class Binaries(BaseModel):
    blender: str = Field(default="", description="path to the Blender 5.2 LTS binary (headless capable)")
    node: str = "node"
    claude_cli: str = "claude"
    codex_cli: str = "codex"
    agy_cli: str = "agy"


class Agents(BaseModel):
    codex_reasoning_effort: str = Field(
        default="high",
        description="`-c model_reasoning_effort=` passed to every `codex:` call (agent and one-shot).  "
        "One of minimal|low|medium|high|xhigh, or '' to leave the choice to ~/.codex/config.toml.  "
        "Override per id with `codex:<model>@<effort>`.")
    claude_effort: str = Field(
        default="xhigh",
        description="`--effort` passed to every `claude-code:` session (low|medium|high|xhigh|max; '' in the "
        "config file leaves it to the CLI).  A harness session reads no user settings (`--setting-sources project`), so the "
        "effort is the harness's to state; xhigh is what sessions ran at while they still inherited the "
        "owner's ~/.claude/settings.json (until 2026-09-22).")


class Render(BaseModel):
    width: int = 768
    height: int = 768
    scene_width: int = 1024
    scene_height: int = 576
    gpu: Annotated[Literal["auto", "on", "off"], BeforeValidator(lambda v: str(v).strip().lower())] = Field(
        default="auto", description="headless Chrome WebGL backend (C3D_RENDER__GPU, or the flat C3D_RENDER_GPU "
        "the node side reads too)")
    sheet_cols: int = 4
    sheet_tile: int = 384
    # scene_blender pictures (spatial/render_blender.py).  Cycles CUDA + GPU OIDN is the fast path;
    # EEVEE needs the Mesa d3d12 env (spatial/gl_render.GPU_ENV) or lands on llvmpipe.
    # OptiX is unavailable under WSL2 and probing it crashes Blender: CUDA only.
    blender_engine: Annotated[Literal["cycles", "eevee"], BeforeValidator(lambda v: str(v).strip().lower())] = Field(
        default="cycles", description="judged-frame engine for scene_blender (C3D_RENDER__BLENDER_ENGINE)")
    blender_frame_time_limit_s: float = Field(
        default=20.0, ge=0, description="Cycles time cap per judged frame, seconds (0 = none): a heavy scene stops "
        "sampling early instead of eating the render timeout (C3D_RENDER__BLENDER_FRAME_TIME_LIMIT_S)")
    blender_samples: int = Field(
        default=32, ge=1, description="Cycles samples (adaptive + OIDN) / EEVEE TAA samples "
        "(C3D_RENDER__BLENDER_SAMPLES)")
    blender_device: Annotated[Literal["gpu", "cpu"], BeforeValidator(lambda v: str(v).strip().lower())] = Field(
        default="gpu", description="'gpu' takes a machine-wide GPU slot and falls back to the CPU when none frees "
        "within blender_gpu_wait_s or the GPU render dies; 'cpu' never touches the GPU")
    blender_gpu_slots: int = Field(
        default=2, ge=1, description="machine-wide concurrent GPU Blender renders (flock'd files under "
        "<cache_dir>/slots/blender_gpu/)")
    blender_gpu_wait_s: float = Field(
        default=60.0, ge=0, description="how long a render waits for a GPU slot before it renders on the CPU "
        "(~4x slower, measured on 5.2.2)")


class Limits(BaseModel):
    build_timeout_s: int = 300
    render_timeout_s: int = 300
    agent_timeout_s: int = AGENT_TIMEOUT_S
    bpy_rlimit_gb: int = 12
    # These size the subprocess side (blender / node / chrome, bound by cores); the model-call
    # ceiling is Rate.max_in_flight.
    max_parallel_agents: int = Field(default=12, ge=1, description="thread fan-out for agent tasks (>= 1)")
    max_parallel_builds: int = Field(default=8, ge=1, description="concurrent build subprocesses (>= 1)")


class Rate(BaseModel):
    """The model-call ceiling and the 503 hedge.

    ``max_in_flight`` is machine-wide (flock'd slot files under ``<cache_dir>/slots/``), separate
    from the core-bound ``Limits`` fan-outs.  0 = unlimited.  A vendor CLI session takes a key
    but no slot.
    """

    max_in_flight: int = Field(
        default=96,
        ge=0,  # negative is not "unlimited" (0 is); a negative would poison the slot accounting
        description="machine-wide cap on concurrent model calls (0 = off): every process draws "
        "from the same N slot files, so the machine never exceeds the largest N any process runs "
        "with.  Measured knee: a 128-call burst of 12k-token prompts ran 29.6 calls/min at 16 "
        "in-flight, 43.9 at 32, 72.9 at 64 and fell back to 47.2 at 128 (docs/COST.md Part III) — with 22 "
        "keys; raised 64 → 96 on 2026-10-15 for the 30-key pool after iteration 0 of the argon loop hit the "
        "64 cap 172 times at launch while the keys themselves showed no 429 pressure (12 concurrent calls on "
        "one key: 0 × 429; re-measure the knee when the loops run two tracks at once).")
    hedge: int = Field(
        default=2, ge=1,
        description="keys a retry is raced on once a call has met its first 503 (1 = off).  Measured "
        "2026-08-26: a failed 503 costs the 21-50 s round-trip the provider holds before rejecting, "
        "storm streaks average 4.7 attempts, and a 503 bills nothing — so the hedge is free while it "
        "storms and wastes one call only when both keys answer (docs/COST.md §27).  "
        "C3D_RATE__HEDGE=1 for the A/B.")


class Judge(BaseModel):
    max_px: int = Field(default=1024, description="longest edge of a montage / crop sent to the judge")
    montages: int = Field(default=5, description="max 2x2 montages per verdict (14-view rig, D47)")
    detail_crops: int = Field(default=2, description="max zoomed detail crops per verdict")
    samples: int = Field(default=1, description="default VLM samples per verdict")
    slices: Literal["on-error", "off"] = Field(
        default="on-error",
        description="conditional cross-section slices (D48, C3D_JUDGE__SLICES): 'on-error' appends ≤2 "
        "interior slice images + one provenance-elicitation sentence to an object-track verdict whose "
        "connectivity gate carries an ERROR; clean rounds build a byte-identical payload either way.  "
        "Slice render is local CPU; no profile touches this dial (the channel measured ≤ $0).")
    thinking: Annotated[Literal["off", "low", "medium", "high"], BeforeValidator(lambda v: str(v).strip().lower())] = Field(
        default="medium",
        description="VLM and pairwise judges' thinking level (C3D_JUDGE__THINKING; models/gemini.THINKING_BUDGET).  "
        "The default gemini-4-argon thinks only from 'medium' "
        "(4096) — measured 2026-10-14 on 19 scene/object rounds: argon@medium within-sd .012 vs .029 at low, "
        "same cost, same mean (D105).  `3dcodeverse judge --thinking` replays with another level.")


class Placement(BaseModel):
    """The scene_placement gate's thresholds (spatial/scene_placement.py), metres / fractions.
    The JS measurement (host_placement.mjs) keeps its own CONTACT / FLOATING / SUNK constants:
    these decide severities only."""

    contact_m: float = Field(default=0.02, ge=0, description="foot within this of a surface = seated")
    floating_outdoor_m: float = Field(default=0.05, ge=0, description="gap above which an outdoor asset floats")
    floating_indoor_m: float = Field(default=0.02, ge=0, description="gap above which an indoor asset floats")
    floating_error_m: float = Field(default=0.08, ge=0, description="a gap this large is an ERROR (else WARN)")
    floating_error_count: int = Field(default=2, ge=0, description="more floating assets than this = ERROR")
    sunk_m: float = Field(default=0.10, ge=0, description="burial deeper than this = WARN")
    sunk_error_m: float = Field(default=0.20, ge=0, description="burial deeper than this AND > sunk_error_frac of the height = ERROR")
    sunk_error_frac: float = Field(default=0.20, ge=0, le=1)
    overlap_warn: float = Field(default=0.10, ge=0, le=1, description="AABB overlap (fraction of the smaller box) = interpenetration WARN")
    overlap_error: float = Field(default=0.35, ge=0, le=1, description="… = interpenetration ERROR")
    free_exempt_warn: float = Field(default=0.25, ge=0, le=1, description="share of placed assets tagged placement='free' above which the gate WARNs (the agent is hiding from it)")
    free_exempt_error: float = Field(default=0.50, ge=0, le=1, description="… above which it is an ERROR")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="C3D_", env_nested_delimiter="__", extra="ignore",
                                      env_ignore_empty=True)

    #: nested knobs with a flat spelling; the flat one wins
    FLAT: ClassVar[dict[str, tuple[str, str]]] = {
        "C3D_MAX_IN_FLIGHT": ("rate", "max_in_flight"),
        "C3D_RENDER_GPU": ("render", "gpu"),
    }

    @classmethod
    def settings_customise_sources(cls, settings_cls: Any, init_settings: Any, env_settings: Any,
                                   dotenv_settings: Any, file_secret_settings: Any) -> tuple[Any, ...]:
        """``C3D_*`` beats the yaml files (passed as init kwargs, which would otherwise rank first)."""
        return env_settings, init_settings, dotenv_settings, file_secret_settings

    @model_validator(mode="before")
    @classmethod
    def _flat_spellings(cls, data: Any) -> Any:
        for env, (section, name) in cls.FLAT.items():
            raw = os.environ.get(env, "").strip()
            if raw and isinstance(data, dict):
                sub = data.get(section) or {}
                data[section] = {**(sub.model_dump() if isinstance(sub, BaseModel) else sub), name: raw}
        return data

    runs_dir: Path = Field(default=Path("runs"))
    cache_dir: Path = Field(default=Path.home() / ".cache" / "codeverse3d")
    binaries: Binaries = Field(default_factory=Binaries)
    agents: Agents = Field(default_factory=Agents)
    render: Render = Field(default_factory=Render)
    limits: Limits = Field(default_factory=Limits)
    rate: Rate = Field(default_factory=Rate)
    judge: Judge = Field(default_factory=Judge)
    placement: Placement = Field(default_factory=Placement)

    gemini_api_keys: list[str] = Field(default_factory=list)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""

    default_planner: str = Field(default_factory=lambda: Backends().planner)
    default_generator: str = Field(default_factory=lambda: Backends().generator)
    default_judge: str = Field(default_factory=lambda: Backends().judge)
    default_captioner: str = Field(default_factory=lambda: Backends().captioner)
    ray_engine: Literal["numpy", "embree"] = Field(
        default="embree",
        description="ray engine of the deterministic inside tests (connectivity penetration, pose sweep): Embree via "
        "embreex, or trimesh's own ray-triangle intersector (numpy: ~100x slower, memory grows with rays x "
        "triangles) for a GLB on which embreex 4 segfaults.  C3D_RAY_ENGINE.",
    )

    model_timeout_s: float = Field(
        default=1200.0,
        description="HTTP read timeout for ONE API model call (per attempt).  It is a CEILING: the "
        "call ends when the answer does, and gemini.py clips each attempt to the retry budget "
        "actually left.  300 s was the binding wall once max_output_tokens went to the model's "
        "65 536 ceiling — measured 2026-08-27, output streams at 145 tok/s p50 and 60 tok/s p10, so "
        "a 55 800-token plan needs ~930 s and died at the socket, its tokens billed and discarded. "
        "Owner's rule 2026-08-27: time may be generous, errors may not.")

    # Per-model prompt/skill variants: overlays under prompts/variants/<name>/ and
    # skills/variants/<name>/, chosen by name or exact generator id, never by regex.
    variant: str = Field(default="", description="prompt/skill variant for every run of this process "
                         "(C3D_VARIANT; a name under codeverse3d/prompts/variants/); '' = base")
    variants: dict[str, str] = Field(default_factory=dict, description="exact generator id → variant name, "
                                     "e.g. {'agy:gemini-4-argon': 'argon'} (yaml mapping, or env as JSON "
                                     "C3D_VARIANTS='{\"agy:gemini-4-argon\": \"argon\"}'); consulted when "
                                     "`variant` is empty, by the id as typed and then by '<kind>:<served model>'")

    # ------------------------------------------------------------------ switches
    # tracks/plan_features.py derives the live C3D_* names from these fields.
    skills: Flag = Field(default=True, description="route, materialise and measure skill bundles "
                         "(D81, on since 2026-09-22); C3D_SKILLS=0 is an A/B's no-skills arm")
    skills_max: Count = Field(
        default=5, description="bundles attached to one session (skills design §5.2 law 1)")
    skills_unverified: Flag = Field(default=True, description="also route the bundles labelled "
                                    "inherited-unverified (with it all 17 route; the cap still holds)")
    skills_only: str = Field(default="", description="comma list: the router considers ONLY these "
                             "bundles, so an effect A/B can attribute its delta to one; an unknown name "
                             "routes nothing, which shows up at once as a variant equal to its control")
    skills_dir: Path | None = Field(default=None, description="another skill library (tests, the live "
                                    "CLI smoke, a private overlay)")
    plan_brief: Flag = Field(default=True, description="expand the engineering brief before planning "
                             "(object tracks)")
    plan_restart: Flag = Field(default=True, description="re-sample a degenerate plan from the original "
                               "prompt instead of editing it in context (docs/COST.md §30)")
    reference_diff: Flag = Field(default=True, description="the judge's reference-image mismatch pass")
    zone_layouts: Flag = Field(default=True, description="scene track: the L2 zone-layout planner calls")
    axis_repair: Flag = Field(default=True, description="articulated track: rewrite a joint axis the "
                              "measured motion proves wrong, before the sweep renders")
    settle: Flag = Field(default=True, description="scene driver: the boot-time auto-seat")
    camera_repair: Flag = Field(default=True, description="scene driver: move a camera out of geometry "
                                "(zero triggers on a healthy battery; the case it exists for is fatal)")
    post: Flag = Field(default=True, description="scene pictures: the GTAO + bloom + grade post chain")
    stream: Flag = Field(default=True, description="Gemini: stream replies with inter-chunk stall "
                         "detection (a hung buffered read held the socket for the whole attempt)")
    ipv4: Flag = Field(default=True, description="Gemini: bind the transport to IPv4 (the WSL2 IPv6 "
                       "path dropped responses silently)")
    runtime_js: Path | None = Field(default=None, description="the node runtime directory; a "
                                    "non-editable install must point this at a checkout")

    # ------------------------------------------------------------------ helpers
    def backends(self, **overrides: str | None) -> Backends:
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

    def resolve_blender(self) -> str:
        """The configured binary, else the first Blender on PATH; a configured-but-missing one is ``""``."""
        if self.binaries.blender:
            return shutil.which(self.binaries.blender) or ""
        for cand in ("blender-5.2", "blender"):
            p = shutil.which(cand)
            if p:
                return p
        return ""

    def runtime_js_dir(self) -> Path:
        """The Node runtime dir; ``C3D_RUNTIME_JS`` overrides it (required for a non-editable install)."""
        d = self.runtime_js.expanduser() if self.runtime_js else Path(__file__).resolve().parent.parent / "runtime_js"
        if not d.is_dir():
            raise RuntimeError(
                f"runtime_js not found at {d} — install the harness editable (pip install -e harness) "
                "or set C3D_RUNTIME_JS to a 3dcodeverse/harness/runtime_js checkout (with node_modules installed)"
            )
        return d


def _load_yaml(path: Path) -> dict:
    """A config file's mapping; anything else is a ``ValueError`` naming the file."""
    if not path.is_file():
        return {}
    try:
        with path.open() as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping of settings, got a {type(data).__name__}")
    return data


def _legacy_gemini_keys() -> list[str]:
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


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data: dict = {}
    for p in (_USER_CONFIG, Path("3dcodeverse.yaml")):
        data = _deep_merge(data, _load_yaml(p))
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
    return s
