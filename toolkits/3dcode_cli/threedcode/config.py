"""Config: R2 endpoint + bucket + scoped token + default source.

Resolution order (first hit wins): environment variables, then
``~/.config/3dcode/config.toml``. Secrets live ONLY here / in env — never in the repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # 3.10
    import tomli as tomllib  # type: ignore

CONFIG_PATH = Path(os.path.expanduser("~/.config/3dcode/config.toml"))

# config key -> environment variable that overrides it
_ENV = {
    "endpoint": "R2_ENDPOINT",
    "bucket": "R2_BUCKET",
    "access_key_id": "R2_ACCESS_KEY_ID",
    "secret_access_key": "R2_SECRET_ACCESS_KEY",
    "source": "DCODE_SOURCE",
    "api": "CV_API",
    "contrib_token": "CV_CONTRIB_TOKEN",
    "blender_5_0": "BLENDER_5_0",
    "blender_5_1": "BLENDER_5_1",
    "openscad": "OPENSCAD",
    "freecadcmd": "FREECADCMD",
    "core_dir": "DCODE_CORE_DIR",
}


@dataclass
class Config:
    endpoint: str = ""
    bucket: str = "3dcodeverse"
    access_key_id: str = ""
    secret_access_key: str = ""
    source: str = ""
    api: str = "https://www.3dcodebench.com"   # registry + dedup-index endpoints
    contrib_token: str = ""                     # shared contributor token for those endpoints
    blender_5_0: str = ""                        # local Blender 5.0 binary, for `3dcode exec`
    blender_5_1: str = ""                        # local Blender 5.1 binary
    openscad: str = ""                           # local OpenSCAD binary/AppImage (.scad exec/render)
    freecadcmd: str = ""                         # local freecadcmd binary (FreeCAD exec/render)
    core_dir: str = "/lab/yipeng/infinigen/3dcodeverse"   # admin-side canonical store (for `3dcode ingest`)

    def require_r2(self) -> None:
        missing = [k for k in ("endpoint", "bucket", "access_key_id", "secret_access_key")
                   if not getattr(self, k)]
        if missing:
            raise SystemExit(
                f"R2 not configured (missing {', '.join(missing)}). "
                f"Run `3dcode config set ...` or set the R2_* env vars."
            )


def _file_values() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "rb") as fh:
        return tomllib.load(fh)


def load_config() -> Config:
    raw = _file_values()
    # only pass non-empty values so dataclass defaults (e.g. `api`) survive
    vals = {k: (os.environ.get(env) or raw.get(k, "")) for k, env in _ENV.items()}
    return Config(**{k: v for k, v in vals.items() if v})


def save_config(updates: dict) -> Path:
    """Merge ``updates`` into the on-disk config (creating it if needed)."""
    raw = _file_values()
    raw.update({k: v for k, v in updates.items() if v is not None})
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'{k} = "{v}"' for k, v in raw.items()]
    CONFIG_PATH.write_text("\n".join(lines) + "\n")
    CONFIG_PATH.chmod(0o600)  # secret key inside — keep it private
    return CONFIG_PATH
