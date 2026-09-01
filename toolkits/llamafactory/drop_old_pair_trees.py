"""Remove the superseded top-level pairs/ and pair_images/ once their replacements are registered.

Both were mine: the pair datasets were first published into a separate top-level tree, which read as if it were
a source of its own. They now live at <source>/<subdir>_llamafactory_{text,img,imgtext} and
<source>/pair_renders/, and dataset_info.json points only at those, so the old tree is safe to drop.
"""
import os, json
from huggingface_hub import HfApi, hf_hub_download, CommitOperationDelete
tok = os.environ["HF_TOKEN"]; REPO = "ilabai/3dcodeverse"; api = HfApi(token=tok)
agg = json.load(open(hf_hub_download(REPO, "dataset_info.json", repo_type="dataset", token=tok)))
still = [k for k, v in agg.items() if str(v.get("folder", "")).startswith(("pairs/", "pair_images/"))]
if still:
    raise SystemExit(f"REFUSING: {len(still)} index entries still point into the old tree")
for d in ("pairs", "pair_images"):
    files = [e.path for e in api.list_repo_tree(REPO, repo_type="dataset", path_in_repo=d, recursive=True)
             if e.__class__.__name__.startswith("RepoFile")]
    print(f"[drop] {d}/: {len(files):,} files", flush=True)
    B = 400
    for i in range(0, len(files), B):
        api.create_commit(repo_id=REPO, repo_type="dataset",
                          operations=[CommitOperationDelete(path_in_repo=f) for f in files[i:i+B]],
                          commit_message=f"remove the superseded {d}/ ({i+1}-{min(i+B, len(files))} of {len(files)})")
        if (i // B) % 20 == 0:
            print(f"[drop]   {min(i+B, len(files)):,}/{len(files):,}", flush=True)
    print(f"[drop] {d}/ removed", flush=True)
print("[drop] DONE", flush=True)
