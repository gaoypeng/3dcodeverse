"""Publish the pair datasets INTO their source folders, not into a separate top-level tree.

Every converted dataset in this repo lives at <source>/<subdir>_llamafactory; the pair datasets follow the same
rule as <source>/<subdir>_llamafactory_{text,img,imgtext}, and their renders as <source>/pair_renders/... . A
top-level pairs/ or pair_images/ would read as if it were a source of its own, which is the same mistake the
five stray *_llamafactory folders made.

usage: python scripts/upload_pairs.py [--batch 24] [--images] [--drop_old]
"""
import argparse
import glob
import json
import os

from huggingface_hub import HfApi, CommitOperationAdd, CommitOperationDelete, hf_hub_download

REPO = "ilabai/3dcodeverse"
ROOTS = ("/wekafs/ict/hx_624/hf_pairs_shadertoy", "/wekafs/ict/hx_624/hf_pairs", "/wekafs/ict/hx_624/hf_pairs_other")
# the two sources the user renamed after these were built
RENAME = {"blender_distill": "blender_coding_agent", "threejs_distill": "threejs_coding_agent"}


def collect():
    out = []
    for root in ROOTS:
        for d in sorted(glob.glob(f"{root}/*_llamafactory_*")):
            q = json.load(open(f"{d}/qc.json"))
            if q["rows"] == 0:
                continue
            src = RENAME.get(q["subdir"].split("/")[0], q["subdir"].split("/")[0])
            out.append((d, f"{src}/{os.path.basename(d)}", q))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=24)
    ap.add_argument("--images", action="store_true")
    ap.add_argument("--drop_old", action="store_true", help="remove the earlier top-level pairs/ and pair_images/")
    a = ap.parse_args()
    api = HfApi(token=os.environ["HF_TOKEN"])
    items = collect()
    print(f"[up] {len(items)} pair datasets, {sum(q['rows'] for _, _, q in items):,} rows", flush=True)

    for i in range(0, len(items), a.batch):
        chunk = items[i:i + a.batch]
        ops = []
        for d, path, _ in chunk:
            for f in ("train.parquet", "dataset_info.json", "qc.json"):
                if os.path.exists(f"{d}/{f}"):
                    ops.append(CommitOperationAdd(path_in_repo=f"{path}/{f}", path_or_fileobj=f"{d}/{f}"))
        api.create_commit(repo_id=REPO, repo_type="dataset", operations=ops,
                          commit_message=f"pairs: publish {i+1}-{i+len(chunk)} of {len(items)} under their sources")
        print(f"[up] pushed {i+1}-{i+len(chunk)}", flush=True)

    p = hf_hub_download(REPO, "dataset_info.json", repo_type="dataset", token=os.environ["HF_TOKEN"])
    agg = json.load(open(p))
    before = len(agg)
    # drop the entries that pointed into the old top-level tree, then register the new locations
    agg = {k: v for k, v in agg.items() if not str(v.get("folder", "")).startswith("pairs/")}
    for d, path, q in items:
        info = json.load(open(f"{d}/dataset_info.json"))
        for name, attr in info.items():
            agg[name] = {"hf_hub_url": REPO, "folder": path, "formatting": "sharegpt", "split": "train",
                         "columns": attr.get("columns", {"messages": "conversations", "system": "system"})}
    out = "/tmp/claude-1035/-wekafs-ict-hx-624/8a069962-27e5-496d-b000-d4e3dd4dac29/scratchpad/dataset_info_v2.json"
    json.dump(agg, open(out, "w"), indent=2, ensure_ascii=False, sort_keys=True)
    api.upload_file(path_or_fileobj=out, path_in_repo="dataset_info.json", repo_id=REPO, repo_type="dataset",
                    commit_message=f"dataset_info: point the pair datasets at their source folders ({before} -> {len(agg)})")
    print(f"[up] index {before} -> {len(agg)} entries", flush=True)

    if a.images:
        for src in sorted(os.listdir("/wekafs/ict/hx_624/data/pair_images_small")):
            d = f"/wekafs/ict/hx_624/data/pair_images_small/{src}"
            if not os.path.isdir(d):
                continue
            dst = f"{RENAME.get(src, src)}/pair_renders"
            print(f"[up] renders {src} -> {dst}", flush=True)
            api.upload_folder(folder_path=d, path_in_repo=dst, repo_id=REPO, repo_type="dataset",
                              commit_message=f"{dst}: renders at training resolution")

    if a.drop_old:
        for old in ("pairs", "pair_images"):
            try:
                es = list(api.list_repo_tree(REPO, repo_type="dataset", path_in_repo=old, recursive=True))
                files = [e.path for e in es if e.__class__.__name__.startswith("RepoFile")]
                for i in range(0, len(files), 200):
                    api.create_commit(repo_id=REPO, repo_type="dataset",
                                      operations=[CommitOperationDelete(path_in_repo=f) for f in files[i:i+200]],
                                      commit_message=f"remove the superseded top-level {old}/ ({i+1}-{min(i+200,len(files))})")
                print(f"[up] removed {len(files)} files from {old}/", flush=True)
            except Exception as e:
                print(f"[up] {old}/: {type(e).__name__} {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
