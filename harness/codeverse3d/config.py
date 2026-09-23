"""Harness settings: keys, binaries, directories, defaults.

Resolution order (later wins): built-in defaults < ``~/.config/3dcodeverse/config.yaml``
< ``./3dcodeverse.yaml`` < environment variables (``C3D_*``).  Secrets are never
written to run records.  The names before D78 (``~/.config/codeverse/config.yaml``,
``./codeverse.yaml``, ``CV3D_*``) are still read, each under its new name.

Gemini keys: ``GEMINI_API_KEYS`` (csv) or ``GEMINI_API_KEY`` or the owner's
``~/.config/astra3d/gemini_keys.env`` file (read-only compatibility).

One switch grammar: every ``C3D_*`` knob the harness reads is a field here, read through
``get_settings()`` at the call that uses it — no module reads the environment by hand.  A
field ``x`` is ``C3D_X`` (a nested ``section.x`` is ``C3D_SECTION__X``, plus the flat
spellings in :data:`Settings.FLAT`); an on/off switch is a :data:`Flag`.
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

from codeverse3d.contracts.common import Backends

_LEGACY_KEYS_FILE = Path.home() / ".config" / "astra3d" / "gemini_keys.env"
_USER_CONFIG = Path.home() / ".config" / "3dcodeverse" / "config.yaml"
#: the file names before D78 (2026-09-22): still read, each UNDER its new name (a new file wins per setting)
_LEGACY_USER_CONFIG = Path.home() / ".config" / "codeverse" / "config.yaml"


log = logging.getLogger(__name__)


def _or_default(value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo) -> Any:
    """A switch that does not parse warns and keeps its DEFAULT (D44 c): a typo in a bench
    command runs the arm that never set it — not a crash mid-battery, and not the variant."""
    try:
        return handler(value.strip() if isinstance(value, str) else value)
    except ValidationError:
        log.warning("C3D_%s=%r does not parse; using the default", (info.field_name or "?").upper(), value)
        raise PydanticUseDefault() from None


#: an on/off switch: on/off, 1/0, true/false, yes/no (any case, t/f and y/n too); anything
#: else warns and keeps the field's default
Flag = Annotated[bool, WrapValidator(_or_default)]
#: a switch that is a count (>= 0); anything else warns and keeps the field's default
Count = Annotated[int, Field(ge=0), WrapValidator(_or_default)]


class Binaries(BaseModel):
    blender: str = Field(default="", description="path to a Blender 4.2+/5.x binary (headless capable)")
    node: str = "node"
    gemini_cli: str = "gemini"
    claude_cli: str = "claude"
    codex_cli: str = "codex"
    agy_cli: str = "agy"


class Agents(BaseModel):
    """Knobs for the CLI-backed coding agents (the subscription CLIs)."""

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
    agent_max_turns: Count = Field(
        default=0,
        description="hard cap on model turns per agent session (0 = the backend's own default, "
        "which is what ships: a 28-turn cap cost $0.02 more and 0.205 of a score point in its "
        "own A/B — docs/COST.md §17 — so no profile sets one; name it here or as "
        "C3D_AGENT_MAX_TURNS if you want one).",
    )
    seed_recipes: Flag = Field(
        default=True,
        description="graphics / glsl_shader: paste the cookbook recipes the brief calls for "
        "(curtain / aurora / stars / bokehSoft / dropsLayer + the hash / noise / fbm helpers "
        "they use) into the harness-owned, read-only src/recipes.glsl BEFORE the baseline session "
        "(tracks/graphics.py:seed_recipes; pasted above src/common.glsl at build time).  "
        "ON by default: measured 2026-08-26 (refs_v2_graphics, aurora brief, gemini-3.7-flash) "
        "the prompt carried the verified curtain() recipe five times and the agent used it zero "
        "times — round 0 was again a comb of bars (comb_artefact, 0.33); and (eval/bench/out/seed_v1) "
        "recipes seeded into the agent's own common.glsl were overwritten before the end of the run.  "
        "`C3D_SEED_RECIPES=0` is the control arm.",
    )


class Rate(BaseModel):
    """The model-call ceiling and the 503 hedge (``docs/COST.md`` Part III).

    ``max_in_flight`` is a MACHINE-WIDE ceiling on the harness's own concurrent model calls
    (flock'd slot files under ``<cache_dir>/slots/``, shared by every process — COST §23)
    and is deliberately separate from ``Limits.max_parallel_agents`` /
    ``max_parallel_builds``: blender / node / chrome are CPU-bound and sized by cores, model
    calls are network-bound and sized by the provider.  0 = unlimited.  A vendor CLI
    session takes a key from the pool but no slot.  There is no RPM / TPM quota here any more:
    since the api-agent went the harness's own calls peaked at 2.6 % of one key's RPM
    and 3.9 % of its TPM, so the buckets they fed never engaged (COST §19).
    """

    max_in_flight: int = Field(
        default=64,
        ge=0,  # NEGATIVE is not "unlimited" here: 0 is.  Without this bound -5 survived config,
               # was reported as FITTING the pool budget by `3dcode doctor`, poisoned the shared
               # in-flight accounting, and finally died as a bare ValueError from
               # threading.BoundedSemaphore inside KeyPool -- at the first model call, long
               # after the workspace and spec.json were written.
        description="machine-wide cap on concurrent model calls (0 = off): every process draws "
        "from the same N slot files, so the machine never exceeds the largest N any process runs "
        "with.  Measured knee: a 128-call burst of 12k-token prompts ran 29.6 calls/min at 16 "
        "in-flight, 43.9 at 32, 72.9 at 64 and fell back to 47.2 at 128 (docs/COST.md Part III).")
    hedge: int = Field(
        default=2, ge=1,
        description="keys a retry is raced on once a call has met its first 503 (1 = off).  Measured "
        "2026-08-26: a failed 503 costs the 21-50 s round-trip the provider holds before rejecting, "
        "storm streaks average 4.7 attempts, and a 503 bills nothing — so the hedge is free while it "
        "storms and wastes one call only when both keys answer (docs/COST.md §27).  "
        "C3D_RATE__HEDGE=1 for the A/B.")


class Judge(BaseModel):
    """Judge payload size — what one verdict is allowed to send (docs/COST.md §3)."""

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


class Settings(BaseSettings):
    # env_ignore_empty: ``C3D_X=`` is unset, never "" (an empty C3D_CACHE_DIR was the cwd)
    model_config = SettingsConfigDict(env_prefix="C3D_", env_nested_delimiter="__", extra="ignore",
                                      env_ignore_empty=True)

    #: Nested knobs that also have a flat spelling — the name the docs, the node side and the
    #: A/B commands type.  ``C3D_MAX_IN_FLIGHT=16`` went into three launch commands on
    #: 2026-08-24 before anyone noticed only ``C3D_RATE__MAX_IN_FLIGHT`` was read.  The flat
    #: one wins when both are set; the nested field validates it.
    FLAT: ClassVar[dict[str, tuple[str, str]]] = {
        "C3D_MAX_IN_FLIGHT": ("rate", "max_in_flight"),
        "C3D_RENDER_GPU": ("render", "gpu"),
        "C3D_SEED_RECIPES": ("limits", "seed_recipes"),
        "C3D_AGENT_MAX_TURNS": ("limits", "agent_max_turns"),
    }

    @classmethod
    def settings_customise_sources(cls, settings_cls: Any, init_settings: Any, env_settings: Any,
                                   dotenv_settings: Any, file_secret_settings: Any) -> tuple[Any, ...]:
        """``C3D_*`` beats the yaml files (``get_settings`` passes those as init kwargs, which
        pydantic-settings ranks FIRST): a switch in a config file must never override the
        environment an A/B arm runs under."""
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

    # Cost dial: one name that sets model-per-role, judge samples, rounds, candidates
    # and the texture pass together (codeverse3d/cost/profiles.py).
    model_timeout_s: float = Field(
        default=1200.0,
        description="HTTP read timeout for ONE API model call (per attempt).  It is a CEILING: the "
        "call ends when the answer does, and gemini.py clips each attempt to the retry budget "
        "actually left.  300 s was the binding wall once max_output_tokens went to the model's "
        "65 536 ceiling — measured 2026-08-27, output streams at 145 tok/s p50 and 60 tok/s p10, so "
        "a 55 800-token plan needs ~930 s and died at the socket, its tokens billed and discarded. "
        "Owner's rule 2026-08-27: time may be generous, errors may not.")
    profile: str = Field(default="balanced", description="economy | balanced | quality")

    # ------------------------------------------------------------------ switches
    # The A/B switches and runtime overrides, each read as C3D_<NAME> at the call that uses
    # it.  An A/B arm is a child process with its own environment (eval/bench/ab_plan.py), and
    # tracks/plan_features.py derives which C3D_* names are live from these fields.
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
    scoped_parts: Flag = Field(default=True, description="per-part scoped baseline sessions for an object "
                               "of 8+ parts")
    reference_diff: Flag = Field(default=True, description="the judge's reference-image mismatch pass")
    zone_layouts: Flag = Field(default=True, description="scene track: the L2 zone-layout planner calls")
    scene_textures: Flag = Field(default=False, description="scene track: generate a texture pack before "
                                 "env/zones and describe it in both prompts.  OFF: an image-model call per "
                                 "run, unmeasured — though 4 of 5 scored cells of scene_baseline "
                                 "(2026-09-05) complained about a flat, untextured ground")
    axis_repair: Flag = Field(default=True, description="articulated track: rewrite a joint axis the "
                              "measured motion proves wrong, before the sweep renders")
    settle: Flag = Field(default=True, description="scene driver: the boot-time auto-seat")
    camera_repair: Flag = Field(default=True, description="scene driver: move a camera out of geometry "
                                "(zero triggers on a healthy battery; the case it exists for is fatal)")
    auto_exposure: Flag = Field(default=False, description="scene driver: bounded scene-wide exposure "
                                "into the healthy band")
    post: Flag = Field(default=True, description="scene pictures: the GTAO + bloom + grade post chain")
    stream: Flag = Field(default=True, description="Gemini: stream replies with inter-chunk stall "
                         "detection (a hung buffered read held the socket for the whole attempt)")
    ipv4: Flag = Field(default=True, description="Gemini: bind the transport to IPv4 (the WSL2 IPv6 "
                       "path dropped responses silently)")
    runtime_js: Path | None = Field(default=None, description="the node runtime directory; a "
                                    "non-editable install must point this at a checkout")

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
        the :class:`~codeverse3d.cost.profiles.Profile`.

        A field the user stated themselves (config file / ``C3D_*`` env) is left
        alone unless ``force`` — so a profile is a *default* dial, while
        ``3dcode make --profile X`` (which forces) is an instruction.  The CLI's own
        flags are applied after this and always win."""
        from codeverse3d.cost.profiles import get_profile

        p = get_profile(name or self.profile)
        stated = set() if force else set(self.model_fields_set)

        def put(field: str, value: Any) -> None:
            if field not in stated and value not in (None, ""):
                setattr(self, field, value)

        def put_section(section: str, values: dict[str, Any]) -> None:
            """Per-FIELD statedness inside a sub-model.

            ``Settings.model_fields_set`` is SECTION-granular — pydantic marks the whole
            ``judge`` sub-model as set when any ``C3D_JUDGE__*`` is present — so testing
            it here let one stated field suppress the profile's every other dial:
            ``C3D_PROFILE=quality C3D_JUDGE__MAX_PX=800`` judged at n=1 instead of n=3,
            silently, while `3dcode make` printed "judge sigma 0.017 at n=3".  The sub-model
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
        put_section("judge", {"samples": p.judge_samples})
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
        """The Node runtime (runtime_js/) — repo-relative in an editable install.

        ``C3D_RUNTIME_JS`` overrides the location (the wheel does not package
        runtime_js, so a non-editable install MUST point this at a checkout).
        Missing dir → a loud error at first use instead of a cryptic node crash."""
        d = self.runtime_js.expanduser() if self.runtime_js else Path(__file__).resolve().parent.parent / "runtime_js"
        if not d.is_dir():
            raise RuntimeError(
                f"runtime_js not found at {d} — install the harness editable (pip install -e harness) "
                "or set C3D_RUNTIME_JS to a 3dcodeverse/harness/runtime_js checkout (with node_modules installed)"
            )
        return d


def _load_yaml(path: Path) -> dict:
    """A config file's mapping; a file that is not one is a ``ValueError`` naming it (the CLI
    reports that as a bad configuration — a YAML syntax error or a top-level list used to
    escape every command as a raw traceback)."""
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


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge — ``overlay`` wins per SETTING, not per section.

    ``dict.update`` replaced a whole sub-dict, so a project ``./3dcodeverse.yaml`` that
    merely NAMED a section silently dropped every sibling key the user had set in
    ``~/.config/3dcodeverse/config.yaml`` — those keys fell back to the built-in Field
    defaults, not to the user's values (2026-08-24: a user's per-key TPM quota was lost to
    a project file that set only the in-flight cap).  docs/INSTALL.md §8.3 documents the
    order as "built-in defaults < user config < project config < env", which every reader
    takes as per-setting.
    """
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the singleton Settings (cached; call ``get_settings.cache_clear()`` in tests)."""
    data: dict = {}
    for p in (_LEGACY_USER_CONFIG, _USER_CONFIG, Path("codeverse.yaml"), Path("3dcodeverse.yaml")):
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
    # the dial named in config.yaml / C3D_PROFILE, applied as a *default*: a value the
    # user stated themselves survives it.  `3dcode make --profile X` forces the same dial
    # (codeverse3d.cli._common.resolve_dial is the one resolver both paths go through).
    s.apply_profile(s.profile)
    return s
