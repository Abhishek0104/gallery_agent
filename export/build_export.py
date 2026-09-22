"""
Build the canonical export (docs/export_design.md §2): verified episodes -> HF chat-format JSONL, split by
held-out persona.

    python -m export.build_export     # -> data/export/<version>/{train,eval}.jsonl, eval_ids_<tag>.txt, manifest.json

Model-agnostic: messages + tools in the format tokenizer.apply_chat_template(messages, tools=tools) accepts.
Rendering for a specific base model is export/render_check.py.
"""
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

import yaml

from realize.system_prompt import GUIDANCE_END, GUIDANCE_START

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "export.yaml").read_text())
EPISODES = ROOT / "data" / "episodes"


def to_messages(ep):
    """Episode -> HF chat messages. Tool-call arguments stay objects; tool results are the JSON the model saw."""
    msgs = [{"role": "system", "content": ep["system_prompt"]}]
    for m in ep["messages"]:
        if m["role"] == "user":
            msgs.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            out = {"role": "assistant", "content": m["content"] or ""}
            if m["tool_calls"]:
                out["tool_calls"] = [{"type": "function", "function": {"name": c["name"], "arguments": c["args"]}}
                                     for c in m["tool_calls"]]
            msgs.append(out)
        else:
            msgs.append({"role": "tool", "name": m["name"], "content": json.dumps(m["content"], ensure_ascii=False)})
    return msgs


def to_record(ep, tag, verdict):
    s = ep["spec"]
    return {
        "id": f"{tag}/{ep['episode_id']}",
        "tools": [{"type": "function", "function": t} for t in ep["tools"]],
        "messages": to_messages(ep),
        "meta": {"source": tag, "episode_id": ep["episode_id"], "spec_version": s["spec_version"], "path": s["path"],
                 "persona": s["persona"], "scenario": s.get("scenario"), "style": ep["surface"]["style"],
                 "turn_mode": s["sampling"]["turn_mode"], "verifier_score": verdict["score"],
                 "teacher_guidance_version": ep["teacher_guidance_version"],
                 "models": {"teacher": ep["generation"]["teacher"][0]["served_model"],
                            "user_sim": ep["generation"]["user_sim"][0]["served_model"]}},
    }


def check_record(r):
    """Fail loudly on anything that would silently poison training."""
    blob = json.dumps(r, ensure_ascii=False)
    assert GUIDANCE_START not in blob and GUIDANCE_END not in blob, f"{r['id']}: teacher guidance leaked"
    assert r["messages"][0]["role"] == "system" and r["messages"][1]["role"] == "user", r["id"]
    assert any(m["role"] == "assistant" for m in r["messages"]), f"{r['id']}: no assistant turn"
    for m in r["messages"]:
        if m["role"] == "assistant":
            assert m["content"] or m.get("tool_calls"), f"{r['id']}: empty assistant message"
            assert len(m.get("tool_calls") or []) <= 1, f"{r['id']}: more than one call in a message"


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return None


def main():
    out = ROOT / "data" / "export" / CFG["version"]
    out.mkdir(parents=True, exist_ok=True)
    held = set(CFG["held_out_personas"])
    split = {"train": [], "eval": []}
    eval_ids = {}
    skipped = Counter()
    for src in CFG["sources"]:
        tag, spec_tag = (src, src) if isinstance(src, str) else (src["episodes"], src["specs"])
        eps = [json.loads(l) for l in (EPISODES / f"episodes_{tag}.jsonl").read_text().splitlines()]
        verdicts = {json.loads(l)["episode_id"]: json.loads(l)
                    for l in (EPISODES / f"verified_{tag}.jsonl").read_text().splitlines()}
        for ep in eps:
            v = verdicts.get(ep["episode_id"])
            if not v or not v["accept"]:
                skipped[f"{tag}:not_accepted"] += 1
                continue
            r = to_record(ep, tag, v)
            r["meta"]["cleanup"] = {k: ep["cleanup"][k] for k in ("changed_messages", "backstory_remains")
                                    if k in ep.get("cleanup", {})}
            check_record(r)
            part = "eval" if ep["spec"]["persona"] in held else "train"
            split[part].append(r)
            if part == "eval":
                eval_ids.setdefault(spec_tag, []).append(ep["episode_id"])

    for part, rows in split.items():
        (out / f"{part}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    for tag, ids in eval_ids.items():                    # for re-realizing held-out specs with the trained model
        (out / f"eval_ids_{tag}.txt").write_text("\n".join(ids) + "\n")

    def counts(rows, key):
        return dict(sorted(Counter(key(r) for r in rows).items()))

    scen = lambda r: (r["meta"]["scenario"] or {}).get("type", "happy") + (
        ":" + r["meta"]["scenario"]["variant"] if (r["meta"]["scenario"] or {}).get("variant") else "")
    all_paths = {r["meta"]["path"] for rows in split.values() for r in rows}
    manifest = {
        "version": CFG["version"], "sources": CFG["sources"], "held_out_personas": sorted(held),
        "git_commit": git_commit(),
        "system_prompt_sha": hashlib.sha256(split["train"][0]["messages"][0]["content"].encode()).hexdigest()[:12],
        "skipped": dict(skipped),
        "counts": {part: {"episodes": len(rows),
                          "assistant_turns": sum(m["role"] == "assistant" for r in rows for m in r["messages"]),
                          "by_source": counts(rows, lambda r: r["meta"]["source"]),
                          "by_scenario": counts(rows, scen),
                          "by_persona": counts(rows, lambda r: r["meta"]["persona"]),
                          "paths": len({r["meta"]["path"] for r in rows})}
                   for part, rows in split.items()},
        "eval_paths_missing": len(all_paths - {r["meta"]["path"] for r in split["eval"]}),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    c = manifest["counts"]
    print(f"train {c['train']['episodes']} episodes / {c['train']['assistant_turns']} assistant turns; "
          f"eval {c['eval']['episodes']} / {c['eval']['assistant_turns']}  -> {out}")
    print("eval by scenario:", c["eval"]["by_scenario"])
    print(f"eval covers {c['eval']['paths']} of {len(all_paths)} paths; skipped: {dict(skipped)}")


if __name__ == "__main__":
    main()
