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

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CV3D_", env_nested_delimiter="__", extra="ignore")

    runs_dir: Path = Field(default=Path("runs"))
    cache_dir: Path = Field(default=Path.home() / ".cache" / "codeverse")
    binaries: Binaries = Field(default_factory=Binaries)
    render: Render = Field(default_factory=Render)
    limits: Limits = Field(default_factory=Limits)

    gemini_api_keys: list[str] = Field(default_factory=list)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""

    default_planner: str = "gemini:gemini-3.7-flash"
    default_generator: str = "api-agent:gemini:gemini-3.7-flash"
    # The judge drives the refine loop: the pro tier has ~3x lower sample noise than flash
    # (calibration 2026-08-23: std 0.03 vs 0.08-0.12) for ~$0.07 per verdict.
    default_judge: str = "gemini:gemini-3.1-pro-preview"
    default_candidates: int = Field(default=1, description="best-of-N baseline candidates (tracks read it)")

    # ------------------------------------------------------------------ helpers
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
    return s
