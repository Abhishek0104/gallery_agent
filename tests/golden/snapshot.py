"""
Golden snapshot: a hash of every artifact the current code produces from the committed data, with no LLM calls.

    python -m tests.golden.snapshot --write     # record tests/golden/golden.json (only when a change is intended)

tests/test_golden.py recomputes it under several PYTHONHASHSEED values and compares with golden.json, so a
refactor that changes any prompt, skeleton, simulator output, verdict or export record fails loudly. Prompts
matter most: every LLM call is cached by its prompt, so a changed byte means regenerating that stage.
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).with_name("golden.json")
DATA = ROOT / "data"


def h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def fake_embed(texts):
    """Deterministic stand-in for the embedding model: identical texts -> cosine 1, else 0."""
    import numpy as np
    uniq = sorted(set(texts))
    v = np.zeros((len(texts), max(1, len(uniq))), dtype=np.float32)
    for i, t in enumerate(texts):
        v[i, uniq.index(t)] = 1.0
    return v


def snapshot():
    from catalog.generate_catalog import build_catalog
    from export.build_export import to_record
    from realize.cleanup import PROMPT as CLEANUP_PROMPT, text_items
    from realize.run import CFG as RCFG, history_text
    from realize.system_prompt import system_prompt, teacher_system_prompt, tool_declarations
    from realize.user_sim import build_user_prompt, sample_surface
    from specs.intent_filler import build_prompt as filler_prompt
    from specs.make_personas import build_prompt as persona_prompt
    from specs.make_query_pool import calls as pool_calls
    from specs.persona_outline import quota_list, sample_outlines
    from specs.scenarios import round2_skeleton
    from specs.spec_sampler import load_catalog, load_personas, skeleton
    from specs.spec_validator import validate
    from verify.verifier import replay, verify

    personas = {p["persona_id"]: p for p in load_personas()}
    out = {}

    # catalog + personas + query pool plan
    out["catalog.build"] = h([{k: c[k] for k in ("id", "initial", "calls", "path")} for c in build_catalog()])
    out["catalog.loaded"] = h(load_catalog())
    out["personas.outlines"] = h(sample_outlines())
    out["personas.prompts"] = h([persona_prompt(o, {"a", "b"}) for o in sample_outlines()])
    out["query_pool.prompts"] = h([c[3] for c in pool_calls()])

    # skeletons and filler prompts (v0: 50 @ 11, v1/v2: 200 @ 101, round 2 as generated for r2b)
    skels = {"v0": skeleton(50, 11), "v2": skeleton(200, 101), "r2b": round2_skeleton()}
    for tag, sk in skels.items():
        out[f"skeleton.{tag}"] = h(sk)
        out[f"filler_prompts.{tag}"] = h([filler_prompt(s, personas[s["persona"]]) for s in sk])

    # committed specs still validate the same way; tool declarations + system prompts
    for tag in ("v2", "r2b"):
        specs = jsonl(DATA / "specs" / f"specs_{tag}.jsonl")
        out[f"validate.{tag}"] = h([validate(s, personas[s["persona"]]) for s in specs])
        out[f"tools.{tag}"] = h([tool_declarations(s["config"]) for s in specs])
        # surface forms realize.run samples for these specs (styles and per-episode seeds as in main)
        rng = random.Random(RCFG["seed"])
        styles = quota_list(RCFG["style"], len(specs), rng)
        seeds = [rng.randrange(2 ** 31) for _ in specs]
        out[f"surfaces.{tag}"] = h([sample_surface(s, personas[s["persona"]], st, random.Random(sd))
                                    for s, st, sd in zip(specs, styles, seeds)])
    out["system_prompt"] = h(system_prompt())
    out["teacher_system_prompt"] = h(teacher_system_prompt())

    # episodes: user-sim prompts, simulator replay, verifier, export records, cleanup prompts
    for tag in ("v0", "v2c", "r2b"):
        eps = jsonl(DATA / "episodes" / f"episodes_{tag}.jsonl")
        prompts, sims, verdicts, records = [], [], [], []
        for e in eps:
            spec, p = e["spec"], personas[e["spec"]["persona"]]
            users = [k for k, m in enumerate(e["messages"]) if m["role"] == "user"]
            if len(users) == len(spec["turns"]):
                prompts += [build_user_prompt(spec, p, t, e["surface"], history_text(e["messages"][:k]))
                            for k, t in zip(users, spec["turns"])]
            sim = replay(e)
            recorded = [m["content"] for m in e["messages"] if m["role"] == "tool"]
            sims.append({"map": sim.map, "flags": sim.flags, "done": sim.done(), "ledger": sim.ledger})
            rerun = _replay_results(e)
            assert rerun == recorded, f"{tag}/{e['episode_id']}: simulator results differ from the recorded ones"
            verdicts.append(verify(e, p, fake_embed))
            records.append(to_record(e, tag, {"score": 1.0, "accept": True}))
        out[f"user_prompts.{tag}"] = h(prompts)
        out[f"sim.{tag}"] = h(sims)
        out[f"verdicts.{tag}"] = h(verdicts)
        out[f"export_records.{tag}"] = h(records)
    v2 = jsonl(DATA / "episodes" / "episodes_v2.jsonl")
    out["cleanup_prompts.v2"] = h([CLEANUP_PROMPT.substitute(
        messages="\n".join(f"[{n}] {r.upper()}: {t}" for n, (_, r, t) in enumerate(text_items(e), 1)), feedback="")
        for e in v2])
    return out


def _replay_results(ep):
    """Tool results the simulator returns for the episode's recorded calls (selection lines applied in order)."""
    from sim.simulator import Simulator
    from verify.verifier import SELECTION

    sim = Simulator(ep["spec"])
    selects = iter([s for s in ep["spec"]["steps"] if "event" in s])
    results = []
    for m in ep["messages"]:
        if m["role"] == "user":
            for hnd in SELECTION.findall(m["content"] or ""):
                if hnd != "r0":
                    sim.select(next(selects))
        elif m["role"] == "assistant":
            for c in m["tool_calls"] or []:
                results.append(sim.call(c["name"], c["args"]))
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="record golden.json (only for an intended change)")
    args = ap.parse_args()
    snap = snapshot()
    if args.write:
        GOLDEN.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
        print(f"wrote {len(snap)} hashes to {GOLDEN}")
    else:
        print(json.dumps(snap, sort_keys=True))
