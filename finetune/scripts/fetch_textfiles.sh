#!/bin/bash
# Fetch only small text files (code/captions/meta/reports) of per-sample HF dataset folders with parallel curl.
# usage: fetch_textfiles.sh <repo_id> <subpath-or-empty> <outdir> <regex-of-files-to-keep>
REPO=$1; SUB=$2; OUT=$3; PAT=$4
mkdir -p "$OUT"; cd "$OUT" || exit 1
curl -s -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/datasets/$REPO/tree/main/$SUB?recursive=true" \
 | python3 -c "import sys,json,re; pat=re.compile(sys.argv[1]); [print(x['path']) for x in json.load(sys.stdin) if x['type']=='file' and pat.search(x['path'])]" "$PAT" > .filelist
echo "files to fetch: $(wc -l < .filelist)"
cat .filelist | xargs -P 32 -I{} bash -c 'f="{}"; mkdir -p "$(dirname "$f")"; [ -s "$f" ] || curl -sS -L -H "Authorization: Bearer $HF_TOKEN" -o "$f" "https://huggingface.co/datasets/'$REPO'/resolve/main/$f" || echo "FAIL $f"'
echo "FETCH_DONE $(find . -type f ! -name .filelist | wc -l) files"
