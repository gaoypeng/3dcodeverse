"""Harness settings: keys, binaries, directories, defaults.

Resolution order (later wins): built-in defaults < ``~/.config/3dcodeverse/config.yaml``
< ``./3dcodeverse.yaml`` < environment variables (``C3D_*``).  Secrets are never
written to run records.  The names before D78 (``~/.config/codeverse/config.yaml``,
``./codeverse.yaml``, ``CV3D_*``) are still read, each under its new name.

Gemini keys: ``GEMINI_API_KEYS`` (csv) or ``GEMINI_API_KEY`` or the owner's
``~/.config/astra3d/gemini_keys.env`` file (read-only compatibility).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeverse3d.contracts.common import Backends

_LEGACY_KEYS_FILE = Path.home() / ".config" / "astra3d" / "gemini_keys.env"
_USER_CONFIG = Path.home() / ".config" / "3dcodeverse" / "config.yaml"
#: the file names before D78 (2026-09-22): still read, each UNDER its new name (a new file wins per setting)
_LEGACY_USER_CONFIG = Path.home() / ".config" / "codeverse" / "config.yaml"


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
    fewer_turns: bool = Field(
        default=False,
        description="fold the cheap gates into `build`, report a per-file lint verdict from "
        "write_file / edit_file, inline the files a refine task edits, and ask the baseline "
        "session for every file in its first turn (docs/COST.md §29).  OFF until the A/B "
        "reads out; `C3D_FEWER_TURNS=1` (read at call time by `fewer_turns_enabled`) is "
        "what `bench/ab_plan.py --variant-env` flips.",
    )
    seed_recipes: bool = Field(
        default=True,
        description="graphics / glsl_shader: paste the cookbook recipes the brief calls for "
        "(curtain / aurora / stars / bokehSoft / dropsLayer + the hash / noise / fbm helpers "
        "they use) into the harness-owned, read-only src/recipes.glsl BEFORE the baseline session "
        "(tracks/graphics.py:seed_recipes; pasted above src/common.glsl at build time).  "
        "ON by default: measured 2026-08-26 (refs_v2_graphics, aurora brief, gemini-3.7-flash) "
        "the prompt carried the verified curtain() recipe five times and the agent used it zero "
        "times — round 0 was again a comb of bars (comb_artefact, 0.33); and (bench/out/seed_v1) "
        "recipes seeded into the agent's own common.glsl were overwritten before the end of the run.  "
        "`C3D_SEED_RECIPES=0` (read at call time by `seed_recipes_enabled`) is the control arm.",
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
               # was reported as FITTING the pool budget by `3dcode doctor`, poisoned the shared
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
        "reproducible: C3D_RATE__STORM_GATE=1, or bench/concurrency_probe.py --storm-gate.")
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


#: The fewer-turns switch (docs/COST.md §29).  Read at CALL time by
#: :func:`fewer_turns_enabled`, never only through the cached Settings: ``bench/ab_plan.py``
#: differs its arms by environment alone, and a value frozen at first ``get_settings()``
#: would hand the variant the control's behaviour (the CQ-5 lesson, tracks/plan_features.py).
FEWER_TURNS_ENV = "C3D_FEWER_TURNS"
#: The recipe-seeding switch (Limits.seed_recipes); same call-time contract as FEWER_TURNS_ENV.
SEED_RECIPES_ENV = "C3D_SEED_RECIPES"
#: wire the scene texture pack into the scene loop (a stage before env/zones, and the
#: pack description in both prompts).  OFF by default: it adds an image-model call per
#: run, and whether it earns that is a measurement nobody has made yet.
SCENE_TEXTURES_ENV = "C3D_SCENE_TEXTURES"
_TRUE_WORDS = frozenset({"1", "on", "true", "yes", "y"})
_FALSE_WORDS = frozenset({"0", "off", "false", "no", "n"})


def _env_flag(raw: str, env: str) -> bool:
    word = raw.strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    raise ValueError(f"{env}={raw!r}: expected on/off (1/0, true/false, yes/no)")


def env_flag(env: str, fallback: bool) -> bool:
    """``$env`` read NOW (never through the cached Settings — an A/B arm sets it after
    first touch): unset or empty → ``fallback``; garbage → OFF with a warning, because a
    typo in a bench command must produce a control run, not a crash mid-battery (and
    not the variant: ``fallback`` may be on).  ``skills/config.py`` reads its switches
    through this too."""
    raw = os.environ.get(env)
    if raw is not None and raw.strip():
        try:
            return _env_flag(raw, env)
        except ValueError as e:
            logging.getLogger(__name__).warning("%s; treating it as off", e)
            return False
    return fallback


def fewer_turns_enabled() -> bool:
    """Is the fewer-turns bundle on for THIS call?  ``$C3D_FEWER_TURNS`` when it is set,
    else ``Settings.limits.fewer_turns``."""
    return env_flag(FEWER_TURNS_ENV, get_settings().limits.fewer_turns)


def scene_textures_enabled() -> bool:
    """Is the scene texture stage on for THIS call?  ``$C3D_SCENE_TEXTURES``, else off.

    The pack generator (``texturing.plan.scene_texture_pack``) and the prompt snippet that
    describes it (``texture_pack_prompt`` — whose docstring already says "for zone/env
    generation") have existed since the texturing work, but nothing in ``tracks/scene.py``
    called either: the switch `Spec.options.texture` does nothing on this track and the
    generator was never told a pack could exist.  Measured on bench/out/scene_baseline
    (2026-09-05): four of the five scored cells' judge complaints are the GROUND being a
    flat untextured colour, in near-identical words, and that is the most consistent
    defect in the battery.
    """
    return env_flag(SCENE_TEXTURES_ENV, False)


def seed_recipes_enabled() -> bool:
    """Is recipe seeding (``tracks/graphics.py:seed_recipes``) on for THIS call?  ``$C3D_SEED_RECIPES``
    when it is set, else ``Settings.limits.seed_recipes`` (default ON)."""
    return env_flag(SEED_RECIPES_ENV, get_settings().limits.seed_recipes)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="C3D_", env_nested_delimiter="__", extra="ignore")

    #: Flat aliases for nested knobs people actually type.  ``C3D_MAX_IN_FLIGHT=16`` was
    #: written into three launch commands and two workflow briefs on 2026-08-24 before anyone
    #: noticed pydantic-settings only reads ``C3D_RATE__MAX_IN_FLIGHT`` — every one of them
    #: silently ran at the default 64.  An env knob that is read by nothing is worse than no
    #: knob; both spellings now work and the doctor prints the short one.
    _FLAT_ALIASES: ClassVar[dict[str, tuple[str, str]]] = {
        "C3D_MAX_IN_FLIGHT": ("rate", "max_in_flight"),
        # the spelling every doc and gpu_launch.cjs use; only C3D_RENDER__GPU was read
        "C3D_RENDER_GPU": ("render", "gpu"),
        FEWER_TURNS_ENV: ("limits", "fewer_turns"),
        SEED_RECIPES_ENV: ("limits", "seed_recipes"),
    }

    @model_validator(mode="after")
    def _apply_flat_aliases(self) -> Settings:
        for env, (section, field) in self._FLAT_ALIASES.items():
            raw = os.environ.get(env)
            if raw is None or raw.strip() == "":
                continue
            sub = getattr(self, section)
            value: int | bool | str
            ann = type(sub).model_fields[field].annotation
            if ann is bool:
                value = _env_flag(raw, env)
            elif ann is str:  # render.gpu is the only str alias: validate its enum here
                value = raw.strip().lower()
                if value not in ("auto", "on", "off"):
                    raise ValueError(f"{env}={raw!r}: expected auto|on|off")
            else:
                try:
                    value = int(raw)
                except ValueError as e:
                    raise ValueError(f"{env}={raw!r}: expected an integer") from e
                # plain assignment does NOT re-validate (no validate_assignment), so the target
                # field's own bound is enforced here — and the message names the variable the
                # operator actually typed, not the nested field they never heard of.
                lo = _lower_bound(type(sub), field)
                if lo is not None and value < lo:
                    raise ValueError(f"{env}={raw!r}: must be >= {lo}")
            setattr(sub, field, value)
        return self

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
        override = os.environ.get("C3D_RUNTIME_JS", "").strip()
        d = Path(override).expanduser() if override else Path(__file__).resolve().parent.parent / "runtime_js"
        if not d.is_dir():
            raise RuntimeError(
                f"runtime_js not found at {d} — install the harness editable (pip install -e harness) "
                "or set C3D_RUNTIME_JS to a 3dcodeverse/harness/runtime_js checkout (with node_modules installed)"
            )
        return d


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


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge — ``overlay`` wins per SETTING, not per section.

    ``dict.update`` replaced a whole sub-dict, so a project ``./3dcodeverse.yaml`` that
    merely NAMED a section silently dropped every sibling key the user had set in
    ``~/.config/3dcodeverse/config.yaml`` — those keys fell back to the built-in Field
    defaults, not to the user's values.  Concretely: a user config with
    ``rate.tpm_per_key: 250000`` plus a project file with only ``rate.max_in_flight: 8``
    scheduled the key pool against the built-in 1,000,000 TPM, 4x the operator's real
    quota, even though Rate's own docstring calls tpm_per_key the binding limit on this
    box.  docs/INSTALL.md §8.3 documents the order as "built-in defaults < user config <
    project config < env", which every reader takes as per-setting.
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
