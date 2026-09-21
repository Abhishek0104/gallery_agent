"""
Persona outline sampler (code only, no LLM).

An outline fixes everything the LLM must respect: region, household, owner age/gender,
the exact relation of every person, pet status, and one "tricky name" shape.
The LLM later fills in names, home city, places, albums and interests.

Quotas are exact counts (largest remainder) shuffled by seed, so 20 personas at
india=0.5 always gives 10 Indian personas, not "about 10".
"""
import random
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"

# role -> canonical relation, by gender of the person in that role
ROLE_RELATIONS = {
    "child":       {"m": "son",      "f": "daughter"},
    "parent":      {"m": "dad",      "f": "mom"},
    "sibling":     {"m": "brother",  "f": "sister"},
    "grandchild":  {"m": "grandson", "f": "granddaughter"},
    "grandparent": {"m": "grandpa",  "f": "grandma"},
    "friend":      {"m": "friend",   "f": "friend"},
    "colleague":   {"m": "colleague", "f": "colleague"},
    "roommate":    {"m": "roommate", "f": "roommate"},
    "cousin":      {"m": "cousin",   "f": "cousin"},
}


def load_config():
    return yaml.safe_load((CONFIG / "personas.yaml").read_text())


def quota_counts(weights, n):
    """Exact integer counts summing to n (largest-remainder method)."""
    total = sum(weights.values())
    raw = {k: n * w / total for k, w in weights.items()}
    counts = {k: int(v) for k, v in raw.items()}
    short = n - sum(counts.values())
    for k in sorted(raw, key=lambda k: (raw[k] - counts[k], k), reverse=True)[:short]:
        counts[k] += 1
    return counts


def quota_list(weights, n, rng):
    """Shuffled list with exactly quota_counts(weights, n) of each value."""
    items = [k for k, c in quota_counts(weights, n).items() for _ in range(c)]
    rng.shuffle(items)
    return items


def sample_relations(household, owner_gender, cfg, rng):
    """Pick role counts from the template, then map each person to a canonical relation."""
    tmpl = cfg["households"][household]
    counts = {role: rng.randint(lo, hi) for role, (lo, hi) in tmpl["roles"].items()}

    # respect people.min/max: trim optional roles first, then top up optional roles
    lo_n, hi_n = cfg["people"]["min"], cfg["people"]["max"]
    optional = [r for r, (lo, hi) in tmpl["roles"].items() if hi > lo]
    while sum(counts.values()) > hi_n:
        r = rng.choice([r for r in optional if counts[r] > tmpl["roles"][r][0]])
        counts[r] -= 1
    while sum(counts.values()) < lo_n:
        r = rng.choice([r for r in optional if counts[r] < tmpl["roles"][r][1]])
        counts[r] += 1

    relations = []
    for role, c in counts.items():
        if role == "spouse":
            relations += ["wife" if owner_gender == "m" else "husband"] * c
        elif role == tmpl["group"]:
            g = rng.choice("mf")                    # group shares one relation word
            relations += [ROLE_RELATIONS[role][g]] * c
        elif role == "parent" and c == 2:
            relations += ["mom", "dad"]
        elif role == "grandparent" and c == 2:
            relations += ["grandma", "grandpa"]
        else:
            relations += [ROLE_RELATIONS[role][rng.choice("mf")] for _ in range(c)]
    return relations


def sample_outlines(n=None, seed=None):
    cfg = load_config()
    n = n or cfg["n_personas"]
    seed = cfg["seed"] if seed is None else seed
    rng = random.Random(seed)
    q = cfg["quotas"]
    regions = quota_list(q["region"], n, rng)
    households = quota_list(q["household"], n, rng)
    pets = quota_list(q["pet"], n, rng)
    tricky = quota_list(q["tricky_name"], n, rng)
    species, sw = zip(*cfg["pet_species"].items())

    outlines = []
    for i in range(n):
        hh = households[i]
        gender = rng.choice("mf")
        lo, hi = cfg["households"][hh]["age"]
        outlines.append({
            "persona_id": f"persona_{i + 1:02d}",
            "region": regions[i],
            "household": hh,
            "owner": {"gender": gender, "age": rng.randint(lo, hi)},
            "relations": sample_relations(hh, gender, cfg, rng),
            "pet": {"status": pets[i],
                    "species": None if pets[i] == "none" else rng.choices(species, sw)[0]},
            "tricky_name": tricky[i],
        })
    return outlines


if __name__ == "__main__":
    for o in sample_outlines():
        print(o["persona_id"], o["region"], o["household"], o["owner"], o["pet"],
              o["tricky_name"], o["relations"])
