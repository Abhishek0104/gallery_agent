"""
Verify realized episodes against their specs.

    python -m verify.run     # data/episodes/episodes_v0.jsonl -> data/episodes/verified_v0.jsonl + summary
"""
import json
from collections import Counter
from pathlib import Path

from llm import LLM
from verify.verifier import verify

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "data" / "episodes" / "episodes_v0.jsonl"
OUT = ROOT / "data" / "episodes" / "verified_v0.jsonl"


def main():
    episodes = [json.loads(l) for l in EPISODES.read_text().splitlines()]
    personas = {json.loads(f.read_text())["persona_id"]: json.loads(f.read_text())
                for f in (ROOT / "data" / "personas").glob("persona_*.json")}
    emb = LLM("embedding")
    verdicts = [verify(ep, personas[ep["spec"]["persona"]], emb.embed) for ep in episodes]
    OUT.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in verdicts))

    print(f"{'episode':9} {'score':>5}  accept  groups")
    for v in verdicts:
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


if __name__ == "__main__":
    main()
