import copy
import random

import pytest

from specs.intent_filler import build_prompt, merge
from specs.spec_sampler import FILL, load_catalog, need_bounds, sample_counts, sample_turns, skeleton
from specs.spec_validator import normalize_person, validate

PERSONA = {
    "persona_id": "persona_x", "region": "india", "household": "young_family",
    "owner": {"name": "Abhinav", "gender": "m", "age": 38, "home_city": "Bengaluru"},
    "people": [{"name": "Meera", "relation": "wife"}, {"name": "Riya", "relation": "daughter"},
               {"name": "Priya", "relation": "daughter"}],
    "pets": [{"name": "Bruno", "species": "dog"}],
    "places_visited": ["Goa", "Manali", "Dubai"], "albums": ["Goa 2024", "Receipts"],
    "interests": ["trekking", "cooking", "cricket"],
}


def good_spec():
    return {
        "spec_version": 1, "episode_id": "ep_t", "path": "P999", "persona": "persona_x",
        "config": {"collage_max": 9, "effects": ["sepia", "warm"]},
        "initial_selection": None, "motivation": "wants a vintage keepsake of the girls' Goa trip",
        "steps": [
            {"i": 1, "call": "search_images", "args": {"query": "on the beach", "people": ["daughter"], "location": "Goa"},
             "out": "r1", "outcome": {"count": 7}},
            {"i": 2, "call": "apply_effect", "args": {"images": "r1", "effect": "sepia"}, "out": "r2",
             "outcome": {"status": "created", "count": 7}},
            {"i": 3, "call": "make_collage", "args": {"images": "r2"}, "out": "r3",
             "outcome": {"status": "created", "count": 1}},
            {"i": 4, "call": "move_to_album", "args": {"images": "r3", "album": "Goa 2024"}, "out": None,
             "outcome": {"status": "moved", "count": 1, "album": "Goa 2024", "created": False}},
        ],
        "turns": [[1], [2, 3, 4]], "fill": [],
        "sampling": {"turn_mode": "mixed", "slot_patterns": ["people+location+query"]},
    }


def kinds(spec, persona=PERSONA):
    return {c for c, _ in validate(spec, persona)}


def test_good_spec_passes():
    assert validate(good_spec(), PERSONA) == []


@pytest.mark.parametrize("mutate,kind", [
    (lambda s: s["steps"][0]["args"].update(query="riya on the beach"), "purity"),
    (lambda s: s["steps"][0]["args"].update(people=["Karan"]), "people"),
    (lambda s: s["steps"][0]["args"].update(date="last year"), "slots"),
    (lambda s: s["steps"][1]["args"].update(effect="cool"), "effect"),
    (lambda s: s["steps"][2].update(args={"images": "r9"}), "schema"),
    (lambda s: s["steps"][1]["args"].update(strength=3), "schema"),
    (lambda s: s["steps"][3]["args"].update(album="Brand New"), "album"),
    (lambda s: s.update(turns=[[1, 2], [4]]), "turns"),
    (lambda s: s["steps"][0]["args"].update(query=FILL), "schema"),
])
def test_violations(mutate, kind):
    s = good_spec()
    mutate(s)
    assert kind in kinds(s)


def test_collage_count_bound():
    s = good_spec()
    s["steps"][0]["outcome"]["count"] = 12
    s["steps"][1]["outcome"]["count"] = 12
    assert "counts" in kinds(s)


def test_relation_alias_normalized():
    assert normalize_person("Mum") == "mom" and normalize_person("daughters") == "daughter"
    s = good_spec()
    s["steps"][0]["args"]["people"] = ["daughters"]
    assert validate(s, PERSONA) == []


def test_ask_needs_self_contained_question():
    s = good_spec()
    s["steps"] = [{"i": 1, "call": "ask_gallery", "args": {"question": "What is its number?"}, "out": "r1",
                   "outcome": {"answer": "K123", "count": 1}}]
    s["turns"], s["sampling"]["slot_patterns"] = [[1]], []
    assert "ask" in kinds(s)


def test_backward_bounds_through_selection_and_effect():
    cat = {c["id"]: c for c in load_catalog()}
    # search -> effect -> [select] -> collage: search needs 2+, selection needs 2..cmax
    p = next(c for c in cat.values() if c["path"] == "search→r1  ▸  effect(r1)→r2  ▸  [select](r2)→r3  ▸  collage(r3)→r4")
    need = need_bounds(p["steps"], 4)
    assert need["r3"] == (2, 4) and need["r2"][0] == 2 and need["r1"][0] == 2
    rng = random.Random(0)
    for _ in range(200):
        c = sample_counts(p["steps"], p["initial"], 4, rng)
        assert 2 <= c["r3"] <= min(4, c["r2"]) and c["r1"] == c["r2"]


def test_all_catalog_paths_sample():
    rng = random.Random(1)
    for c in load_catalog():
        for cmax in (4, 12):
            sample_counts(c["steps"], c["initial"], cmax, rng)


def test_turns_rules():
    steps = [{"kind": "search"}, {"kind": "search"}, {"kind": "select"}, {"kind": "collage"}]
    for mode in ("one_message", "one_per_turn", "mixed"):
        t = sample_turns(steps, mode, random.Random(0))
        assert t == [[1], [2], [3, 4]]


def test_skeletons_validate_after_fake_fill():
    """Every sampled skeleton, filled with harmless text, passes the validator (sampler/validator agree)."""
    from specs.spec_sampler import load_personas
    personas = {p["persona_id"]: p for p in load_personas()}
    for skel in skeleton(50, seed=3):
        out = {"motivation": "test", "question": "Do I have photos of a sunset at the beach?",
               "answer": "Yes, a few.",
               "album": next((personas[skel["persona"]]["albums"][0] if f["exists"] else "Zz Test Album"
                              for f in skel["fill"] if f["field"] == "album"), None),
               "queries": [{"step": f["step"], "query": "sunset"} for f in skel["fill"] if f["field"] == "query"]}
        spec = merge(skel, out)
        assert validate(spec, personas[spec["persona"]]) == [], spec["episode_id"]
        assert "<QUERY>" in build_prompt(skel, personas[spec["persona"]]) or not any(
            f["field"] == "query" for f in skel["fill"])


def test_refined_search_never_gets_documents():
    for seed in range(5):
        for skel in skeleton(50, seed=seed):
            by_i = {s["i"]: s for s in skel["steps"]}
            for f in skel["fill"]:
                if f["field"] == "query" and f["category"] == "documents":
                    step = by_i[f["step"]]
                    later = [s for s in skel["steps"] if s.get("refines") == step["i"]]
                    assert set(step["args"]) == {"query"} and all(set(s["args"]) == {"query"} for s in later)


@pytest.mark.parametrize("q,ok", [
    ("Do I have any pictures of Anika blowing out the candles on her birthday cake?", True),
    ("What is my passport number?", True),
    ("What is its number?", False),
    ("When did we go there?", False),
    ("Do I have photos of her at the beach?", False),
])
def test_self_contained(q, ok):
    from specs.spec_validator import self_contained
    assert self_contained(q) == ok


def test_none_fits_flips_to_new_album():
    from specs.spec_sampler import load_personas
    personas = {p["persona_id"]: p for p in load_personas()}
    skel = next(s for s in skeleton(50, seed=3) if any(f["field"] == "album" and f["exists"] for f in s["fill"]))
    out = {"motivation": "m", "question": "Do I have photos of a sunset at the beach?", "answer": "Yes.",
           "album": "Something Brand New", "album_is_new": True,
           "queries": [{"step": f["step"], "query": "sunset"} for f in skel["fill"] if f["field"] == "query"]}
    spec = merge(skel, out)
    move = next(s for s in spec["steps"] if s.get("call") == "move_to_album")
    assert move["outcome"]["created"] and spec["sampling"]["album_flipped"]
    assert validate(spec, personas[spec["persona"]]) == []
