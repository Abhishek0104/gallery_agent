import copy

from specs.persona_outline import quota_counts, sample_outlines
from specs.persona_validator import validate

OUTLINE = {
    "persona_id": "persona_01", "region": "india", "household": "young_family",
    "owner": {"gender": "m", "age": 38},
    "relations": ["wife", "daughter", "daughter", "friend"],
    "pet": {"status": "named", "species": "dog"},
    "tricky_name": "hyphenated",
    "home_city": "Bengaluru", "required_destinations": ["Goa", "Manali"], "album_years": [2022, 2024],
}
GOOD = {
    "persona_id": "persona_01", "version": 1, "region": "india", "household": "young_family",
    "owner": {"name": "Abhinav", "gender": "m", "age": 38, "home_city": "Bengaluru"},
    "people": [{"name": "Meera", "relation": "wife"}, {"name": "Riya", "relation": "daughter"},
               {"name": "Anu-Priya", "relation": "daughter"}, {"name": "Karan", "relation": "friend"}],
    "pets": [{"name": "Bruno", "species": "dog"}],
    "places_visited": ["Goa", "Manali", "Dubai"],
    "albums": ["Goa 2024", "Riya's birthday", "Receipts"],
    "interests": ["trekking", "home baking", "cricket"],
}


def checks(p, outline=OUTLINE, used=()):
    return {c for c, _ in validate(p, outline, used)}


def test_quotas_are_exact():
    assert quota_counts({"a": 0.5, "b": 0.2, "c": 0.1, "d": 0.2}, 20) == {"a": 10, "b": 4, "c": 2, "d": 4}
    assert sum(quota_counts({"a": 1, "b": 1, "c": 1}, 20).values()) == 20
    regions = [o["region"] for o in sample_outlines(20, seed=1)]
    assert regions.count("india") == 10 and regions.count("us") == 4 and regions.count("uk") == 2


def test_outlines_deterministic_and_have_group():
    assert sample_outlines(20, seed=3) == sample_outlines(20, seed=3)
    for o in sample_outlines(20, seed=3):
        assert 3 <= len(o["relations"]) <= 7
        assert any(o["relations"].count(r) >= 2 for r in o["relations"])


def test_good_persona_passes():
    assert validate(GOOD, OUTLINE) == []


def test_relation_mismatch():
    p = copy.deepcopy(GOOD)
    p["people"][1]["relation"] = "son"
    assert "relations" in checks(p)


def test_name_is_place_or_relation():
    p = copy.deepcopy(GOOD)
    p["people"][3]["name"] = "Georgia"          # a gazetteer destination
    assert "names" in checks(p)
    p["people"][3]["name"] = "Mummy"
    assert "names" in checks(p)


def test_pet_name_duplicates_person():
    p = copy.deepcopy(GOOD)
    p["pets"][0]["name"] = "Riya"
    assert "names" in checks(p)


def test_unnamed_pet_must_have_no_name():
    o = copy.deepcopy(OUTLINE)
    o["pet"]["status"] = "unnamed"
    assert "pets" in checks(GOOD, o)


def test_cross_persona_names():
    assert "cross_persona" in checks(GOOD, used={"riya"})


def test_tricky_name_required():
    p = copy.deepcopy(GOOD)
    p["people"][2]["name"] = "Anu"
    assert "tricky_name" in checks(p)


def test_home_city_and_interests():
    p = copy.deepcopy(GOOD)
    p["owner"]["home_city"] = "Seattle"         # not the outline's city
    p["interests"][0] = "Goa beaches"
    assert {"places", "interests"} <= checks(p)


def test_list_sizes():
    p = copy.deepcopy(GOOD)
    p["albums"] = ["Goa 2024"]
    assert "albums" in checks(p)


def test_unicode_and_apostrophe_names():
    p = copy.deepcopy(GOOD)
    p["people"][3]["name"] = "René"
    p["people"][2]["name"] = "D'Andre-Lee"
    assert validate(p, OUTLINE) == []


def test_child_groups_split_evenly():
    groups = []
    for o in sample_outlines(20, seed=5):
        rel = o["relations"]
        groups += [r for r in ("son", "daughter") if rel.count(r) >= 2]
    assert abs(groups.count("son") - groups.count("daughter")) <= 1


def test_required_destinations_and_album_years():
    p = copy.deepcopy(GOOD)
    p["places_visited"] = ["Goa", "Dubai", "Ooty"]          # Manali missing
    p["albums"][0] = "Goa 2023"                               # year not in outline
    assert {"places", "albums"} <= checks(p)


def test_pet_names_count_as_used():
    assert "cross_persona" in checks(GOOD, used={"bruno"})


def test_outline_spreads_home_cities():
    outs = [o for o in sample_outlines(20, seed=7) if o["region"] == "india"]
    assert len({o["home_city"] for o in outs}) == len(outs)
    assert all(o["home_city"] not in o["required_destinations"] for o in outs)
    genders = [o["owner"]["gender"] for o in sample_outlines(20, seed=7)]
    assert genders.count("m") == genders.count("f") == 10
