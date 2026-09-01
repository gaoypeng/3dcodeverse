"""Registry + dedup-index client — talks to the 3dcodebench API (Vercel).

`register` records a pushed project; `fetch_index` pulls the compact hash index so
`check`/`push` can flag duplicates locally. Plain urllib — no extra dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import Config

_UA = "3dcode-toolkit"


def _req(cfg: Config, path: str, method: str, body: dict | None = None) -> dict:
    headers = {"User-Agent": _UA, "x-cv-token": cfg.contrib_token}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(cfg.api.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:  # network/timeout
        return {"error": str(e)}


def register(cfg: Config, meta: dict) -> dict:
    return _req(cfg, "/api/data/cv/register", "POST", meta)


def fetch_index(cfg: Config, source: str | None = None) -> list[dict]:
    path = "/api/data/cv/index" + (f"?source={source}" if source else "")
    return _req(cfg, path, "GET").get("index", [])


def fetch_approved(cfg: Config) -> list[dict]:
    """Admin-approved projects awaiting ingest: [{id, source, project}]."""
    return _req(cfg, "/api/data/cv/approved", "GET").get("approved", [])


def _phash_close(a: str, b: str, thresh: int = 6) -> bool:
    try:
        ha, hb = int(a.split(":", 1)[1], 16), int(b.split(":", 1)[1], 16)
        return bin(ha ^ hb).count("1") <= thresh
    except Exception:
        return False


def dedup_matches(meta: dict, index: list[dict]) -> list[tuple[str, str]]:
    """(existing_id, reason) for each existing project the new one likely duplicates."""
    d = meta.get("dedup", {})
    hits: list[tuple[str, str]] = []
    for row in index:
        if row.get("id") == meta.get("id"):
            continue
        if d.get("code_hash") and row.get("code_hash") == d["code_hash"]:
            hits.append((row["id"], "identical code"))
        elif d.get("geom_hash") and row.get("geom_hash") == d["geom_hash"]:
            hits.append((row["id"], "identical geometry"))
        elif d.get("phash") and row.get("phash") and _phash_close(d["phash"], row["phash"]):
            hits.append((row["id"], "near-identical render"))
    return hits
