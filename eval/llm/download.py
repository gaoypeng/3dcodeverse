"""Download the evaluation assets from the Hub into $CV3D_EVAL_DATA/hub (needs a token with access to
`ilabai/*`; `hf auth login` or HF_TOKEN).

  --core       212 3DCodeBench tasks (YipengGao/3DCode/3DCodeBench), the five held-out test parquets and
               mixes.json (ilabai/3dcodeverse-llamafactory), the 3dcodeverse dataset card + _reports/ +
               factories metadata                                                    (~30 MB)
  --views      the official 4 GT reference views per bench task (3DCodeBench_ModelLogs/inputs) (~930 MB)
  --logs       frontier-model generation logs of the official benchmark (text_to_3D / image_to_3D parquets),
               re-executable here for reference numbers                              (~16 MB)
  --gt-tar     3dcodebench/factories_geo/samples-000.tar with the canonical object.glb per factory (2.1 GB)
"""
from __future__ import annotations

import argparse

from huggingface_hub import snapshot_download

from . import config


def dl(repo: str, patterns: list[str], sub: str, workers: int = 16) -> None:
    p = snapshot_download(repo, repo_type="dataset", allow_patterns=patterns, local_dir=str(config.HUB_DIR / sub), max_workers=workers)
    print(f"[download] {repo} {patterns[:2]}... -> {p}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", action="store_true")
    ap.add_argument("--views", action="store_true")
    ap.add_argument("--logs", action="store_true")
    ap.add_argument("--gt-tar", action="store_true")
    a = ap.parse_args()
    if not any(vars(a).values()):
        a.core = True
    if a.core:
        dl("YipengGao/3DCode", ["3DCodeBench/*", "3DCodeBench_ModelLogs/README.md", "README.md"], "3DCode")
        dl("ilabai/3dcodeverse-llamafactory", ["test/*", "*.json", "README.md"], "3dcodeverse-llamafactory")
        dl("ilabai/3dcodeverse", ["_reports/*", "dataset_info*.json", "README.md", "3dcodebench/factories_geo/metadata.parquet",
                                  "3dcodebench/factories_tex/metadata.parquet", "data/build_report_text.json"], "3dcodeverse")
    if a.views:
        dl("YipengGao/3DCode", ["3DCodeBench_ModelLogs/inputs/*"], "3DCode")
    if a.logs:
        dl("YipengGao/3DCode", ["3DCodeBench_ModelLogs/data/text_to_3D.parquet", "3DCodeBench_ModelLogs/data/image_to_3D.parquet"], "3DCode")
    if a.gt_tar:
        dl("ilabai/3dcodeverse", ["3dcodebench/factories_geo/samples-000.tar"], "3dcodeverse", workers=4)


if __name__ == "__main__":
    main()
