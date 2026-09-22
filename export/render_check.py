"""
Render check + per-turn SFT data for the base model (docs/export_design.md §3).

    python -m export.render_check      # tokenizer only; no model weights, no training

For every assistant turn k of every exported episode:
    prompt     = template(messages[:k], tools, add_generation_prompt=True, enable_thinking=False)
    completion = template(messages[:k+1], tools, enable_thinking=False)[len(prompt):]
This is exactly what the model sees and must produce at inference. (Whole-episode rendering is not: Qwen3.5
renders earlier assistant turns without the empty <think></think> block that the generation prompt adds.)

Checks (any failure raises): the prompt is a prefix of the next render; the prompt ends with the non-thinking
generation marker; the completion parses back to exactly the gold tool call (name + arguments) or reply text;
no reasoning inside completions; no teacher guidance anywhere.
Writes data/export/<version>/sft_{train,eval}.jsonl ({id, turn, prompt, completion}) and render_report.json.
"""
import json
import re
from collections import Counter
from pathlib import Path

import yaml

from realize.system_prompt import GUIDANCE_START

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "export.yaml").read_text())
TOOL_CALL = re.compile(r"<tool_call>\s*<function=([^>\s]+)>\s*(.*?)\s*</function>\s*</tool_call>", re.S)
PARAM = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", re.S)
END = "<|im_end|>"


class RenderError(AssertionError):
    pass


def parse_completion(text, tools=()):
    """Qwen3.5 completion -> ("call", name, args) or ("text", reply). The XML parameter format loses types
    ("2022" vs 2022), so values are converted by the tool's JSON schema, as vLLM's Qwen XML parser does:
    string params stay text; array / object / number / boolean params are parsed as JSON."""
    body = text[:-len(END)] if text.endswith(END) else text
    calls = TOOL_CALL.findall(body)
    if not calls:
        return "text", body.strip(), None
    if len(calls) > 1:
        raise RenderError(f"more than one tool call: {body!r}")
    name, params = calls[0]
    props = next((t["function"]["parameters"].get("properties", {}) for t in tools
                  if t["function"]["name"] == name), {})
    args = {}
    for k, v in PARAM.findall(params):
        if props.get(k, {}).get("type") == "string":
            args[k] = v
            continue
        try:
            args[k] = json.loads(v)
        except json.JSONDecodeError:
            args[k] = v
    return "call", name, args


def turn_examples(tok, record, kwargs):
    msgs, tools = record["messages"], record["tools"]
    marker = generation_marker(tok, kwargs)
    out = []
    for k, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        prompt = tok.apply_chat_template(msgs[:k], tools=tools, tokenize=False, add_generation_prompt=True, **kwargs)
        upto = tok.apply_chat_template(msgs[:k + 1], tools=tools, tokenize=False, **kwargs)
        where = f"{record['id']} turn {k}"
        if not upto.startswith(prompt):
            raise RenderError(f"{where}: generation prompt is not a prefix of the rendered turn")
        if not prompt.endswith(marker):
            raise RenderError(f"{where}: prompt does not end with the non-thinking marker {marker!r}")
        completion = upto[len(prompt):].rstrip("\n")
        if not completion.endswith(END):
            raise RenderError(f"{where}: completion does not end with {END}")
        if "<think>" in completion:
            raise RenderError(f"{where}: reasoning inside the completion")
        kind, a, b = parse_completion(completion, tools)
        if m.get("tool_calls"):
            gold = m["tool_calls"][0]["function"]
            if kind != "call" or a != gold["name"] or b != gold["arguments"]:
                raise RenderError(f"{where}: tool call does not round-trip: {gold} -> {(kind, a, b)}")
        elif kind != "text" or a != m["content"].strip():
            raise RenderError(f"{where}: reply does not round-trip: {m['content']!r} -> {a!r}")
        out.append({"id": record["id"], "turn": k, "kind": "call" if m.get("tool_calls") else "reply",
                    "prompt": prompt, "completion": completion})
    return out


def generation_marker(tok, kwargs):
    """What the template appends after '<|im_start|>assistant' at generation time (non-thinking: empty think)."""
    s = tok.apply_chat_template([{"role": "user", "content": "x"}], tokenize=False, add_generation_prompt=True, **kwargs)
    return s[s.rindex("<|im_start|>assistant"):]


def main():
    from transformers import AutoTokenizer

    out = ROOT / "data" / "export" / CFG["version"]
    tok = AutoTokenizer.from_pretrained(CFG["base_model"])
    kwargs = CFG.get("chat_template_kwargs") or {}
    report = {"base_model": CFG["base_model"], "chat_template_kwargs": kwargs,
              "generation_marker": generation_marker(tok, kwargs), "splits": {}}
    for part in ("train", "eval"):
        records = [json.loads(l) for l in (out / f"{part}.jsonl").read_text().splitlines()]
        rows = []
        for r in records:
            if GUIDANCE_START in json.dumps(r, ensure_ascii=False):
                raise RenderError(f"{r['id']}: teacher guidance leaked")
            rows += turn_examples(tok, r, kwargs)
        lens = [len(tok(x["prompt"] + x["completion"])["input_ids"]) for x in rows]
        comp = [len(tok(x["completion"])["input_ids"]) for x in rows]
        (out / f"sft_{part}.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
        report["splits"][part] = {"episodes": len(records), "examples": len(rows),
                                  "by_kind": dict(Counter(x["kind"] for x in rows)),
                                  "tokens_max": max(lens), "tokens_mean": round(sum(lens) / len(lens)),
                                  "completion_tokens_max": max(comp), "completion_tokens_total": sum(comp)}
    (out / "render_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    for part, r in report["splits"].items():
        print(f"{part}: {r['episodes']} episodes -> {r['examples']} examples {r['by_kind']}; "
              f"tokens max {r['tokens_max']} mean {r['tokens_mean']}; completion tokens {r['completion_tokens_total']}")
    print("all turns round-trip; generation marker:", repr(report["generation_marker"]))


if __name__ == "__main__":
    main()
