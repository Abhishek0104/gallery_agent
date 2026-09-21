"""
Generate personas: outline (code) -> LLM fill -> validator -> data/personas/persona_XX.json

    python -m specs.make_personas            # generate all (cached calls are free)
    python -m specs.make_personas --dry-run  # print the first prompt, no LLM call
    python -m specs.make_personas --limit 1  # smoke test: one persona
    python -m specs.make_personas --show     # print a summary of the saved personas

Personas are generated one at a time so each one can exclude names already used.
A rejected attempt is retried with the violations appended to the prompt (a new cache key).
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from string import Template
from typing import List, Optional

from pydantic import BaseModel

from llm import LLM
from specs.persona_outline import sample_outlines
from specs.persona_validator import REGIONS, validate

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "personas"
PROMPT = Template((ROOT / "specs" / "prompts" / "persona.txt").read_text())
VERSION = 1

TRICKY = {
    "none":       "no special requirement.",
    "two_word":   "one of the people above, or the pet, goes by a two-word first name (e.g. \"Mary Ann\", \"Rose Marie\", \"Sai Krishna\").",
    "hyphenated": "one of the people above, or the pet, has a hyphenated name (e.g. \"Jean-Luc\", \"Anne-Marie\", \"Mei-Ling\").",
    "apostrophe": "one of the people above, or the pet, has a name with an apostrophe (e.g. \"D'Andre\", \"Ke'Ana\", a pet called \"O'Malley\").",
    "nickname":   "at least one person goes by a family nickname rather than a given name (e.g. \"Chintu\", \"Bubbles\", \"Bunty\", \"Lulu\").",
    "long":       "one of the people above, or the pet, has a long name of 11+ letters (e.g. \"Venkateshwaran\", \"Maximilian\", \"Oluwaseun\").",
}


class Person(BaseModel):
    relation: str
    name: str


class Pet(BaseModel):
    species: str
    name: Optional[str]


class PersonaContent(BaseModel):
    owner_name: str
    home_city: str
    people: List[Person]
    pets: List[Pet]
    places_visited: List[str]
    albums: List[str]
    interests: List[str]


def build_prompt(o, used_names, feedback=()):
    region = REGIONS[o["region"]]
    pet = o["pet"]
    pet_line = {"named": f"one {pet['species']}, with a name",
                "unnamed": f"one {pet['species']}, which the owner never names (name = null)",
                "none": "no pet"}[pet["status"]]
    pet_field = ("[]" if pet["status"] == "none"
                 else f"exactly one entry with species \"{pet['species']}\"")
    fb = ""
    if feedback:
        fb = "\n## Your previous attempt was rejected — fix these\n" + "\n".join(f"- {m}" for _, m in feedback)
    return PROMPT.substitute(
        region=o["region"], name_style=region["names"], household=o["household"].replace("_", " "),
        age=o["owner"]["age"], gender={"m": "man", "f": "woman"}[o["owner"]["gender"]],
        relations="\n".join(f"  {i}. {r}" for i, r in enumerate(o["relations"], 1)),
        pet=pet_line, pet_field=pet_field, tricky=TRICKY[o["tricky_name"]],
        home_cities=", ".join(region["home_cities"]), destinations=", ".join(region["destinations"]),
        used_names=", ".join(sorted(used_names)) or "(none yet)", feedback=fb,
    )


def clean(name):
    return name.replace("\u2019", "'").strip() if name else name


def to_persona(o, c):
    return {
        "persona_id": o["persona_id"], "version": VERSION,
        "region": o["region"], "household": o["household"],
        "owner": {"name": clean(c["owner_name"]), "gender": o["owner"]["gender"],
                  "age": o["owner"]["age"], "home_city": c["home_city"]},
        "people": [{"name": clean(x["name"]), "relation": x["relation"]} for x in c["people"]],
        "pets": [{"name": clean(x["name"]), "species": x["species"]} for x in c["pets"]],
        "places_visited": c["places_visited"],
        "albums": c["albums"],
        "interests": c["interests"],
    }


def generate(max_attempts, dry_run=False, limit=None):
    llm = LLM("generation")
    outlines = sample_outlines()[:limit]
    if dry_run:
        print(build_prompt(outlines[0], set()))
        return
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "_outlines.json").write_text(json.dumps(outlines, indent=2))

    used, report = set(), {"personas": {}, "rejections_by_check": Counter()}
    for o in outlines:
        feedback, persona = (), None
        for attempt in range(max_attempts):
            prompt = build_prompt(o, used, feedback)
            c = llm.json(prompt, PersonaContent, seed=f"{o['persona_id']}/a{attempt}")
            candidate = to_persona(o, c)
            feedback = validate(candidate, o, used)
            report["rejections_by_check"].update(check for check, _ in feedback)
            if not feedback:
                persona = candidate
                break
            print(f"{o['persona_id']} attempt {attempt}: rejected: {[m for _, m in feedback]}")
        report["personas"][o["persona_id"]] = {
            "accepted": persona is not None, "attempts": attempt + 1,
            "last_violations": [m for _, m in feedback]}
        if persona is None:
            print(f"{o['persona_id']}: FAILED after {max_attempts} attempts")
            continue
        (OUT / f"{o['persona_id']}.json").write_text(json.dumps(persona, indent=2, ensure_ascii=False))
        used |= {persona["owner"]["name"].lower()} | {x["name"].lower() for x in persona["people"]}
        print(f"{o['persona_id']}: ok ({attempt + 1} attempt{'s' * bool(attempt)})")

    (OUT / "_report.json").write_text(json.dumps(report, indent=2))
    ok = sum(v["accepted"] for v in report["personas"].values())
    print(f"\naccepted {ok}/{len(outlines)}; rejections by check: {dict(report['rejections_by_check'])}")


def show():
    for f in sorted(OUT.glob("persona_*.json")):
        p = json.loads(f.read_text())
        o = p["owner"]
        pets = ", ".join(f"{x['name'] or '(unnamed)'} the {x['species']}" for x in p["pets"]) or "no pet"
        print(f"\n{p['persona_id']}  {o['name']} ({o['gender']}, {o['age']}), {o['home_city']}"
              f"  [{p['region']} / {p['household']}]")
        print("  people:   " + ", ".join(f"{x['name']} ({x['relation']})" for x in p["people"]))
        print(f"  pets:     {pets}")
        print("  places:   " + ", ".join(p["places_visited"]))
        print("  albums:   " + ", ".join(p["albums"]))
        print("  interests:" + ", ".join(" " + i for i in p["interests"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--limit", type=int, help="only the first N personas (smoke test)")
    args = ap.parse_args()
    show() if args.show else generate(args.max_attempts, args.dry_run, args.limit)
