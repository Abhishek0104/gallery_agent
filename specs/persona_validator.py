"""
Persona validator (code only).

validate(persona, outline, used_names) -> list of violations; empty list = accepted.
Each violation is (check, message) so rejection reasons can be counted per check.

Main job: protect downstream checks. The intent validator's query-purity check compares
queries against persona names, relation aliases and a gazetteer, so a person named after a
place, a pet sharing a person's name, or a name that is also a relation word would silently
break it later.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"

LIMITS = {"places_visited": (3, 6), "albums": (2, 6), "interests": (3, 5)}
MAX_ALBUM_CHARS = 30
MAX_INTEREST_WORDS = 3


def load_relation_aliases():
    rel = yaml.safe_load((CONFIG / "relations.yaml").read_text())
    return rel, {a.lower() for aliases in rel.values() for a in aliases} | set(rel)


REGIONS = yaml.safe_load((CONFIG / "regions.yaml").read_text())
RELATIONS, ALIASES = load_relation_aliases()
GAZETTEER = {p.lower() for r in REGIONS.values() for p in r["home_cities"] + r["destinations"]}


def name_ok(n):
    """Unicode letters plus space . ' - (so René and D'Andre pass); starts with a letter."""
    return (bool(n) and n[0].isalpha() and (n[-1].isalpha() or n[-1] == ".")
            and all(c.isalpha() or c in " .'-" for c in n))


def tricky_ok(kind, names):
    checks = {
        "two_word":   lambda n: len(n.split()) >= 2 and "-" not in n,
        "hyphenated": lambda n: "-" in n,
        "apostrophe": lambda n: "'" in n,
        "long":       lambda n: len(n.replace(" ", "")) >= 11,
        "nickname":   lambda n: True,       # not string-checkable; the prompt asks for it
    }
    return kind == "none" or any(checks[kind](n) for n in names)


def validate(p, outline, used_names=()):
    v = []
    add = lambda check, msg: v.append((check, msg))

    # --- outline fields copied through unchanged
    for k in ("persona_id", "region", "household"):
        if p.get(k) != outline[k]:
            add("outline", f"{k} is {p.get(k)!r}, outline says {outline[k]!r}")

    owner = (p.get("owner") or {}).get("name", "")
    people = p.get("people", [])
    pets = p.get("pets", [])
    home = (p.get("owner") or {}).get("home_city", "")
    places = p.get("places_visited", [])

    # --- relations: exact multiset from the outline, canonical words only
    got = sorted(x.get("relation", "") for x in people)
    if got != sorted(outline["relations"]):
        add("relations", f"relations {got} != outline {sorted(outline['relations'])}")
    for x in people:
        if x.get("relation") not in RELATIONS:
            add("relations", f"non-canonical relation {x.get('relation')!r}")
    if not any(got.count(r) >= 2 for r in set(got)):
        add("relations", "no relation with 2+ people")

    # --- pets
    status = outline["pet"]["status"]
    if status == "none" and pets:
        add("pets", "outline says no pet")
    if status != "none":
        if len(pets) != 1:
            add("pets", f"expected 1 pet, got {len(pets)}")
        else:
            pet = pets[0]
            if pet.get("species") != outline["pet"]["species"]:
                add("pets", f"species {pet.get('species')!r} != {outline['pet']['species']!r}")
            if status == "named" and not pet.get("name"):
                add("pets", "named pet has no name")
            if status == "unnamed" and pet.get("name"):
                add("pets", "unnamed pet has a name")

    # --- names
    names = [owner] + [x.get("name", "") for x in people] + [pt["name"] for pt in pets if pt.get("name")]
    place_words = GAZETTEER | {s.lower() for s in [home] + places}
    seen = set()
    for n in names:
        low = n.lower().strip()
        if not name_ok(n) or len(n.split()) > 3:
            add("names", f"bad name format {n!r}")
        if low in seen:
            add("names", f"duplicate name {n!r}")
        seen.add(low)
        if low in ALIASES or low == "me" or any(t in ALIASES for t in low.split()):
            add("names", f"name collides with a relation word: {n!r}")
        if low in place_words or any(t in place_words for t in low.split()):
            add("names", f"name collides with a place: {n!r}")
    for n in [owner] + [x.get("name", "") for x in people]:
        if n.lower() in used_names:
            add("cross_persona", f"name already used by another persona: {n!r}")
    if not tricky_ok(outline["tricky_name"], names[1:]):
        add("tricky_name", f"no {outline['tricky_name']} name among people/pets")

    # --- places
    if home not in REGIONS[outline["region"]]["home_cities"]:
        add("places", f"home_city {home!r} not in the {outline['region']} list")
    if home.lower() in {s.lower() for s in places}:
        add("places", "home_city repeated in places_visited")

    # --- list sizes and uniqueness
    for k, (lo, hi) in LIMITS.items():
        xs = p.get(k, [])
        if not lo <= len(xs) <= hi:
            add(k, f"{k} has {len(xs)} items, want {lo}-{hi}")
        if len({x.lower() for x in xs}) != len(xs):
            add(k, f"duplicate {k}")

    # --- albums
    for a in p.get("albums", []):
        if not a.strip() or len(a) > MAX_ALBUM_CHARS:
            add("albums", f"bad album name {a!r}")

    # --- interests: short, and no place or person names leaking in
    lowered_names = {n.lower() for n in names}
    for it in p.get("interests", []):
        words = it.lower().replace("-", " ").split()
        if len(words) > MAX_INTEREST_WORDS:
            add("interests", f"interest too long: {it!r}")
        if any(w in place_words or w in lowered_names for w in words) or it.lower() in place_words:
            add("interests", f"interest mentions a place or person: {it!r}")

    return v
