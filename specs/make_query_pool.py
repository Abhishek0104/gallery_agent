"""
Build the query pool: brainstorm (LLM, per category x facet, plus region-flavored calls)
-> purity check (code) -> exact + embedding dedup (code) -> data/query_pool.json

    python -m specs.make_query_pool            # build (cached calls are free)
    python -m specs.make_query_pool --dry-run  # print one prompt, no LLM call
    python -m specs.make_query_pool --show     # print a sample per category

Rejected items, near-duplicate pairs and review-band pairs go to data/query_pool_report.json.
"""
import argparse
import json
import random
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Template
from typing import List

import yaml
from pydantic import BaseModel

from llm import LLM
from specs.query_purity import check_query, persona_name_hits

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "query_pool.yaml").read_text())
PROMPT = Template((ROOT / "specs" / "prompts" / "query_pool.txt").read_text())
OUT = ROOT / "data" / "query_pool.json"
REPORT = ROOT / "data" / "query_pool_report.json"
VERSION = 1
WORKERS = 4                     # parallel brainstorm calls; results keep plan order

PEOPLE_RULE = {
    "required": "Each item follows a person's name, e.g. \"<name> hugging\", \"<name> with a dog\". "
                "Write only the part after the name.",
    "optional": "Items may mention people generically (\"kids\", \"a man\", \"two girls\") but never specific people.",
    "never": "Documents only; no people.",
}
REGION_LABEL = {"india": "India", "us": "the United States", "uk": "the United Kingdom",
                "australia": "Australia", "canada": "Canada", "singapore": "Singapore", "uae": "the UAE"}


class QueryItems(BaseModel):
    short: List[str]
    medium: List[str]
    long: List[str]


BUCKETS = CFG["length"]["buckets"]


def split_counts(n):
    """Items per length bucket for a call of n items (largest remainder)."""
    split = CFG["length"]["split"]
    raw = {k: n * w for k, w in split.items()}
    counts = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[:n - sum(counts.values())]:
        counts[k] += 1
    return counts


def length_bucket(text):
    n = len(text.split())
    return next((k for k, (lo, hi) in BUCKETS.items() if lo <= n <= hi), "long")


def calls():
    """(category, facet, regions, prompt, seed) for every brainstorm call."""
    out = []
    for cat, c in CFG["categories"].items():
        base = dict(category=cat.replace("_", " "), shape=c["shape"], people_rule=PEOPLE_RULE[c["people"]])
        for facet in c["facets"]:
            prompt = PROMPT.substitute(base, topic=f"Topic: {facet}.", **n_args(CFG["per_facet"]))
            out.append((cat, facet, ["any"], prompt, f"{cat}/{facet}"))
        if c["regional"]:
            for group, regions in CFG["region_groups"].items():
                topic = (f"Topic: things especially common in photos taken by people living in {REGION_LABEL[group]} "
                         f"(everyday life, food, clothing, customs). Still no place or festival names.")
                prompt = PROMPT.substitute(base, topic=topic, **n_args(CFG["per_region"]))
                out.append((cat, f"regional:{group}", regions, prompt, f"{cat}/regional:{group}"))
    return out


def n_args(n):
    return {f"n_{k}": v for k, v in split_counts(n).items()}


def norm_key(text):
    """Key for exact dedup: lowercase, no articles/punctuation, crude plural strip."""
    words = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in {"a", "an", "the"}]
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words)


def build(dry_run=False):
    llm = LLM("generation")
    plan = calls()
    if dry_run:
        print(f"{len(plan)} calls; first prompt:\n")
        print(plan[0][3])
        return

    raw, served = [], Counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = pool.map(lambda c: llm.json(c[3], QueryItems, seed=c[4]), plan)
        for i, ((cat, facet, regions, _, _), res) in enumerate(zip(plan, results), 1):
            served[res.meta["served_model"]] += 1
            n = 0
            for asked in BUCKETS:                      # short first, so short items win dedup ties
                for text in res.output[asked]:
                    text = text.strip().rstrip(".").strip()
                    raw.append({"category": cat, "facet": facet, "regions": regions, "text": text,
                                "asked": asked, "length": length_bucket(text)})
                    n += 1
            print(f"[{i}/{len(plan)}] {cat} / {facet}: {n} items", flush=True)

    report = {"raw": len(raw),
              "length_mismatch": sum(e["asked"] != e["length"] for e in raw),
              "rejected_by_check": Counter(), "rejected": [],
              "exact_duplicates": 0, "near_duplicates": [], "review_band": []}

    # 1. purity
    pure = []
    for e in raw:
        v = check_query(e["text"])
        if v:
            report["rejected_by_check"].update({c for c, _ in v})
            report["rejected"].append({**e, "why": [m for _, m in v]})
        else:
            pure.append(e)

    # 2. exact duplicates (anywhere in the pool; first one wins)
    seen, unique = set(), []
    for e in pure:
        k = norm_key(e["text"])
        if k in seen:
            report["exact_duplicates"] += 1
            continue
        seen.add(k)
        unique.append(e)

    # 3. near duplicates within a category, greedy: generic facets come first, so they win
    emb = LLM("embedding")
    thr, band = CFG["dedup"]["threshold"], CFG["dedup"]["review_band"]
    kept = []
    by_cat = defaultdict(list)
    for e in unique:
        by_cat[e["category"]].append(e)
    for cat, items in by_cat.items():
        vecs = emb.embed([e["text"] for e in items])
        keep_idx = []
        for i in range(len(items)):
            sims = [(float(vecs[i] @ vecs[j]), j) for j in keep_idx]
            best, j = max(sims, default=(0.0, None))
            if best >= thr:
                report["near_duplicates"].append([items[i]["text"], items[j]["text"], round(best, 3)])
                continue
            if best >= band:
                report["review_band"].append([items[i]["text"], items[j]["text"], round(best, 3)])
            keep_idx.append(i)
        kept += [items[i] for i in keep_idx]

    # 4. write
    entries = []
    for n, e in enumerate(kept, 1):
        c = CFG["categories"][e["category"]]
        entries.append({"id": f"q{n:04d}", "category": e["category"], "facet": e["facet"],
                        "text": e["text"], "length": e["length"], "regions": e["regions"], "people": c["people"],
                        "avoid_personas": persona_name_hits(e["text"])})
    pool = {
        "version": VERSION,
        "generation": {"provider": llm.provider, "model": llm.model, "served_models": dict(served),
                       "embedding_model": emb.model, "dedup_threshold": thr},
        "category_weights": {k: CFG["weights"][c["weight"]] for k, c in CFG["categories"].items()},
        "entries": entries,
    }
    OUT.write_text(json.dumps(pool, indent=2, ensure_ascii=False))
    report["kept_by_category"] = Counter(e["category"] for e in entries)
    report["length_by_category"] = {cat: dict(Counter(e["length"] for e in entries if e["category"] == cat))
                                    for cat in CFG["categories"]}
    report["kept"] = len(entries)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nraw {len(raw)} -> rejected {len(report['rejected'])} -> exact dups {report['exact_duplicates']} "
          f"-> near dups {len(report['near_duplicates'])} -> kept {len(entries)}")
    print("kept by category:", dict(report["kept_by_category"]))
    print("rejections by check:", dict(report["rejected_by_check"]))
    print(f"length mismatches (asked vs actual bucket): {report['length_mismatch']}/{len(raw)}")
    total = Counter(e["length"] for e in entries)
    print("kept by length:", {k: f"{total[k]} ({total[k] / len(entries):.0%})" for k in BUCKETS})
    for cat, d in report["length_by_category"].items():
        print(f"  {cat:11s}", d)
    print("served models:", dict(served))


def show(k=12, seed=0):
    pool = json.loads(OUT.read_text())
    rng = random.Random(seed)
    by_cat = defaultdict(list)
    for e in pool["entries"]:
        by_cat[e["category"]].append(e)
    for cat, items in by_cat.items():
        regional = sum(e["regions"] != ["any"] for e in items)
        print(f"\n{cat} ({len(items)}, {regional} regional):")
        for e in rng.sample(items, min(k, len(items))):
            tag = "" if e["regions"] == ["any"] else f"  [{','.join(e['regions'])}]"
            print(f"  {e['text']}{tag}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    show() if args.show else build(args.dry_run)
