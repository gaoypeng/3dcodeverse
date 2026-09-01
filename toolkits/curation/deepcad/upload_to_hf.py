"""
upload_to_hf.py
Upload the 3DCodeVerse-format corpus tree to a HuggingFace dataset repo.
Uses upload_large_folder (resumable, multi-threaded) -- required for the
~1.5M small files. NEEDS A WRITE TOKEN with access to the target repo.

Usage:
  python upload_to_hf.py --folder /data/.../corpus_3dcv \
      --repo ilabai/3dcodeverse --token_file /data/.../.hf_token_write
"""
import argparse
from pathlib import Path
from huggingface_hub import HfApi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True, help="local folder whose contents map to repo root (contains deepcad/)")
    ap.add_argument("--repo", required=True, help="e.g. ilabai/3dcodeverse or adonis126/deepcad-3dcodeverse")
    ap.add_argument("--token_file", required=True)
    ap.add_argument("--repo_type", default="dataset")
    ap.add_argument("--create", action="store_true", help="create the repo if it doesn't exist")
    args = ap.parse_args()

    token = Path(args.token_file).read_text().strip()
    api = HfApi(token=token)

    me = api.whoami()
    role = me.get("auth", {}).get("accessToken", {}).get("role", "?")
    print(f"Authenticated as {me.get('name')} (token role: {role})")
    if role != "write":
        print("WARNING: token role is not 'write' -- upload will fail. Create a WRITE token.")

    if args.create:
        api.create_repo(args.repo, repo_type=args.repo_type, exist_ok=True)
        print(f"Ensured repo {args.repo} exists.")

    print(f"Uploading {args.folder} -> {args.repo} ({args.repo_type}) ...")
    api.upload_large_folder(
        repo_id=args.repo,
        repo_type=args.repo_type,
        folder_path=args.folder,
    )
    print("Upload complete.")


if __name__ == "__main__":
    main()
