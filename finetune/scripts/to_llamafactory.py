"""Convert our messages-jsonl (system/user/assistant) into LLaMA-Factory sharegpt json + dataset_info.json entries."""
import argparse, json, os
ap = argparse.ArgumentParser()
ap.add_argument("--data_dir", default="/wekafs/ict/hx_624/llm-ft/data/sft_v1")
ap.add_argument("--out_dir", default="/wekafs/ict/hx_624/llm-ft/data/lf")
ap.add_argument("--name", default="blender3d_v1")
a = ap.parse_args()
os.makedirs(a.out_dir, exist_ok=True)
info_path = os.path.join(a.out_dir, "dataset_info.json")
info = json.load(open(info_path)) if os.path.exists(info_path) else {}
for split in ["train", "val"]:
    rows = []
    for l in open(os.path.join(a.data_dir, f"{split}.jsonl")):
        m = json.loads(l)["messages"]
        sys_msg = next((x["content"] for x in m if x["role"] == "system"), None)
        conv = []
        for x in m:
            if x["role"] == "user": conv.append({"from": "human", "value": x["content"]})
            elif x["role"] == "assistant": conv.append({"from": "gpt", "value": x["content"]})
        rows.append({"conversations": conv, "system": sys_msg})
    fn = f"{a.name}_{split}.json"
    json.dump(rows, open(os.path.join(a.out_dir, fn), "w"), ensure_ascii=False)
    info[f"{a.name}_{split}"] = {"file_name": fn, "formatting": "sharegpt", "columns": {"messages": "conversations", "system": "system"}}
    print(split, len(rows), "->", fn)
json.dump(info, open(info_path, "w"), indent=2)
print("dataset_info:", list(info.keys()))
