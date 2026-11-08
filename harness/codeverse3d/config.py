"""Instrument settings: binaries, directories, render and gate knobs.

Resolution order (later wins): built-in defaults < ``~/.config/3dcodeverse/config.yaml``
< ``./3dcodeverse.yaml`` < environment variables (``C3D_*``).

Every ``C3D_*`` knob is a field here, read through ``get_settings()``.  A field ``x`` is
``C3D_X`` (nested ``section.x`` is ``C3D_SECTION__X``, plus :data:`Settings.FLAT`); an on/off
switch is a :data:`Flag`.
"""

from __future__ import annotations

import logging
import os
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
    field_validator,
    model_validator,
)
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, SettingsConfigDict

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


#: the supported Blender (5.2 LTS); ``3dcode doctor`` fails anything older.
BLENDER_MIN: tuple[int, int] = (5, 2)


class Binaries(BaseModel):
    blender: str = Field(default="", description="path to the Blender 5.2 LTS binary (headless capable)")
    node: str = "node"


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
    # scene_blender pictures (spatial/render_blender.py): Cycles CUDA + GPU OIDN is the fast path.
    # OptiX is unavailable under WSL2 and probing it crashes Blender: CUDA only.
    blender_frame_time_limit_s: float = Field(
        default=20.0, ge=0, description="Cycles time cap per judged frame, seconds (0 = none): a heavy scene stops "
        "sampling early instead of eating the render timeout (C3D_RENDER__BLENDER_FRAME_TIME_LIMIT_S)")
    blender_samples: int = Field(
        default=32, ge=1, description="Cycles samples, adaptive + OIDN (C3D_RENDER__BLENDER_SAMPLES)")
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
    bpy_rlimit_gb: int = 12


class Judge(BaseModel):
    """What the judge pack contains (``judge_pack``)."""

    max_px: int = Field(default=1024, description="longest edge of a montage / crop in the judge pack")
    montages: int = Field(default=5, description="max 2x2 montages per pack (14-view rig)")
    detail_crops: int = Field(default=2, description="max zoomed detail crops per pack")
    slices: Literal["on-error", "off"] = Field(
        default="on-error",
        description="conditional cross-section slices (C3D_JUDGE__SLICES): 'on-error' appends up to 2 interior "
        "slice images + one provenance sentence to an object-track pack whose connectivity gate carries "
        "an ERROR; clean rounds get a byte-identical pack either way.")


class Placement(BaseModel):
    """The scene_placement gate's thresholds (spatial/scene_placement.py), metres / fractions.
    The JS measurement (host_placement.mjs) keeps its own CONTACT / FLOATING / SUNK constants: Settings refuses a
    placement whose contact_m / floating_outdoor_m / sunk_m differ from them; the rest decide severities only."""

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

    @field_validator("placement")
    @classmethod
    def _placement_matches_the_probe(cls, v: Placement) -> Placement:
        """The probe (host_placement.mjs) measures `seated` / `floating` / `sunk` at its own constants: the gate must grade the same numbers."""
        d = Placement()
        if (v.contact_m, v.floating_outdoor_m, v.sunk_m) != (d.contact_m, d.floating_outdoor_m, d.sunk_m):
            raise ValueError("placement.contact_m / floating_outdoor_m / sunk_m are pinned to host_placement.mjs "
                             "(CONTACT_TOL_M / FLOATING_M / SUNK_M); change both together")
        return v

    cache_dir: Path = Field(default=Path.home() / ".cache" / "codeverse3d")
    binaries: Binaries = Field(default_factory=Binaries)
    render: Render = Field(default_factory=Render)
    limits: Limits = Field(default_factory=Limits)
    judge: Judge = Field(default_factory=Judge)
    placement: Placement = Field(default_factory=Placement)

    ray_engine: Literal["numpy", "embree"] = Field(
        default="embree",
        description="ray engine of the deterministic inside tests (connectivity penetration, pose sweep): Embree via "
        "embreex, or trimesh's own ray-triangle intersector (numpy: ~100x slower, memory grows with rays x "
        "triangles) for a GLB on which embreex 4 segfaults.  C3D_RAY_ENGINE.",
    )

    # ------------------------------------------------------------------ switches
    settle: Flag = Field(default=True, description="scene driver: the boot-time auto-seat")
    camera_repair: Flag = Field(default=True, description="scene driver: move a camera out of geometry "
                                "(zero triggers on a healthy battery; the case it exists for is fatal)")
    post: Flag = Field(default=True, description="scene pictures: the GTAO + bloom + grade post chain")
    runtime_js: Path | None = Field(default=None, description="the node runtime directory; a "
                                    "non-editable install must point this at a checkout")

    # ------------------------------------------------------------------ helpers
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
                f"runtime_js not found at {d} — install the plugin editable (pip install -e harness) "
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
    return Settings(**data)
