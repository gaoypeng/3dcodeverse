"""make_lora_cfg.py NAME DATANAME [--epochs 2] [--lr 1e-4] -> configs/lf/lora_<NAME>.yaml (from the v1 LoRA template)"""
import sys, argparse, yaml
ap = argparse.ArgumentParser(); ap.add_argument("name"); ap.add_argument("dataname"); ap.add_argument("--epochs", type=float, default=2.0); ap.add_argument("--lr", type=float, default=1e-4)
ap.add_argument("--rank", type=int, default=64); ap.add_argument("--cutoff", type=int, default=8192)
a = ap.parse_args()
cfg = yaml.safe_load(open("/wekafs/ict/hx_624/llm-ft/configs/lf/qwen35_9b_lora_sft.yaml"))
cfg.update(dataset=f"{a.dataname}_train", eval_dataset=f"{a.dataname}_val",
           tokenized_path=f"/wekafs/ict/hx_624/llm-ft/data/lf/tokenized_{a.dataname}_len{a.cutoff}_packed",
           output_dir=f"/wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_lora_{a.name}",
           num_train_epochs=a.epochs, learning_rate=a.lr, lora_rank=a.rank, lora_alpha=2 * a.rank, cutoff_len=a.cutoff)
out = f"/wekafs/ict/hx_624/llm-ft/configs/lf/lora_{a.name}.yaml"
yaml.safe_dump(cfg, open(out, "w"), sort_keys=False); print(out)
