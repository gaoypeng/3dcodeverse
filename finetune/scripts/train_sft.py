"""SFT (full or LoRA) of a causal LM on chat-format jsonl using TRL SFTTrainer.

Data: jsonl rows with {"messages":[system,user,assistant]} -> converted to conversational prompt/completion
so that the loss is computed on the assistant completion only.
"""
import argparse, json, os, sys, time
import torch
from datasets import load_dataset
from transformers import AutoTokenizer, AutoModelForCausalLM
from trl import SFTConfig, SFTTrainer

def parse():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data_dir", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--max_length", type=int, default=8192)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--warmup_ratio", type=float, default=0.03)
    ap.add_argument("--per_device_bs", type=int, default=1)
    ap.add_argument("--grad_accum", type=int, default=8)
    ap.add_argument("--eval_bs", type=int, default=2)
    ap.add_argument("--lora", action="store_true")
    ap.add_argument("--lora_r", type=int, default=64)
    ap.add_argument("--lora_alpha", type=int, default=128)
    ap.add_argument("--lora_dropout", type=float, default=0.05)
    ap.add_argument("--packing", action="store_true")
    ap.add_argument("--no_liger", action="store_true")
    ap.add_argument("--attn", default="auto", help="auto|flash_attention_2|sdpa|eager")
    ap.add_argument("--grad_ckpt", action="store_true", default=True)
    ap.add_argument("--deepspeed", default=None)
    ap.add_argument("--save_steps", type=int, default=200)
    ap.add_argument("--eval_steps", type=int, default=100)
    ap.add_argument("--logging_steps", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max_train_samples", type=int, default=None)
    ap.add_argument("--report_to", default="tensorboard")
    ap.add_argument("--run_name", default=None)
    ap.add_argument("--weight_decay", type=float, default=0.0)
    ap.add_argument("--neftune", type=float, default=None)
    return ap.parse_args()

def to_prompt_completion(ex):
    msgs = ex["messages"]
    return {"prompt": msgs[:-1], "completion": [msgs[-1]]}

def main():
    a = parse()
    torch.manual_seed(a.seed)
    attn = a.attn
    if attn == "auto":
        try:
            import flash_attn  # noqa
            attn = "flash_attention_2"
        except Exception:
            attn = "sdpa"
    tok = AutoTokenizer.from_pretrained(a.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    ds = load_dataset("json", data_files={"train": f"{a.data_dir}/train.jsonl", "val": f"{a.data_dir}/val.jsonl"})
    keep = ["messages"]
    ds = ds.map(to_prompt_completion, remove_columns=[c for c in ds["train"].column_names if c not in keep])
    ds = ds.remove_columns(["messages"])
    if a.max_train_samples:
        ds["train"] = ds["train"].select(range(min(a.max_train_samples, len(ds["train"]))))
    print(f"[data] train={len(ds['train'])} val={len(ds['val'])} attn={attn}", flush=True)

    model_kwargs = dict(dtype=torch.bfloat16, attn_implementation=attn)
    model = AutoModelForCausalLM.from_pretrained(a.model, **model_kwargs)
    model.config.use_cache = False

    peft_config = None
    if a.lora:
        from peft import LoraConfig
        peft_config = LoraConfig(r=a.lora_r, lora_alpha=a.lora_alpha, lora_dropout=a.lora_dropout, bias="none",
                                 task_type="CAUSAL_LM",
                                 target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])

    cfg_kwargs = dict(
        output_dir=a.output_dir, run_name=a.run_name or os.path.basename(a.output_dir.rstrip("/")),
        num_train_epochs=a.epochs, learning_rate=a.lr, lr_scheduler_type="cosine", warmup_ratio=a.warmup_ratio,
        weight_decay=a.weight_decay,
        per_device_train_batch_size=a.per_device_bs, per_device_eval_batch_size=a.eval_bs,
        gradient_accumulation_steps=a.grad_accum,
        bf16=True, tf32=True,
        gradient_checkpointing=a.grad_ckpt, gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=a.logging_steps, eval_strategy="steps", eval_steps=a.eval_steps,
        save_strategy="steps", save_steps=a.save_steps, save_total_limit=2,
        report_to=a.report_to, seed=a.seed,
        max_length=a.max_length, packing=a.packing,
        completion_only_loss=True,
        use_liger_kernel=not a.no_liger,
        dataloader_num_workers=2, remove_unused_columns=True,
        deepspeed=a.deepspeed,
        ddp_find_unused_parameters=False,
        save_only_model=True,
    )
    if a.neftune: cfg_kwargs["neftune_noise_alpha"] = a.neftune
    cfg = SFTConfig(**cfg_kwargs)

    trainer = SFTTrainer(model=model, args=cfg, train_dataset=ds["train"], eval_dataset=ds["val"],
                         processing_class=tok, peft_config=peft_config)
    if trainer.accelerator.is_main_process:
        n_tr = sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)
        n_all = sum(p.numel() for p in trainer.model.parameters())
        print(f"[model] trainable={n_tr/1e6:.1f}M / total={n_all/1e6:.1f}M ({100*n_tr/max(n_all,1):.2f}%)", flush=True)
        # show one tokenized example length
        ex = trainer.train_dataset[0]
        print("[data] example keys:", list(ex.keys()), "| input_ids len:", len(ex.get("input_ids", [])), flush=True)
    t0 = time.time()
    trainer.train()
    if trainer.accelerator.is_main_process:
        print(f"[done] train time {(time.time()-t0)/60:.1f} min", flush=True)
    trainer.save_model(os.path.join(a.output_dir, "final"))
    tok.save_pretrained(os.path.join(a.output_dir, "final"))
    if a.lora and trainer.accelerator.is_main_process:
        # also save a merged full model for easy eval
        try:
            merged = trainer.model.merge_and_unload()
            merged.save_pretrained(os.path.join(a.output_dir, "final_merged"), safe_serialization=True)
            tok.save_pretrained(os.path.join(a.output_dir, "final_merged"))
            print("[done] merged model saved", flush=True)
        except Exception as e:
            print("merge failed:", e)

if __name__ == "__main__":
    main()
