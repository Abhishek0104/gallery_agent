import random

from specs.intent_filler import merge
from specs.scenarios import round2_skeleton
from specs.spec_sampler import load_personas
from specs.spec_validator import validate
from sim.simulator import Simulator
from realize.user_sim import check_message, sample_surface, turn_intent

PERSONAS = {p["persona_id"]: p for p in load_personas()}
SKELS = round2_skeleton(60, seed=5)


def fake_fill(s):
    return merge(s, {"motivation": "m", "question": "Do I have photos of a sunset at the beach?", "answer": "Yes.",
                     "album": next((PERSONAS[s["persona"]]["albums"][0] if f["exists"] else "Zz Test Album"
                                    for f in s["fill"] if f["field"] == "album"), None),
                     "queries": [{"step": f["step"], "query": "sunset"} for f in s["fill"] if f["field"] == "query"]})


def by(kind, variant=None):
    return next(fake_fill(s) for s in SKELS if s["scenario"]["type"] == kind and s["scenario"].get("variant") == variant)


def test_quotas_and_validation():
    kinds = [s["scenario"]["type"] for s in SKELS]
    assert kinds.count("missing_arg") == 18 and kinds.count("cancelled") == 12
    for s in SKELS:
        assert validate(fake_fill(s), PERSONAS[s["persona"]]) == [], s["episode_id"]


def test_expect_ask_ends_turn_and_next_turn_answers():
    for s in SKELS:
        by_i = {x["i"]: x for x in s["steps"]}
        for t in s["turns"]:
            for i in t:
                if by_i[i].get("expect") == "ask":
                    assert t[-1] == i


def test_simulator_round2_outcomes():
    spec = by("no_results", "truncate")
    sim = Simulator(spec)
    fail = next(x for x in spec["steps"] if x.get("outcome", {}).get("error") == "no_results")
    for x in spec["steps"]:
        if "call" in x and not x.get("skipped") and x["i"] < fail["i"]:
            sim.call(x["call"], x["args"])
    assert sim.call("search_images", fail["args"]) == {"error": "no_results"}
    spec = by("cancelled")
    sim = Simulator(spec)
    d = next(x for x in spec["steps"] if x.get("call") == "delete_images")
    sim.pos = sim.planned.index(d)
    assert sim.call("delete_images", {"images": "r1"}) == {"status": "cancelled", "count": 0}


def test_collage_over_limit_is_rejected_by_backend():
    spec = by("collage_over_limit")
    sim = Simulator(spec)
    over = spec["scenario"]["over"]
    sim.ledger["r1"] = {"count": over, "alive": True, "src": "search"}
    assert sim.call("make_collage", {"images": "r1"}) == {"error": "too_many_images", "max": spec["config"]["collage_max"]}


def test_missing_arg_first_request_forbids_the_value():
    spec = by("missing_arg", "album")
    surf = sample_surface(spec, PERSONAS[spec["persona"]], "casual", random.Random(0))
    move = next(x for x in spec["steps"] if x.get("call") == "move_to_album")
    first = next(t for k, t in enumerate(spec["turns"]) if move["i"] in spec["requests"][k] and move["i"] not in t)
    lines, req, forbidden = turn_intent(spec, first, surf)
    assert move["args"]["album"] in forbidden
    assert check_message(f"put these into {move['args']['album']}", [], [], None, forbidden)
