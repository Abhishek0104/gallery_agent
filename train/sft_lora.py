"""
LoRA SFT on the per-turn prompt/completion data from export/render_check.py. Loss on completion tokens only.

    python -m train.sft_lora --check     # tokenize + report (no weights, no GPU) — safe anywhere
    python -m train.sft_lora             # train (CUDA machine); saves the adapter to output_dir

Prompt and completion are tokenized separately and concatenated, which is exactly how inference sees them
(the prompt is tokenized alone, then the model generates). Text-only: AutoModelForCausalLM loads Qwen3.5 as
Qwen3_5ForCausalLM (no vision tower).
"""
import argparse
import json
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "train.yaml").read_text())
IGNORE = -100


def load_rows(part):
    return [json.loads(l) for l in (ROOT / CFG["data"] / f"sft_{part}.jsonl").read_text().splitlines()]


def encode(tok, row, max_length):
    p = tok(row["prompt"], add_special_tokens=False)["input_ids"]
    c = tok(row["completion"], add_special_tokens=False)["input_ids"]
    if len(p) + len(c) > max_length:
        raise ValueError(f"{row['id']} turn {row['turn']}: {len(p) + len(c)} tokens > max_length {max_length}")
    return {"input_ids": p + c, "labels": [IGNORE] * len(p) + c}


class Collator:
    def __init__(self, pad_id):
        self.pad_id = pad_id

    def __call__(self, batch):
        import torch

        n = max(len(b["input_ids"]) for b in batch)
        ids = [b["input_ids"] + [self.pad_id] * (n - len(b["input_ids"])) for b in batch]
        labels = [b["labels"] + [IGNORE] * (n - len(b["labels"])) for b in batch]
        mask = [[1] * len(b["input_ids"]) + [0] * (n - len(b["input_ids"])) for b in batch]
        return {"input_ids": torch.tensor(ids), "labels": torch.tensor(labels), "attention_mask": torch.tensor(mask)}


def main(check=False):
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(CFG["base_model"])
    pad_id = tok.pad_token_id if tok.pad_token_id is not None else tok.convert_tokens_to_ids("<|endoftext|>")
    train = [encode(tok, r, CFG["max_length"]) for r in load_rows("train")]
    evals = [encode(tok, r, CFG["max_length"]) for r in load_rows("eval")]
    trained = sum(sum(t != IGNORE for t in x["labels"]) for x in train)
    steps = math.ceil(len(train) / (CFG["batch_size"] * CFG["grad_accum"])) * CFG["epochs"]
    print(f"train {len(train)} examples ({trained} completion tokens), eval {len(evals)}; "
          f"~{steps} optimizer steps; longest {max(len(x['input_ids']) for x in train + evals)} tokens; pad id {pad_id}")
    batch = Collator(pad_id)(train[:2])
    assert (batch["labels"][0] != IGNORE).sum() > 0 and batch["input_ids"].shape == batch["labels"].shape
    if check:
        print("check ok: data, masking and collator build; no model loaded")
        return

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, Trainer, TrainingArguments

    model = AutoModelForCausalLM.from_pretrained(CFG["base_model"],
                                                 dtype=torch.bfloat16 if CFG["bf16"] else torch.float32)
    lc = CFG["lora"]
    model = get_peft_model(model, LoraConfig(r=lc["r"], lora_alpha=lc["alpha"], lora_dropout=lc["dropout"],
                                             target_modules=lc["target_modules"], task_type="CAUSAL_LM"))
    model.print_trainable_parameters()
    args = TrainingArguments(
        output_dir=str(ROOT / CFG["output_dir"]), num_train_epochs=CFG["epochs"], learning_rate=CFG["lr"],
        per_device_train_batch_size=CFG["batch_size"], per_device_eval_batch_size=CFG["batch_size"],
        gradient_accumulation_steps=CFG["grad_accum"], warmup_ratio=CFG["warmup_ratio"],
        weight_decay=CFG["weight_decay"], bf16=CFG["bf16"], seed=CFG["seed"], logging_steps=CFG["logging_steps"],
        eval_strategy="steps", eval_steps=CFG["eval_steps"], save_strategy="epoch", report_to=[],
        remove_unused_columns=False)
    trainer = Trainer(model=model, args=args, train_dataset=train, eval_dataset=evals, data_collator=Collator(pad_id))
    trainer.train()
    model.save_pretrained(ROOT / CFG["output_dir"] / "adapter")
    tok.save_pretrained(ROOT / CFG["output_dir"] / "adapter")
    print("adapter saved to", ROOT / CFG["output_dir"] / "adapter")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="build data and collator only; no model, no training")
    main(ap.parse_args().check)
