"""
Generate episode specs (v0 happy path): sampler (code) -> intent filler (LLM) -> validator (code)

    python -m specs.make_specs              # generate data/specs/specs_v0.jsonl (cached calls are free)
    python -m specs.make_specs --dry-run    # print the filler prompt for the first spec
    python -m specs.make_specs --review     # write data/specs/review_v0.md from the saved specs

A rejected spec is re-filled with the violations appended to the prompt (a new cache key), up to
--max-attempts. Rejection counts per check go to data/specs/_report.json.
"""
import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from llm import LLM
from specs.intent_filler import build_prompt, fill
from specs.spec_sampler import load_personas, skeleton
from specs.spec_validator import validate

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "specs"
WORKERS = 4


def fill_one(llm, skel, persona, max_attempts):
    feedback, history = (), []
    for attempt in range(max_attempts):
        spec, meta = fill(llm, skel, persona, seed=f"{skel['episode_id']}/a{attempt}", feedback=feedback)
        feedback = validate(spec, persona)
        history.append([m for _, m in feedback])
        if not feedback:
            spec["generation"] = meta
            return spec, history
    return None, history


def generate(max_attempts, dry_run=False, n=None, seed=None, tag="v0"):
    personas = {p["persona_id"]: p for p in load_personas()}
    skels = skeleton(n, seed)
    if dry_run:
        print(build_prompt(skels[0], personas[skels[0]["persona"]]))
        return
    llm = LLM("filler")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda s: fill_one(llm, s, personas[s["persona"]], max_attempts), skels))

    OUT.mkdir(parents=True, exist_ok=True)
    report = {"specs": len(skels), "accepted": 0, "rejections_by_check": Counter(), "episodes": {}}
    accepted = []
    for skel, (spec, history) in zip(skels, results):
        report["episodes"][skel["episode_id"]] = {"accepted": spec is not None, "attempts": len(history),
                                                  "violations": history}
        if spec:
            accepted.append(spec)
        else:
            print(f"{skel['episode_id']}: FAILED after {max_attempts} attempts: {history[-1]}")
    for skel, (spec, history) in zip(skels, results):
        for msgs in history[:-1] if spec else history:
            report["rejections_by_check"].update({classify(m) for m in msgs})
    report["accepted"] = len(accepted)
    (OUT / f"specs_{tag}.jsonl").write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in accepted))
    (OUT / f"_report_{tag}.json" if tag != "v0" else OUT / "_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    report["album_flipped"] = [s["episode_id"] for s in accepted if s["sampling"].get("album_flipped")]
    first_try = sum(1 for _, (s, h) in zip(skels, results) if s and len(h) == 1)
    print(f"accepted {len(accepted)}/{len(skels)} ({first_try} on the first attempt); "
          f"rejections by check: {dict(report['rejections_by_check'])}; "
          f"existing album -> new (none fit): {report['album_flipped']}")
    write_review(accepted, personas, tag)


def classify(msg):
    """Map a violation message back to its check (messages are prefixed by step, then the reason)."""
    keys = {"query ": "purity", "slots": "slots", "refinement": "slots", "is not me": "people",
            "not self-contained": "ask", "answer": "ask", "effect ": "effect", "album": "album",
            "turn": "turns", "count": "counts", "collage": "counts", "selected": "counts"}
    return next((v for k, v in keys.items() if k in msg), "schema")


# ---------------------------------------------------------------- review
def fmt_args(args):
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())


def write_review(specs, personas, tag="v0"):
    lines = ["# Episode specs v0 — review", "",
             f"{len(specs)} specs. Per spec: persona, path, motivation, steps (args → outcome), user turns.", ""]
    for sp in specs:
        p = personas[sp["persona"]]
        lines.append(f"## {sp['episode_id']} · {sp['path']} · {p['owner']['name']} ({p['persona_id']}, "
                     f"{p['region']}) · collage_max {sp['config']['collage_max']} · "
                     f"effects {', '.join(sp['config']['effects'])} · turns {sp['sampling']['turn_mode']}")
        lines.append(f"*{sp['motivation']}*")
        lines.append("")
        if sp["initial_selection"]:
            lines.append(f"- 0. [starts with {sp['initial_selection']['count']} photos selected → r0]")
        for s in sp["steps"]:
            if "event" in s:
                lines.append(f"- {s['i']}. [user selects {s['count']} from {s['from']} → {s['out']}]")
                continue
            o = {k: v for k, v in s["outcome"].items() if k != "status"}
            ref = f" *(refines {s['refines']})*" if "refines" in s else ""
            lines.append(f"- {s['i']}. `{s['call']}({fmt_args(s['args'])})` → {json.dumps(o, ensure_ascii=False)}{ref}")
        lines.append(f"- turns: {sp['turns']}")
        hints = [f"step {f['step']}: {f['category']}/{f['length']} e.g. {f['examples']}"
                 for f in sp["fill"] if f["field"] == "query"]
        if hints:
            lines.append(f"- query hints: {'; '.join(hints)}")
        lines.append("")
    (OUT / f"review_{tag}.md").write_text("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--n", type=int, help="number of specs (default: config)")
    ap.add_argument("--seed", type=int, help="sampler seed (default: config)")
    ap.add_argument("--tag", default="v0", help="output data/specs/specs_<tag>.jsonl")
    args = ap.parse_args()
    if args.review:
        write_review([json.loads(l) for l in (OUT / f"specs_{args.tag}.jsonl").read_text().splitlines()],
                     {p["persona_id"]: p for p in load_personas()}, args.tag)
    else:
        generate(args.max_attempts, args.dry_run, args.n, args.seed, args.tag)
