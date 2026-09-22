"""
Teacher-forced eval (docs/export_design.md §5.2): predict each assistant turn from the gold history.

    python -m export.eval_forced                              # base model, data/export/<version>/sft_eval.jsonl
    python -m export.eval_forced --adapter runs/e2e_v2_lora/adapter
    python -m export.eval_forced --limit 40 --device cpu      # smoke test

Unlike the interactive eval (realize.run --assistant-role student + verify.run), this is a pure function of
the model and the exported data: no simulator, no user simulator, no embedding API. It cannot see error
recovery or exposure bias — the history is always gold — so it is the secondary metric, cheap enough to run
per checkpoint and the only one that isolates the model from the Gemini user simulator.

Scoring mirrors the verifier where it can. The headline number is **structural**: tool name + `images` (the
handle, i.e. whether the model tracked conversation state) + the exactly-compared args, normalized by
config/arg_types.yaml. `query` / `question` are compared by cosine at 0.75 in the verifier; with no embedding
model here they are reported as exact-string rates only, and nothing is gated on them.
"""
import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from export.render_check import END, parse_completion
from registry.args import exact_args, normalized, semantic_args
from verify.verifier import HANDLE

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "export.yaml").read_text())
EXACT = exact_args()             # people, location, date, effect, album
SEMANTIC = semantic_args()       # query, question — diagnostic only
STRUCTURAL = ["images"] + EXACT  # `images` is in neither list, and it is the state-tracking signal


def norm(key, v):
    return normalized(key, v) if key in EXACT else v.strip() if isinstance(v, str) else v


def score_row(row, pred_text, tools):
    """One row -> per-metric booleans (None = not applicable to this row)."""
    gold_kind, gold_a, gold_b = parse_completion(row["completion"], tools)
    try:
        pred_kind, pred_a, pred_b = parse_completion(pred_text, tools)
    except Exception:            # more than one call, or unparseable XML
        pred_kind, pred_a, pred_b = "malformed", "", None
    m = {"kind": pred_kind == gold_kind, "truncated": not pred_text.rstrip().endswith(END),
         "malformed": pred_kind == "malformed"}

    if gold_kind == "call":
        m["name"] = pred_kind == "call" and pred_a == gold_a
        g, p = gold_b or {}, pred_b or {}
        keys = {k for k in list(g) + list(p) if k in STRUCTURAL}
        m["args"] = m["name"] and all(k in g and k in p and norm(k, g[k]) == norm(k, p[k]) for k in keys)
        # `images` only applies to the tools that take a handle (search_images does not).
        m["images"] = (m["name"] and g["images"] == (p.get("images") or "").strip()) if "images" in g else None
        m["structural"] = bool(m["args"])
        for k in SEMANTIC:
            if k in g:
                m[f"{k}_exact"] = m["name"] and g[k].strip().lower() == (p.get(k) or "").strip().lower()
    else:
        # Reply wording is free (the verifier never string-matches it); check what it does check. Only when
        # the model actually produced text — otherwise `kind` already counts it and these would double-count.
        body = pred_a if pred_kind == "text" else None
        m["reply_nonempty"] = bool(body.strip()) if body is not None else None
        m["reply_no_handles"] = (not HANDLE.search(body)) if body is not None else None
    return m


def aggregate(scored):
    """rows of (row, tool, metrics) -> {metric: {n, hits, rate}} overall and per group."""
    def table(rows):
        acc = defaultdict(lambda: [0, 0])
        for m in rows:
            for k, v in m.items():
                if v is None:
                    continue
                acc[k][0] += 1
                acc[k][1] += bool(v)
        return {k: {"n": n, "hits": h, "rate": round(h / n, 3)} for k, (n, h) in acc.items()}

    by_kind, by_tool = defaultdict(list), defaultdict(list)
    for row, tool, m in scored:
        by_kind[row["kind"]].append(m)
        if tool:
            by_tool[tool].append(m)
    return {"overall": table([m for _, _, m in scored]),
            "by_kind": {k: table(v) for k, v in sorted(by_kind.items())},
            "by_tool": {k: table(v) for k, v in sorted(by_tool.items())}}


# ---------------------------------------------------------------- model
def load_model(base, adapter, device, dtype):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    torch_dtype = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}[dtype]
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, dtype=torch_dtype).to(device).eval()
    if adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter).to(device).eval()
    return tok, model, device


def predictor(tok, model, device, max_new_tokens):
    """(prompt, gold_completion) -> (predicted text, completion loss). Two passes: generation can't
    reuse the loss pass, which is conditioned on the gold tokens."""
    import torch

    eos = tok.convert_tokens_to_ids(END)

    def run(prompt, gold):
        p = tok(prompt, add_special_tokens=False, return_tensors="pt").to(device)   # as in train/sft_lora
        g = tok(gold, add_special_tokens=False)["input_ids"]
        with torch.no_grad():
            ids = torch.cat([p["input_ids"], torch.tensor([g], device=device)], dim=1)
            labels = ids.clone()
            labels[:, : p["input_ids"].shape[1]] = -100
            loss = float(model(input_ids=ids, labels=labels).loss)
            out = model.generate(**p, max_new_tokens=max_new_tokens, do_sample=False,
                                 eos_token_id=eos, pad_token_id=eos)
        return tok.decode(out[0][p["input_ids"].shape[1]:], skip_special_tokens=False), loss

    return run


# ---------------------------------------------------------------- run
def run(rows, tools_by_id, predict):
    """-> (report, scored, predictions). `predictions` is one record per row: what the model actually
    produced next to the gold, so a number can always be traced back to a turn."""
    scored, predictions, losses = [], [], []
    for i, row in enumerate(rows, 1):
        tools = tools_by_id[row["id"]]
        pred, loss = predict(row["prompt"], row["completion"])
        m = score_row(row, pred, tools)
        gold_kind, gold_name, _ = parse_completion(row["completion"], tools)
        tool = gold_name if gold_kind == "call" else None
        scored.append((row, tool, m))
        predictions.append({"id": row["id"], "turn": row["turn"], "kind": row["kind"], "tool": tool,
                            "gold": row["completion"], "pred": pred,
                            "loss": round(loss, 4) if loss is not None else None,
                            "ok": all(v for k, v in m.items() if k not in ("truncated", "malformed") and v is not None),
                            "metrics": {k: v for k, v in m.items() if v is not None}})
        if loss is not None:
            losses.append(loss)
        if i % 25 == 0:
            print(f"  {i}/{len(rows)}", flush=True)
    report = aggregate(scored)
    if losses:
        report["completion_loss"] = round(sum(losses) / len(losses), 4)
    return report, scored, predictions


def review(predictions, meta):
    """Markdown of the misses only, gold next to prediction — the file you actually read after a run."""
    bad = [p for p in predictions if not p["ok"]]
    out = [f"# Teacher-forced misses — {meta['base_model']}"
           f"{' + ' + meta['adapter'] if meta['adapter'] else ' (untrained base)'}",
           f"\n{len(bad)} of {len(predictions)} turns missed at least one metric "
           f"({meta['version']} {meta['split']}, {meta['device']} {meta['dtype']}).\n"]
    by_tool = defaultdict(list)
    for p in bad:
        by_tool[p["tool"] or "(reply)"].append(p)
    for tool, ps in sorted(by_tool.items(), key=lambda kv: -len(kv[1])):
        out.append(f"\n## {tool} — {len(ps)} miss(es)\n")
        for p in ps:
            failed = [k for k, v in p["metrics"].items() if (v if k in ("truncated", "malformed") else not v)]
            out.append(f"### {p['id']} turn {p['turn']}  ({', '.join(failed)})\n")
            out.append(f"gold:\n```\n{p['gold'].strip()}\n```\n")
            out.append(f"pred:\n```\n{p['pred'].strip()}\n```\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="eval", choices=("eval", "train"))
    ap.add_argument("--adapter", help="LoRA adapter dir; omit to evaluate the untrained base model")
    ap.add_argument("--device", default="auto", choices=("auto", "cuda", "mps", "cpu"))
    ap.add_argument("--dtype", default="float16", choices=("float32", "float16", "bfloat16"))
    ap.add_argument("--limit", type=int, help="only the first N rows (one or two episodes; smoke test)")
    ap.add_argument("--sample", type=int, help="N rows sampled across the split (spans tools; seeded)")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--max-new-tokens", type=int, default=160)
    ap.add_argument("--out", help="report path (default data/export/<version>/forced_<split>_<name>.json)")
    args = ap.parse_args()

    out_dir = ROOT / "data" / "export" / CFG["version"]
    rows = [json.loads(l) for l in (out_dir / f"sft_{args.split}.jsonl").read_text().splitlines()]
    tools_by_id = {json.loads(l)["id"]: json.loads(l)["tools"]
                   for l in (out_dir / f"{args.split}.jsonl").read_text().splitlines()}
    if args.sample:
        rows = sorted(random.Random(args.seed).sample(rows, min(args.sample, len(rows))),
                      key=lambda r: (r["id"], r["turn"]))
    elif args.limit:
        rows = rows[: args.limit]

    tok, model, device = load_model(CFG["base_model"], args.adapter, args.device, args.dtype)
    name = Path(args.adapter).parent.name if args.adapter else "base"
    if args.sample or args.limit:
        name += f"_{args.sample or args.limit}"
    print(f"{CFG['version']} {args.split}: {len(rows)} rows | {CFG['base_model']}"
          f"{' + ' + args.adapter if args.adapter else ' (untrained base)'} | {device} {args.dtype}")
    report, _, predictions = run(rows, tools_by_id, predictor(tok, model, device, args.max_new_tokens))
    report["meta"] = meta = {"version": CFG["version"], "split": args.split, "rows": len(rows),
                             "base_model": CFG["base_model"], "adapter": args.adapter,
                             "device": device, "dtype": args.dtype, "max_new_tokens": args.max_new_tokens,
                             "sample": args.sample, "seed": args.seed if args.sample else None}
    path = Path(args.out) if args.out else out_dir / f"forced_{args.split}_{name}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    preds = path.with_suffix(".jsonl")
    preds.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in predictions))
    md = path.with_suffix(".md")
    md.write_text(review(predictions, meta))

    print(f"\n{'metric':18} {'rate':>6}  n     (headline: structural)")
    for k, v in report["overall"].items():
        print(f"  {k:16} {v['rate']:6.3f}  {v['n']}")
    print(f"completion loss: {report.get('completion_loss')}")
    for group in ("by_kind", "by_tool"):
        print(f"\n{group}:")
        for g, t in report[group].items():
            key = "structural" if "structural" in t else "reply_nonempty"
            print(f"  {g:16} {key}={t[key]['rate']:.3f} kind={t['kind']['rate']:.3f} (n={t['kind']['n']})")
    miss = sum(not p["ok"] for p in predictions)
    print(f"\nwrote {path.relative_to(ROOT)} (scores), {preds.name} (every prediction next to its gold), "
          f"{md.name} ({miss} miss{'es' if miss != 1 else ''}, gold vs pred)")


if __name__ == "__main__":
    main()
