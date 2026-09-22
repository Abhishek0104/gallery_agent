"""
Query purity (code only): a search `query` holds visual content only.

Names, relations, places, dates and "me"/"my" belong in other slots (docs/intent_filler_design.md §6).
Shared by the query-pool builder and, later, the intent validator (check 5), so both apply the
same rules.

    check_query(text)                 -> global checks (relations, places, dates, pronouns, format)
    check_query(text, persona=p)      -> also this persona's people and pet names
    check_query(text, people=True)    -> also: the query must not re-describe the people already in `people`
    persona_name_hits(text)           -> ids of personas whose names appear in text
"""
import json
import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
PERSONAS = ROOT / "data" / "personas"

MAX_WORDS = 8
PRONOUNS = {"i", "me", "my", "mine", "myself", "we", "us", "our", "ours", "selfie", "selfies"}
PHOTO_WORDS = {"photo", "photos", "picture", "pictures", "pic", "pics", "image", "images", "screenshot", "screenshots"}
MONTHS = ("january february march april june july august september october november december "
          "jan feb mar apr jun jul aug sep sept oct nov dec").split()     # "may" left out: a common verb
WEEKDAYS = "monday tuesday wednesday thursday friday saturday sunday".split()
DATE_WORDS = {"today", "yesterday", "tonight", "ago", "weekend"}     # "birthday"/"anniversary" are content
TIME_UNITS = r"(week|month|year|weekend|summer|winter|spring|autumn|fall|monsoon|vacation|holiday|holidays|trip)"
PERSON_NOUNS = {"man", "men", "woman", "women", "girl", "girls", "boy", "boys", "kid", "kids", "child",
                "children", "baby", "babies", "toddler", "toddlers", "teen", "teens", "teenager", "teenagers",
                "lady", "ladies", "guy", "guys", "person", "people", "couple", "family", "adult", "adults"}
# words that introduce someone *else* ("hugging a baby", "with kids"), so a person noun after them is fine
RELATIONAL_LEAD = {"with", "and", "holding", "carrying", "hugging", "feeding", "kissing", "beside", "next",
                   "near", "behind", "among", "between", "of", "for", "to", "playing", "helping", "watching"}
DATE_PATTERNS = [
    re.compile(r"\b(19|20)\d{2}\b"),
    re.compile(rf"\b(last|this|next|previous)\s+{TIME_UNITS}\b"),
    re.compile(r"\b\d+\s+(days?|weeks?|months?|years?)\b"),
]


@lru_cache(maxsize=None)
def _lexicon():
    rel = yaml.safe_load((CONFIG / "relations.yaml").read_text())
    relations = {a.lower() for aliases in rel.values() for a in aliases} | set(rel)
    regions = yaml.safe_load((CONFIG / "regions.yaml").read_text())
    places = {p.lower() for r in regions.values() for p in r["home_cities"] + r["destinations"]}
    festivals = {f.lower() for r in regions.values() for f in r["festivals"]} | {"new year", "new year's eve"}
    personas = [json.loads(f.read_text()) for f in sorted(PERSONAS.glob("persona_*.json"))]
    for p in personas:
        places |= {s.lower() for s in p["places_visited"] + [p["owner"]["home_city"]]}
    return relations, places, festivals, personas


def persona_names(p):
    names = [p["owner"]["name"]] + [x["name"] for x in p["people"]] + [x["name"] for x in p["pets"] if x["name"]]
    return {n.lower() for n in names}


def _has_phrase(text, phrase):
    return re.search(rf"(?<![\w']){re.escape(phrase)}(?![\w'])", text) is not None


def describes_subject(words):
    """True if a person noun is the query's subject (within the first 3 words, not introduced as someone else)."""
    for k, w in enumerate(words[:3]):
        if w in PERSON_NOUNS:
            return not any(x in RELATIONAL_LEAD for x in words[:k])
    return False


def check_query(text, persona=None, people=False):
    """List of (check, message); empty = pure."""
    relations, places, festivals, _ = _lexicon()
    v = []
    add = lambda check, msg: v.append((check, msg))
    t = text.strip()
    low = t.lower()
    words = re.findall(r"[a-z0-9']+", low)

    if not words:
        return [("format", "empty query")]
    if len(words) > MAX_WORDS:
        add("format", f"more than {MAX_WORDS} words")
    if t != low:
        add("format", "not lowercase (capitals usually mean a name or place)")
    # "photos of X" / "pictures of X" wraps the content; "group photo", "passport photo page" are content
    if words[0] in PHOTO_WORDS or re.search(r"\b(photos?|pictures?|pics?|images?)\s+of\b", low) \
            or any(w.startswith("screenshot") for w in words):
        add("format", "mentions photos/pictures instead of content")
    if any(w in PRONOUNS for w in words):
        add("pronoun", f"pronoun or selfie: {sorted(set(words) & PRONOUNS)}")
    if hits := sorted({w for w in words if w in relations}):
        add("relation", f"relation word: {hits}")
    if hits := sorted(p for p in places if _has_phrase(low, p)):
        add("place", f"place name: {hits}")
    if hits := sorted(f for f in festivals if _has_phrase(low, f)):
        add("date", f"festival/holiday name: {hits}")
    if any(w in MONTHS or w in WEEKDAYS or w in DATE_WORDS for w in words) or any(p.search(low) for p in DATE_PATTERNS):
        add("date", "date expression")
    if people and describes_subject(words):
        add("subject", "re-describes the person already in `people` (\"woman in a red saree\" -> \"in a red saree\")")
    if persona is not None:
        if hits := sorted(n for n in persona_names(persona) if _has_phrase(low, n)):
            add("name", f"persona name: {hits}")
    return v


def persona_name_hits(text):
    """Persona ids whose people/pet names appear in text (e.g. a cat named Jalebi vs "plate of jalebi")."""
    low = text.lower()
    return [p["persona_id"] for p in _lexicon()[3] if any(_has_phrase(low, n) for n in persona_names(p))]
