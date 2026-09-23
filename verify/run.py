"""
Verify realized episodes against their specs.

    python -m verify.run     # data/episodes/episodes_v0.jsonl -> data/episodes/verified_v0.jsonl + summary

Calibrating another embedding model (the query/question thresholds were tuned on gemini-embedding-2): re-verify a
batch already verified with Gemini into a new file and compare, e.g.
    LLM_OVERLAY=config/llm_local.yaml python -m verify.run --tag v2c --out-tag v2c_localemb --compare v2c
"""
import json
from collections import Counter
from pathlib import Path

from llm import LLM
from verify.verifier import verify

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "data" / "episodes"


def main(tag="v0", quiet=False, out_tag=None, compare=None):
    episodes = [json.loads(l) for l in (EPISODES / f"episodes_{tag}.jsonl").read_text().splitlines()]
    personas = {json.loads(f.read_text())["persona_id"]: json.loads(f.read_text())
                for f in (ROOT / "data" / "personas").glob("persona_*.json")}
    emb = LLM("embedding")
    verdicts = [verify(ep, personas[ep["spec"]["persona"]], emb.embed) for ep in episodes]
    for v in verdicts:
        v["embedding_model"] = f"{emb.provider}:{emb.model}"     # the similarity checks depend on it
    (EPISODES / f"verified_{out_tag or tag}.jsonl").write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in verdicts))

    print(f"{'episode':9} {'score':>5}  accept  groups")
    for v in verdicts if not quiet else [v for v in verdicts if not v["accept"]]:
        g = " ".join(f"{k}={s:.2f}" for k, s in v["groups"].items())
        print(f"{v['episode_id']:9} {v['score']:5.2f}  {'yes' if v['accept'] else 'NO ':6}  {g}")
        for f in v["failures"]:
            print(f"            - {f}")
    failed = Counter(c["check"].split(":")[0] for v in verdicts for c in v["checks"] if not c["pass"])
    sims = sorted(c["score"] for v in verdicts for c in v["checks"] if c["check"].startswith(("query_sim", "question_sim")))
    print(f"\naccepted {sum(v['accept'] for v in verdicts)}/{len(verdicts)}; "
          f"mean score {sum(v['score'] for v in verdicts) / len(verdicts):.3f}")
    print("failed checks:", dict(failed))
    print("query/question similarity:", sims)
    print("embedding:", f"{emb.provider}:{emb.model}")
    if compare:
        compare_verdicts(verdicts, [json.loads(l) for l in (EPISODES / f"verified_{compare}.jsonl").read_text().splitlines()],
                         compare)


def sim_scores(v):
    return {c["check"]: c["score"] for c in v["checks"] if c["check"].startswith(("query_sim", "question_sim"))}


def compare_verdicts(new, old, old_tag):
    """Agreement with an earlier verification of the same episodes (another embedding model)."""
    old = {v["episode_id"]: v for v in old}
    pairs = [(v, old[v["episode_id"]]) for v in new if v["episode_id"] in old]
    if not pairs:
        print(f"\ncompare: no episode in common with verified_{old_tag}")
        return
    flips = [(n["episode_id"], o["accept"], n["accept"]) for n, o in pairs if n["accept"] != o["accept"]]
    sims = [(sim_scores(o)[k], s) for n, o in pairs for k, s in sim_scores(n).items() if k in sim_scores(o)]
    print(f"\ncompare with verified_{old_tag} ({pairs[0][1].get('embedding_model', 'unrecorded: gemini-embedding-2 before this field')}), "
          f"{len(pairs)} episodes:")
    print(f"  accept agrees {len(pairs) - len(flips)}/{len(pairs)}; flips: {flips or 'none'}")
    print(f"  mean |score diff| {sum(abs(n['score'] - o['score']) for n, o in pairs) / len(pairs):.4f}")
    if sims:
        print("  query/question cosines, old -> new (sorted by old):")
        for a, b in sorted(sims):
            print(f"    {a:.3f} -> {b:.3f}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v0")
    ap.add_argument("--quiet", action="store_true", help="only list rejected episodes")
    ap.add_argument("--out-tag", help="write verified_<out-tag>.jsonl instead of verified_<tag>.jsonl")
    ap.add_argument("--compare", help="compare with verified_<tag>.jsonl (e.g. the same batch, another embedding)")
    args = ap.parse_args()
    main(args.tag, args.quiet, args.out_tag, args.compare)
