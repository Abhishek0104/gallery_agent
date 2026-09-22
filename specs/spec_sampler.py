"""
Spec sampler (code only, v0 happy path): catalog path + persona -> spec skeleton.

The skeleton fixes everything except free text: tool args (people / location / date / effect /
existing album), outcomes (counts, statuses, created), turn grouping, and hints for the intent filler.
The filler later writes only: motivation, `query` text, the ask_gallery question + answer, and new
album names (marked "<fill>" in args, described in `fill`).

Counts come from a backward pass over the path: each consumer constrains the count of the handle it
reads (collage needs 2..collage_max, a selection needs a set of 2+, effect passes its bound through),
then counts are sampled forward inside those bounds.
"""
import json
import random
from pathlib import Path

import yaml

from registry import collage_bounds, effect_values, tools
from specs.persona_outline import quota_list
from specs.query_purity import describes_subject

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "specs.yaml").read_text())
REGIONS = yaml.safe_load((ROOT / "config" / "regions.yaml").read_text())
QP_CFG = yaml.safe_load((ROOT / "config" / "query_pool.yaml").read_text())
SPEC_VERSION = 1
FILL = "<fill>"

TOOL = {"search": "search_images", "ask": "ask_gallery", "effect": "apply_effect",
        "collage": "make_collage", "move": "move_to_album", "delete": "delete_images"}
SLOTS = ["people", "location", "date", "query"]
INF = 10 ** 6


# ---------------------------------------------------------------- inputs
def load_catalog():
    """Catalog paths with status keep; steps as {kind, in, out} (kind = the tool's short name, or "select")."""
    short = {t.name: t.catalog.short for t in tools().values()} | {"select": "select"}
    out = []
    for c in yaml.safe_load((ROOT / "catalog" / "path_catalog.yaml").read_text()):
        if c["status"] != "keep":
            continue
        steps = [{"kind": short[s["tool"]], "in": s["in"], "out": s["out"]} for s in c["steps"]]
        out.append({**c, "steps": steps})
    return out


def load_personas():
    return [json.loads(f.read_text()) for f in sorted((ROOT / "data" / "personas").glob("persona_*.json"))]


def load_pool():
    return json.loads((ROOT / "data" / "query_pool.json").read_text())


# ---------------------------------------------------------------- counts
def need_bounds(steps, cmax):
    """Backward pass: handle -> (lo, hi) its count must satisfy for every downstream consumer."""
    need = {}

    def narrow(h, lo, hi):
        a, b = need.get(h, (1, INF))
        need[h] = (max(a, lo), min(b, hi))

    for s in reversed(steps):
        k = s["kind"]
        if k == "collage":
            narrow(s["in"], 2, cmax)
        elif k == "effect":
            narrow(s["in"], *need.get(s["out"], (1, INF)))
        elif k == "select":
            narrow(s["in"], max(2, need.get(s["out"], (1, INF))[0]), INF)
        elif k in ("move", "delete"):
            narrow(s["in"], 1, INF)
    return need


def sample_counts(steps, initial, cmax, rng):
    """Forward pass inside the backward bounds. Returns handle -> count."""
    need = need_bounds(steps, cmax)
    counts = {}

    def pick(h, lo, hi):
        a, b = need.get(h, (1, INF))
        lo, hi = max(lo, a), min(hi, b)
        if lo > hi:
            raise ValueError(f"no valid count for {h}: [{lo}, {hi}]")
        counts[h] = rng.randint(lo, hi)
        return counts[h]

    if initial == "selection":
        pick("r0", *CFG["counts"]["initial_selection"])
    for i, s in enumerate(steps):
        k = s["kind"]
        if k == "search":
            if s["out"] in counts:                  # already set as the first of a refinement pair
                continue
            nxt = steps[i + 1] if i + 1 < len(steps) else None
            if nxt and nxt["kind"] == "search":     # refinement narrows: sample the second first
                c2 = pick(nxt["out"], *CFG["counts"]["search"])
                pick(s["out"], c2 + 1, max(CFG["counts"]["search"][1], c2 + 5))
            else:
                pick(s["out"], *CFG["counts"]["search"])
        elif k == "ask":
            pick(s["out"], *CFG["counts"]["ask"])
        elif k == "select":
            src = counts[s["in"]]
            lo, hi = need.get(s["out"], (1, INF))
            hi = min(hi, src - 1 if src - 1 >= lo else src)   # a real subset when possible
            pick(s["out"], lo, hi)
        elif k == "effect":
            counts[s["out"]] = counts[s["in"]]
        elif k == "collage":
            counts[s["out"]] = 1
    return counts


# ---------------------------------------------------------------- search args
def sample_people(persona, rng, exclude=()):
    named_pets = [p["name"] for p in persona["pets"] if p["name"]]
    kinds = {k: w for k, w in CFG["people_kind"].items() if k != "pet" or named_pets}
    n = int(rng.choices(list(CFG["people_elements"]), weights=CFG["people_elements"].values())[0])
    out = []
    for _ in range(100):
        if len(out) == n:
            break
        kind = rng.choices(list(kinds), weights=kinds.values())[0]
        val = {"me": "me",
               "name": rng.choice([p["name"] for p in persona["people"]]),
               "relation": rng.choice(sorted({p["relation"] for p in persona["people"]})),
               "pet": rng.choice(named_pets) if named_pets else None}[kind]
        # no repeats, and never a name together with its own relation ("Riya" AND "daughter")
        rel_of = {p["name"]: p["relation"] for p in persona["people"]}
        clash = any(rel_of.get(v) == val or rel_of.get(val) == v for v in out)
        if val and val not in out and val not in exclude and not clash:
            out.append(val)
    return out


def sample_location(persona, rng):
    src = rng.choices(list(CFG["location_source"]), weights=CFG["location_source"].values())[0]
    return persona["owner"]["home_city"] if src == "home_city" else rng.choice(persona["places_visited"])


def sample_date(persona, rng):
    src = rng.choices(list(CFG["date_source"]), weights=CFG["date_source"].values())[0]
    if src == "generic":
        return rng.choice(CFG["generic_dates"])
    reg = REGIONS[persona["region"]]
    return rng.choice(reg["date_phrases"] + [f"during {f}" for f in reg["festivals"]])


def allowed_categories(slots):
    """Categories whose people rule fits the slot set (relational needs people; documents are query-only)."""
    out = {}
    for cat, c in QP_CFG["categories"].items():
        if c["people"] == "required" and "people" not in slots:
            continue
        if c["people"] == "never" and set(slots) != {"query"}:
            continue
        out[cat] = QP_CFG["weights"][c["weight"]]
    return out


def query_hint(slots, persona, pool, rng, also=None, category=None):
    """Category + length + pool examples. `also`: a second slot set the category must fit too
    (a search that will be refined keeps its query, so the category must suit both).
    `category`: quota-assigned category, used when it fits."""
    cats = allowed_categories(slots)
    if also is not None:
        cats = {k: w for k, w in cats.items() if k in allowed_categories(also)}
    cat = category if category in cats else rng.choices(list(cats), weights=cats.values())[0]
    length = rng.choices(list(CFG["query_length"]), weights=CFG["query_length"].values())[0]
    cands = [e for e in pool["entries"] if e["category"] == cat and e["length"] == length
             and (e["regions"] == ["any"] or persona["region"] in e["regions"])
             and persona["persona_id"] not in e["avoid_personas"]
             # with people filled, don't show examples that describe the person ("woman in a red saree")
             and not ("people" in slots and describes_subject(e["text"].split()))]
    ex = rng.sample(cands, min(CFG["query_examples"], len(cands)))
    return {"category": cat, "length": length, "examples": [e["text"] for e in ex],
            "example_ids": [e["id"] for e in ex]}


def search_args(slots, persona, rng):
    args = {}
    if "people" in slots:
        args["people"] = sample_people(persona, rng)
    if "location" in slots:
        args["location"] = sample_location(persona, rng)
    if "date" in slots:
        args["date"] = sample_date(persona, rng)
    if "query" in slots:
        args["query"] = FILL
    return {k: args[k] for k in ("query", "people", "location", "date") if k in args}


def assign_categories(assign, refines, rng, multi=frozenset()):
    """Exact category quota over first searches that carry a query (spec index -> category).
    Documents are query-only, so a documents search gets the "query" pattern (swapping patterns with
    another spec to keep the slot-pattern quota) and is never refined. Relational needs people."""
    weights = {c: QP_CFG["weights"][v["weight"]] for c, v in QP_CFG["categories"].items()}
    q_idx = [i for i in sorted(assign) if "query" in assign[i].split("+")]
    cats = quota_list(weights, len(q_idx), rng)
    out = {}

    n_docs = cats.count("documents")
    cand = sorted((i for i in q_idx if not refines[i] and i not in multi), key=lambda i: assign[i] != "query")[:n_docs]
    for i in cand:
        if assign[i] != "query":
            j = next((j for j in sorted(assign) if assign[j] == "query" and j not in cand
                      and (not refines[j] or len(assign[i].split("+")) < 4)
                      and (j not in multi or len(assign[i].split("+")) >= 2)), None)
            if j is not None:
                assign[j] = assign[i]
            assign[i] = "query"
        out[i] = "documents"

    rest = [c for c in cats if c != "documents"] + ["documents"] * (n_docs - len(cand))
    remaining = [i for i in q_idx if i not in out]
    with_people = [i for i in remaining if "people" in assign[i]]
    n_rel = rest.count("relational")
    for i in with_people[:n_rel]:
        out[i] = "relational"
    others = [c for c in rest if c not in ("relational", "documents")]
    rng.shuffle(others)
    for i in remaining:
        if i not in out:
            out[i] = others.pop() if others else None      # None -> weighted pick among allowed
    return out


# ---------------------------------------------------------------- turns
def sample_turns(steps, mode, rng):
    """Group step indices (1-based) into user turns.
    Forced: a selection starts a new turn and stays with the action after it; a turn ends after
    ask_gallery; refinement searches are in separate turns; deleting something just created (a collage
    or effect copies) starts a new turn, since the user looks at it first."""
    n = len(steps)
    turns, cur = [], [1]
    for j in range(2, n + 1):
        prev, s = steps[j - 2], steps[j - 1]
        if prev["kind"] == "select":
            forced = False                       # the selection's own action
            split = False
        elif s["kind"] == "select" or prev["kind"] == "ask" or (prev["kind"] == s["kind"] == "search"):
            forced, split = True, True
        else:
            forced = False
            split = {"one_message": False, "one_per_turn": True, "mixed": rng.random() < 0.5}[mode]
            if s["kind"] == "delete" and prev["kind"] in ("collage", "effect"):
                split = True                     # decided after the draw, so later specs don't shift
        if split:
            turns.append(cur)
            cur = [j]
        else:
            cur.append(j)
    turns.append(cur)
    return turns


# ---------------------------------------------------------------- skeleton
def skeleton(n=None, seed=None, paths=None, multi=(), id_prefix="ep"):
    """Happy-path skeletons. `paths`: a fixed path per spec (round 2 draws eligible paths itself);
    `multi`: spec indices whose first search needs 2+ slots (so one can be dropped later)."""
    seed = CFG["seed"] if seed is None else seed
    rng = random.Random(seed)
    catalog, personas, pool = load_catalog(), load_personas(), load_pool()
    cmin, cmax_options = collage_bounds()
    all_effects = effect_values()

    if paths is None:
        n = n or CFG["n_specs"]
        paths = rng.sample(catalog, min(n, len(catalog)))        # without replacement: distinct paths
        while len(paths) < n:                                    # every path once more before any repeats again
            paths += rng.sample(catalog, min(n - len(paths), len(catalog)))
    n = len(paths)
    persona_order = quota_list({p["persona_id"]: 1 for p in personas}, n, rng)
    by_id = {p["persona_id"]: p for p in personas}
    modes = quota_list(CFG["turn_mode"], n, rng)

    # slot patterns: exact quota over first searches; a refinement needs a free slot to add
    has_search = [any(s["kind"] == "search" for s in p["steps"]) for p in paths]
    refines = [any(a["kind"] == b["kind"] == "search" for a, b in zip(p["steps"], p["steps"][1:])) for p in paths]
    patterns = quota_list(CFG["slot_patterns"], sum(has_search), rng)
    free = [x for x in patterns if len(x.split("+")) < 4]
    assign = {}
    multi = set(multi)
    order = ([i for i in range(n) if refines[i]] + [i for i in range(n) if has_search[i] and i in multi and not refines[i]]
             + [i for i in range(n) if has_search[i] and i not in multi and not refines[i]])
    for i in order:
        options = free if refines[i] else [x for x in patterns if len(x.split("+")) >= 2] if i in multi else patterns
        pick = next(x for x in options if x in patterns)
        patterns.remove(pick)
        if pick in free:
            free.remove(pick)
        assign[i] = pick

    category_of = assign_categories(assign, refines, rng, multi)

    specs = []
    for i, path in enumerate(paths):
        persona = by_id[persona_order[i]]
        cmax = rng.choice(cmax_options)
        effects = rng.sample(all_effects, rng.randint(*CFG["effects_shown"]))
        counts = sample_counts(path["steps"], path["initial"], cmax, rng)
        steps, fill, sampling = [], [], {"turn_mode": modes[i], "slot_patterns": []}
        prev_search = None
        refine_add = {}                                 # step index of a refinement -> slot it adds
        for j, s in enumerate(path["steps"], 1):
            k = s["kind"]
            if k == "select":
                steps.append({"i": j, "event": "select", "from": s["in"], "out": s["out"],
                              "count": counts[s["out"]]})
                continue
            step = {"i": j, "call": TOOL[k], "args": {}, "out": s["out"], "outcome": {}}
            if k == "search":
                if prev_search is None:
                    slots = assign[i].split("+")
                    step["args"] = search_args(slots, persona, rng)
                    nxt = path["steps"][j] if j < len(path["steps"]) else None
                    if nxt and nxt["kind"] == "search":     # decide the refinement's new slot now
                        refine_add[j + 1] = rng.choice([x for x in SLOTS if x not in slots])
                else:                                   # refinement: previous filters + one new slot
                    slots = list(prev_search["args"])
                    add = refine_add[j]
                    step["args"] = dict(prev_search["args"])
                    step["args"].update(search_args([add], persona, rng))
                    step["refines"] = prev_search["i"]
                    slots = slots + [add]
                sampling["slot_patterns"].append("+".join(x for x in SLOTS if x in step["args"]))
                inherited = "refines" in step and "query" in prev_search["args"]   # refinements copy the query
                later = list(step["args"]) + [refine_add[j + 1]] if j + 1 in refine_add else None
                if step["args"].get("query") == FILL and not inherited:
                    cat = category_of.get(i) if prev_search is None else None
                    fill.append({"field": "query", "step": j,
                                 **query_hint(list(step["args"]), persona, pool, rng, later, cat),
                                 "with": {x: v for x, v in step["args"].items() if x != "query"}})
                step["outcome"] = {"count": counts[s["out"]]}
                prev_search = step
            elif k == "ask":
                before_action = j < len(path["steps"])
                qtype = rng.choice(CFG["ask_types"]["before_action" if before_action else "standalone"])
                step["args"] = {"question": FILL}
                step["outcome"] = {"answer": FILL, "count": counts[s["out"]]}
                fill.append({"field": "question", "step": j, "type": qtype, "count": counts[s["out"]],
                             "before_action": before_action})
            elif k == "effect":
                step["args"] = {"images": s["in"], "effect": rng.choice(effects)}
                step["outcome"] = {"status": "created", "count": counts[s["out"]]}
            elif k == "collage":
                step["args"] = {"images": s["in"]}
                step["outcome"] = {"status": "created", "count": 1}
            elif k == "move":
                exists = rng.random() < CFG["album_exists"]   # the filler picks which album fits the story
                step["args"] = {"images": s["in"], "album": FILL}
                step["outcome"] = {"status": "moved", "count": counts[s["in"]], "album": FILL,
                                   "created": not exists}
                fill.append({"field": "album", "step": j, "exists": exists})
            elif k == "delete":
                step["args"] = {"images": s["in"]}
                step["outcome"] = {"status": "deleted", "count": counts[s["in"]]}
                step["out"] = None
            steps.append(step)

        specs.append({
            "spec_version": SPEC_VERSION,
            "episode_id": f"{id_prefix}_{i + 1:04d}",
            "path": path["id"],
            "path_str": path["path"],
            "persona": persona["persona_id"],
            "config": {"collage_max": cmax, "effects": effects},
            "initial_selection": {"handle": "r0", "count": counts["r0"]} if path["initial"] == "selection" else None,
            "motivation": FILL,
            "steps": steps,
            "turns": sample_turns(path["steps"], modes[i], rng),
            "fill": fill,
            "sampling": sampling,
        })
    return specs


if __name__ == "__main__":
    for sp in skeleton():
        print(sp["episode_id"], sp["path"], sp["persona"], sp["config"], sp["turns"])
        for s in sp["steps"]:
            print("   ", s)
