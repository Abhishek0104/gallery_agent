import pytest

from specs.query_purity import check_query, persona_name_hits

PERSONA = {"persona_id": "p", "owner": {"name": "Abhinav", "home_city": "Bengaluru"},
           "people": [{"name": "Riya", "relation": "daughter"}],
           "pets": [{"name": "Jalebi", "species": "cat"}], "places_visited": ["Goa"]}


def kinds(text, persona=None):
    return {c for c, _ in check_query(text, persona)}


@pytest.mark.parametrize("text", [
    "blowing out birthday candles", "dog sleeping on the sofa", "wearing a red saree",
    "sunset over the sea", "passport", "kids playing in the park", "woman in a hawaiian shirt",
])
def test_pure(text):
    assert check_query(text) == []


@pytest.mark.parametrize("text,kind", [
    ("beach in goa", "place"),
    ("Beach", "format"),
    ("photos of the beach", "format"),
    ("pictures of a sunset", "format"),
    ("screenshot of a chat", "format"),
    ("my passport", "pronoun"),
    ("selfie at the beach", "pronoun"),
    ("with my daughter", "relation"),
    ("hugging mom", "relation"),
    ("diwali lamps", "date"),
    ("christmas tree", "date"),
    ("beach last summer", "date"),
    ("snow in december", "date"),
    ("cake from 2023", "date"),
])
def test_impure(text, kind):
    assert kind in kinds(text)


def test_persona_names():
    assert "name" in kinds("riya dancing", PERSONA)
    assert check_query("plate of jalebi") == []              # fine globally...
    assert "name" in kinds("plate of jalebi", PERSONA)       # ...but not for the persona with a cat named Jalebi
    assert isinstance(persona_name_hits("plate of jalebi"), list)


@pytest.mark.parametrize("text,bad", [
    ("young woman in heavy embroidered lehenga", True), ("woman in wool sweater", True), ("kids playing", True),
    ("in a red saree", False), ("hugging a baby", False), ("with kids at the park", False),
    ("playing with two girls", False), ("blowing out candles", False),
])
def test_subject_rule_only_when_people_filled(text, bad):
    assert ("subject" in kinds_p(text)) == bad
    assert "subject" not in {c for c, _ in check_query(text)}


def kinds_p(text):
    return {c for c, _ in check_query(text, people=True)}


def test_personal_events_are_content():
    assert check_query("birthday party") == [] and check_query("anniversary dinner") == []
