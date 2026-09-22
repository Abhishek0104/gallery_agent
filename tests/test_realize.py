import json
import random

from realize.system_prompt import GUIDANCE_START, system_prompt, teacher_system_prompt, tool_declarations
from realize.user_sim import check_message, number_forms, sample_surface, turn_intent
from sim.simulator import Simulator
from specs.spec_sampler import load_personas
from tests.test_specs import good_spec

SPECS = [json.loads(l) for l in open("data/specs/specs_v0.jsonl")]
PERSONAS = {p["persona_id"]: p for p in load_personas()}


def test_tool_declarations_fill_volatile_values():
    decls = {d["name"]: d for d in tool_declarations({"collage_max": 6, "effects": ["sepia", "cool"]})}
    assert set(decls) == {"search_images", "delete_images", "move_to_album", "make_collage", "apply_effect", "ask_gallery"}
    assert "2–6" in decls["make_collage"]["description"]
    assert decls["apply_effect"]["parameters"]["properties"]["effect"]["enum"] == ["sepia", "cool"]
    assert decls["move_to_album"]["parameters"]["required"] == ["images", "album"]


def test_guidance_only_for_teacher():
    assert GUIDANCE_START not in system_prompt() and GUIDANCE_START in teacher_system_prompt()


def test_simulator_replays_spec():
    spec = good_spec()
    sim = Simulator(spec)
    assert sim.call("search_images", {"query": "beach"}) == {"id": "r1", "count": 7}
    assert sim.call("apply_effect", {"images": "r1", "effect": "sepia"}) == {"status": "created", "images": "r2"}
    assert sim.call("make_collage", {"images": "r2"}) == {"status": "created", "collage": "r3"}
    assert sim.call("move_to_album", {"images": "r3", "album": "Goa 2024"})["created"] is False
    assert sim.done() and sim.flags == []


def test_simulator_flags_unplanned_and_bad_handles():
    sim = Simulator(good_spec())
    sim.call("delete_images", {"images": "r7"})
    assert any(f.startswith("unplanned_call") for f in sim.flags) and any(f.startswith("bad_handle") for f in sim.flags)


def test_relation_number_forms():
    singular, plural = number_forms("daughter")
    assert "daughter" in singular and "daughters" in plural
    singular, plural = number_forms("friend")
    assert "buddies" in plural and "buddy" in singular


def test_every_spec_turn_has_an_intent():
    rng = random.Random(0)
    for spec in SPECS:
        surface = sample_surface(spec, PERSONAS[spec["persona"]], "casual", rng)
        for turn in spec["turns"]:
            lines, required, _ = turn_intent(spec, turn, surface)
            assert lines
            assert not any("r1" in l or "search_images" in l for l in lines)


def test_check_message():
    assert check_message("show me photos from Goa", ["Goa", "<me>"]) == []
    assert check_message("show photos from goa", ["Goa", "<me>"]) != []
    assert check_message("apply sepia to r2", ["sepia"]) != []


def test_extra_filters_rejects_people_places_dates_not_in_spec():
    from realize.user_sim import check_message
    persona = PERSONAS["persona_01"]
    search = [{"query": "watching fireworks"}]
    assert check_message("find photos of us watching fireworks", [], search, persona)
    assert check_message("find fireworks photos from Montreal", [], search, persona)
    assert check_message("find fireworks photos from last year", [], search, persona)
    assert check_message(f"find photos of {persona['people'][0]['name']} at fireworks", [], search, persona)
    assert check_message("show me photos of fireworks", [], search, persona) == []
    # the spec's own values are fine
    assert check_message("fireworks with my husband", ["husband"], [{"query": "fireworks", "people": ["husband"]}], persona) == []


def test_delete_after_create_starts_turn():
    from specs.spec_sampler import sample_turns
    steps = [{"kind": "effect"}, {"kind": "collage"}, {"kind": "delete"}]
    assert sample_turns(steps, "one_message", random.Random(0)) == [[1, 2], [3]]


def test_album_with_year_is_not_a_date():
    from realize.user_sim import check_message
    persona = PERSONAS["persona_13"]
    msg = "find my mom in a gondola in Queenstown and move them to my Queenstown 2022 album"
    args = [{"query": "gondola", "people": ["mom"], "location": "Queenstown"}]
    assert check_message(msg, ["mom", "Queenstown", "Queenstown 2022"], args, persona) == []


def test_self_reference_rejected_when_search_has_no_me():
    from realize.user_sim import check_message
    persona = PERSONAS["persona_01"]
    q = [{"query": "floral dress"}]
    assert check_message("just the ones where I'm wearing a floral dress", [], q, persona)
    assert check_message("show me photos of a floral dress", [], q, persona) == []
    assert check_message("the ones where I'm wearing a floral dress", ["<me>"], [{"query": "floral dress", "people": ["me"]}], persona) == []


def test_named_persons_relation_word_allowed():
    from realize.user_sim import check_message
    persona = PERSONAS["persona_18"]
    name, rel = persona["people"][-1]["name"], persona["people"][-1]["relation"]
    assert check_message(f"photos of my {rel} {name}", [name], [{"people": [name]}], persona) == []


def test_prompts_do_not_depend_on_hash_seed():
    """Prompt text feeds the cache key, so it must not change with PYTHONHASHSEED (set iteration order)."""
    import os
    import subprocess
    import sys
    code = ("import json; from specs.scenarios import round2_skeleton; from realize.user_sim import turn_intent, "
            "sample_surface; from specs.spec_sampler import load_personas; import random; "
            "P={p['persona_id']: p for p in load_personas()}; out=[]\n"
            "for s in round2_skeleton(40, seed=3):\n"
            "    surf = sample_surface(s, P[s['persona']], 'casual', random.Random(1))\n"
            "    out += [turn_intent(s, t, surf) for t in s['turns']]\n"
            "print(json.dumps(out))")
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                           env={**os.environ, "PYTHONHASHSEED": str(h)}).stdout for h in (1, 2, 3)}
    assert len(runs) == 1
