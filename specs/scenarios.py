"""
Round 2 scenario overlays (docs/round2_design.md): a happy skeleton + one scenario -> a round-2 skeleton.

    A no_results  (truncate | recover)   the last search finds nothing
    B cancelled                          the delete is cancelled in the app's dialog
    C collage_over_limit                 the collage input is over collage_max: ask, reselect, then collage
    D missing_arg (album | effect)       the request omits the album / effect: ask, answer, then call

New step kinds: {"expect": "ask" | "report", "about": ...} (assistant turn with no call) and "skipped": true
(asked for, but not executed). `requests` lists, per turn, the steps the user asks for in that turn.
"""
import copy
import random

import yaml

from specs.persona_outline import Dealer, quota_list
from specs.spec_sampler import CFG, ROOT, load_catalog, skeleton

R2 = yaml.safe_load((ROOT / "config" / "round2.yaml").read_text())
SLOTS = ["people", "location", "date", "query"]


# ---------------------------------------------------------------- eligibility (on catalog paths)
def producer(steps, handle):
    return next((s for s in steps if s["out"] == handle), None)


def collage_lineage_ok(steps):
    """The collage input traces back (through effects only) to a search or r0: no selection, no ask."""
    c = next((s for s in steps if s["kind"] == "collage"), None)
    if c is None:
        return False
    h = c["in"]
    while True:
        if h == "r0":
            return True
        p = producer(steps, h)
        if p is None or p["kind"] in ("select", "ask"):
            return False
        if p["kind"] == "search":
            return True
        h = p["in"]                                  # effect: keep walking


ELIGIBLE = {
    ("no_results", "truncate"): lambda st: any(s["kind"] == "search" for s in st),
    ("no_results", "recover"): lambda st: any(s["kind"] == "search" for s in st),
    ("cancelled", None): lambda st: any(s["kind"] == "delete" for s in st),
    ("collage_over_limit", None): collage_lineage_ok,
    ("missing_arg", "album"): lambda st: any(s["kind"] == "move" for s in st),
    ("missing_arg", "effect"): lambda st: any(s["kind"] == "effect" for s in st),
}


# ---------------------------------------------------------------- helpers on skeleton specs
def turn_index(spec, i):
    return next(k for k, t in enumerate(spec["turns"]) if i in t)


def next_id(spec):
    return max(s["i"] for s in spec["steps"]) + 1


def next_handle(spec):
    nums = [int(s["out"][1:]) for s in spec["steps"] if s.get("out")]
    return f"r{max(nums + [0]) + 1}"


def insert_after(spec, after_i, step):
    k = next(n for n, s in enumerate(spec["steps"]) if s["i"] == after_i)
    spec["steps"].insert(k + 1, step)


def renumber(spec):
    """Sequential step ids in list order; remap turns, requests, fills, refines/loosens/at."""
    m = {s["i"]: n for n, s in enumerate(spec["steps"], 1)}
    for s in spec["steps"]:
        s["i"] = m[s["i"]]
        for key in ("refines", "loosens"):
            if key in s:
                s[key] = m[s[key]]
    spec["turns"] = [[m[i] for i in t] for t in spec["turns"]]
    spec["requests"] = [[m[i] for i in t] for t in spec["requests"]]
    spec["fill"] = [{**f, "step": m[f["step"]]} for f in spec["fill"] if f["step"] in m]
    if "at" in spec["scenario"]:
        spec["scenario"]["at"] = m[spec["scenario"]["at"]]
    order = [s["i"] for s in spec["steps"]]
    spec["turns"] = [sorted(t, key=order.index) for t in spec["turns"]]
    spec["sampling"]["slot_patterns"] = ["+".join(x for x in SLOTS if x in s["args"])
                                         for s in spec["steps"] if s.get("call") == "search_images"]
    return spec


# ---------------------------------------------------------------- overlays
def no_results(spec, variant, rng):
    searches = [s for s in spec["steps"] if s.get("call") == "search_images"]
    s = searches[-1]                                    # the search whose results are acted on
    happy_count, out = s["outcome"]["count"], s["out"]
    s["outcome"] = {"count": 0, "error": "no_results"}
    s["out"] = None
    k = turn_index(spec, s["i"])
    t = spec["turns"][k]
    after = t[t.index(s["i"]) + 1:]                     # asked in the same message, now not done
    report = {"i": next_id(spec), "expect": "report", "about": "no_results"}
    spec["scenario"] = {"type": "no_results", "variant": variant, "at": s["i"]}
    by_i = {x["i"]: x for x in spec["steps"]}
    if variant == "truncate":
        for i in after:
            by_i[i]["skipped"] = True
        last = after[-1] if after else s["i"]
        insert_after(spec, last, report)
        spec["turns"][k] = t + [report["i"]]
        later = {i for tt in spec["turns"][k + 1:] for i in tt}
        spec["steps"] = [x for x in spec["steps"] if x["i"] not in later]
        spec["turns"], spec["requests"] = spec["turns"][:k + 1], spec["requests"][:k + 1]
        return spec
    # recover: report, then a new user turn drops one filter; the rest of that turn follows the new search
    dropped = rng.choice(list(s["args"]))
    loose = {"i": next_id(spec) + 1, "call": "search_images",
             "args": {a: v for a, v in s["args"].items() if a != dropped}, "out": out,
             "outcome": {"count": happy_count}, "loosens": s["i"]}
    spec["scenario"]["dropped"] = dropped
    insert_after(spec, s["i"], report)
    insert_after(spec, report["i"], loose)
    spec["turns"][k] = t[:t.index(s["i"]) + 1] + [report["i"]]
    spec["turns"].insert(k + 1, [loose["i"]] + after)
    # the user asked for all of it in turn k, and restates the rest when retrying; merge() copies the query
    # of the failed search into the loosened one (via `loosens`) unless the query is the dropped filter
    spec["requests"].insert(k + 1, [loose["i"]] + after)
    return spec


def cancelled(spec, rng):
    d = next(s for s in spec["steps"] if s.get("call") == "delete_images")
    d["outcome"] = {"status": "cancelled", "count": 0}
    spec["scenario"] = {"type": "cancelled", "at": d["i"]}
    return spec


def collage_over_limit(spec, rng):
    cmax = spec["config"]["collage_max"]
    by_out = {s["out"]: s for s in spec["steps"] if s.get("out")}
    c = next(s for s in spec["steps"] if s.get("call") == "make_collage")
    over = rng.randint(cmax + 1, CFG["counts"]["search"][1])
    # raise the count along the lineage (effects copy the count; a refined search must stay narrower)
    h = c["args"]["images"]
    while True:
        if h == "r0":
            spec["initial_selection"]["count"] = over
            break
        p = by_out[h]
        p["outcome"]["count"] = over
        if p["call"] == "search_images":
            if "refines" in p:
                parent = next(x for x in spec["steps"] if x["i"] == p["refines"])
                parent["outcome"]["count"] = max(parent["outcome"]["count"], over + rng.randint(1, 10))
            break
        h = p["args"]["images"]
    ask = {"i": next_id(spec), "expect": "ask", "about": "select", "max": cmax}
    sel = {"i": next_id(spec) + 1, "event": "select", "from": c["args"]["images"], "out": next_handle(spec),
           "count": rng.randint(2, cmax)}
    spec["scenario"] = {"type": "collage_over_limit", "at": c["i"], "over": over}
    k = turn_index(spec, c["i"])
    t = spec["turns"][k]
    moved = t[t.index(c["i"]):]                      # the collage and anything asked after it in that message
    c["args"]["images"] = sel["out"]
    k_prev = t.index(c["i"])
    spec["turns"][k] = t[:k_prev] + [ask["i"]]
    spec["turns"].insert(k + 1, [sel["i"]] + moved)
    spec["requests"].insert(k + 1, [])               # asked in the first message; the user just reselects
    idx = next(n for n, s in enumerate(spec["steps"]) if s["i"] == c["i"])
    spec["steps"][idx:idx] = [ask, sel]
    return spec


def missing_arg(spec, variant, rng):
    call = {"album": "move_to_album", "effect": "apply_effect"}[variant]
    x = next(s for s in spec["steps"] if s.get("call") == call)
    ask = {"i": next_id(spec), "expect": "ask", "about": variant}
    spec["scenario"] = {"type": "missing_arg", "variant": variant, "at": x["i"]}
    k = turn_index(spec, x["i"])
    t = spec["turns"][k]
    moved = t[t.index(x["i"]):]
    spec["turns"][k] = t[:t.index(x["i"])] + [ask["i"]]
    spec["turns"].insert(k + 1, moved)
    spec["requests"].insert(k + 1, [])               # asked in the first message (without the value); now answered
    idx = next(n for n, s in enumerate(spec["steps"]) if s["i"] == x["i"])
    spec["steps"].insert(idx, ask)
    return spec


def apply(spec, stype, variant, rng):
    spec = copy.deepcopy(spec)
    spec["requests"] = [[i for i in t] for t in spec["turns"]]
    fn = {"no_results": lambda: no_results(spec, variant, rng), "cancelled": lambda: cancelled(spec, rng),
          "collage_over_limit": lambda: collage_over_limit(spec, rng),
          "missing_arg": lambda: missing_arg(spec, variant, rng)}[stype]
    spec = fn()
    # requests never list assistant-only steps
    kinds = {s["i"]: s for s in spec["steps"]}
    spec["requests"] = [[i for i in t if i in kinds and "expect" not in kinds[i]] for t in spec["requests"]]
    return renumber(spec)


# ---------------------------------------------------------------- batch
def round2_skeleton(n=None, seed=None):
    n = n or R2["n_specs"]
    seed = R2["seed"] if seed is None else seed
    rng = random.Random(seed)
    catalog = load_catalog()
    types = quota_list(R2["scenarios"], n, rng)
    variants = {}
    for t, split in R2["variants"].items():
        idx = [i for i, x in enumerate(types) if x == t]
        variants.update(zip(idx, quota_list(split, len(idx), rng)))
    dealers = {key: Dealer([c for c in catalog if f(c["steps"])], rng) for key, f in ELIGIBLE.items()}
    paths, multi = [], set()
    for i, t in enumerate(types):
        key = (t, variants.get(i))
        p = dealers[key].take()[0]
        paths.append(p)
        refined_last = any(a["kind"] == b["kind"] == "search" for a, b in zip(p["steps"], p["steps"][1:]))
        if key == ("no_results", "recover") and not refined_last:
            multi.add(i)                               # its only search needs a filter to drop
    # a different seed for the base skeleton: with the same one its first quota shuffle (personas) repeats the
    # scenario shuffle above, which tied every persona to a single scenario type
    base = skeleton(seed=seed + 1_000_003, paths=paths, multi=multi, id_prefix="r2")
    return [apply(s, types[i], variants.get(i), rng) for i, s in enumerate(base)]


if __name__ == "__main__":
    for s in round2_skeleton(12):
        print(s["episode_id"], s["path"], s["scenario"], "turns", s["turns"], "requests", s["requests"])
        for st in s["steps"]:
            print("   ", {k: v for k, v in st.items() if k in ("i", "call", "event", "expect", "about", "args", "out",
                                                          "outcome", "skipped", "loosens", "refines", "count", "from")})
