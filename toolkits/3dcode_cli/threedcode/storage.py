"""R2 (S3-compatible) object storage — thin boto3 wrapper used by `3dcode`.

Contributors upload straight from their own machine to the staging bucket; the admin
side pulls/lists/deletes the same way. Keys are ``<source>/<project>/<relpath>``.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from .config import Config


@dataclass
class Uploaded:
    files: int
    bytes: int


class R2:
    def __init__(self, cfg: Config):
        cfg.require_r2()
        self.bucket = cfg.bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=cfg.endpoint,
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=cfg.secret_access_key,
            region_name="auto",
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 5}),
        )

    def put_file(self, local: Path, key: str) -> int:
        ctype = mimetypes.guess_type(str(local))[0] or "application/octet-stream"
        self._s3.upload_file(str(local), self.bucket, key, ExtraArgs={"ContentType": ctype})
        return local.stat().st_size

    def upload_tree(self, local_dir: Path, key_prefix: str) -> Uploaded:
        """Upload every file under ``local_dir`` to ``key_prefix/<relpath>``."""
        files = bytes_ = 0
        for path in sorted(local_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(local_dir).as_posix()
                bytes_ += self.put_file(path, f"{key_prefix.rstrip('/')}/{rel}")
                files += 1
        return Uploaded(files, bytes_)

    def download_tree(self, prefix: str, dest_dir: Path) -> Uploaded:
        """Download every object under ``prefix`` into ``dest_dir/<relpath>`` (zero egress on R2)."""
        prefix = prefix.rstrip("/") + "/"
        files = bytes_ = 0
        for key in self.list(prefix):
            rel = key[len(prefix):]
            if not rel:
                continue
            out = dest_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            self._s3.download_file(self.bucket, key, str(out))
            bytes_ += out.stat().st_size
            files += 1
        return Uploaded(files, bytes_)

    def list(self, prefix: str = "") -> list[str]:
        keys, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            r = self._s3.list_objects_v2(**kw)
            keys += [o["Key"] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return keys
            token = r["NextContinuationToken"]

    def get_text(self, key: str) -> str:
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode()

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list(prefix)
        for i in range(0, len(keys), 1000):
            batch = [{"Key": k} for k in keys[i:i + 1000]]
            self._s3.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})
        return len(keys)
