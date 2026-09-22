"""
Intent filler (LLM, one call per spec): writes the free-text fields of a skeleton in one pass so the
episode tells one story. Everything else in the spec is fixed by the sampler.

    build_prompt(skel, persona, feedback) -> prompt
    fill(llm, skel, persona, seed, feedback) -> (filled spec, generation meta)
"""
import copy
import json
from pathlib import Path
from string import Template
from typing import List, Optional

from pydantic import BaseModel

from specs.spec_sampler import FILL

ROOT = Path(__file__).resolve().parent.parent
PROMPT = Template((ROOT / "specs" / "prompts" / "intent_filler.txt").read_text())

ASK_TYPES = {
    "document_fact": "asks for a fact written on a photographed document (a passport or policy number, a flight "
                     "booking code, a bill amount). The answer is that exact value, e.g. \"K4829163\".",
    "count": "asks how many photos of something there are. The answer states a number.",
    "when": "asks when something happened or was photographed. The answer gives a date or period.",
    "yes_no": "asks whether they have photos of something. The answer is yes, with a short description.",
    "what": "asks what something in their photos was (a dish, a place's name, what someone wore). Short answer.",
}
QUERY_RULES = """
## Query rules
- A query holds only what is visible in the photo, all lowercase, in the style and length of the examples.
  Write a fresh query that fits the story; do not copy an example.
- The other slots of that search already cover people, place and date, so the query never repeats them.
- When `people` is set, the query doesn't describe those people again: "in a red saree", not
  "woman in a red saree"; "playing in the snow", not "kids playing in the snow".
- No names of people or pets, no relation words (mom, son, friend, ...), no "me", "my", "I", "our", "selfie".
- No place names (a type of place like "beach" or "temple" is fine), no dates, months, years or festival names.
- Don't start with "photos of" or "pictures of".
"""


class QueryFill(BaseModel):
    step: int
    query: str


class Fill(BaseModel):
    motivation: str
    queries: List[QueryFill]
    question: Optional[str]
    answer: Optional[str]
    album: Optional[str]


class FillExistingAlbum(Fill):
    """For moves sampled into an existing album: the filler may say none fits (-> a new album)."""
    album_is_new: bool


def render_persona(p):
    o = p["owner"]
    people = ", ".join(f"{x['name']} ({x['relation']})" for x in p["people"])
    pets = ", ".join(f"{x['name'] or 'an unnamed'} ({x['species']})" for x in p["pets"]) or "none"
    return "\n".join([
        f"- {o['name']}, {o['age']}, lives in {o['home_city']} ({p['region']}, {p['household'].replace('_', ' ')})",
        f"- People: {people}",
        f"- Pets: {pets}",
        f"- Trips: {', '.join(p['places_visited'])}",
        f"- Existing albums: {', '.join(p['albums'])}",
        f"- Interests: {', '.join(p['interests'])}",
    ])


def render_args(args):
    parts = []
    for k, v in args.items():
        if v == FILL:
            v = {"query": "<QUERY>", "question": "<QUESTION>", "album": "<ALBUM>"}[k]
        parts.append(f"{k}={json.dumps(v, ensure_ascii=False)}")
    return ", ".join(parts)


def render_steps(skel):
    lines = []
    if skel["initial_selection"]:
        lines.append(f"0. [before the conversation the owner has {skel['initial_selection']['count']} photos "
                     f"selected → r0]")
    for s in skel["steps"]:
        if "event" in s:
            lines.append(f"{s['i']}. [owner selects {s['count']} of the photos in {s['from']} → {s['out']}]")
            continue
        if "expect" in s:
            lines.append(f"{s['i']}. [no tool call: " + {
                "report": "the assistant says nothing was found and suggests loosening a filter]",
                "album": "the owner asked to move them to an album without saying which; the assistant asks which album]",
                "effect": "the owner asked for a photo effect without saying which; the assistant asks which one]",
                "select": f"too many photos for a collage (max {s.get('max')}); the assistant asks the owner to "
                          f"select at most {s.get('max')}]",
            }[s["about"] if s["about"] != "no_results" else "report"])
            continue
        o, call = s["outcome"], s["call"]
        if s.get("skipped"):
            lines.append(f"{s['i']}. (asked for, but not done because nothing was found) {call}({render_args(s['args'])})")
            continue
        if o.get("error") == "no_results":
            lines.append(f"{s['i']}. {call}({render_args(s['args'])}) → no photos found")
            continue
        if call == "delete_images" and o.get("status") == "cancelled":
            lines.append(f"{s['i']}. {call}({render_args(s['args'])}) → the owner cancels in the app's confirmation "
                         "dialog; nothing is deleted")
            continue
        result = {
            "search_images": lambda: f"{o['count']} photos ({s['out']})",
            "ask_gallery": lambda: f"answer <ANSWER>, backed by {o['count']} photos ({s['out']})",
            "apply_effect": lambda: f"{o['count']} new copies ({s['out']}); originals unchanged",
            "make_collage": lambda: f"1 collage image ({s['out']})",
            "move_to_album": lambda: f"moved {o['count']} ({'new album created' if o['created'] else 'existing album'})",
            "delete_images": lambda: f"deleted {o['count']}",
        }[call]()
        note = (f"   (refines the search in step {s['refines']})" if "refines" in s else
                f"   (the owner tries again without one filter from step {s['loosens']})" if "loosens" in s else "")
        lines.append(f"{s['i']}. {call}({render_args(s['args'])}) → {result}{note}")
    return "\n".join(lines)


def render_fields(skel):
    out = []
    queries = [f for f in skel["fill"] if f["field"] == "query"]
    if queries:
        out.append("- queries: one entry per <QUERY> above, with its step number:")
        for f in queries:
            covered = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in f["with"].items()) or "nothing else"
            out.append(f"  - step {f['step']}: a {f['length']} query ({ {'short': '1-2', 'medium': '3-4', 'long': '5-6'}[f['length']] } words) "
                       f"of kind \"{f['category']}\". Other slots already cover: {covered}. "
                       f"Examples of the style: {'; '.join(json.dumps(e) for e in f['examples'])}")
    else:
        out.append("- queries: [] (no search in this episode needs a query)")
    ask = next((f for f in skel["fill"] if f["field"] == "question"), None)
    if ask:
        then = (" The owner acts on the photos it returns in the next step, so it must surface photos of something."
                if ask["before_action"] else "")
        out.append(f"- question: the <QUESTION> for ask_gallery. It {ASK_TYPES[ask['type']]}{then} "
                   f"Self-contained: no \"it\", \"that\", \"this\", \"those\", \"they\", \"there\"; name the thing.")
        out.append(f"- answer: the <ANSWER> the gallery gives, consistent with {ask['count']} backing photo(s). "
                   f"One short sentence at most; numbers and codes written exactly.")
    else:
        out.append("- question: null\n- answer: null")
    album = next((f for f in skel["fill"] if f["field"] == "album"), None)
    if album and album["exists"]:
        out.append("- album: the <ALBUM>: copy exactly the one existing album name that fits these photos "
                   "as the search arguments describe them, and set album_is_new to false. If none of the existing "
                   "albums fits, write a new album name instead (as the owner would type it, max 30 characters) "
                   "and set album_is_new to true.")
    elif album:
        out.append("- album: the <ALBUM>: a new album name the owner creates, as they would type it "
                   "(max 30 characters). It must not be one of the existing albums.")
    else:
        out.append("- album: null")
    return "\n".join(out)


def render_turns(skel):
    return "; ".join(f"turn {n}: step{'s' if len(t) > 1 else ''} {', '.join(map(str, t))}"
                     for n, t in enumerate(skel["turns"], 1))


def build_prompt(skel, persona, feedback=()):
    fb = ""
    if feedback:
        fb = "\n## Your previous attempt was rejected — fix these\n" + "\n".join(f"- {m}" for _, m in feedback)
    has_query = any(f["field"] == "query" for f in skel["fill"])
    return PROMPT.substitute(
        persona=render_persona(persona), steps=render_steps(skel), turns=render_turns(skel),
        effects=", ".join(skel["config"]["effects"]), fields=render_fields(skel),
        query_rules=QUERY_RULES if has_query else "", feedback=fb,
    )


def merge(skel, out):
    """Write the filler output into a copy of the skeleton. Refinements inherit the earlier query."""
    spec = copy.deepcopy(skel)
    spec["motivation"] = out["motivation"].strip()
    queries = {q["step"]: q["query"].strip() for q in out["queries"]}
    by_i = {s["i"]: s for s in spec["steps"]}
    for s in spec["steps"]:
        if "event" in s or "expect" in s:
            continue
        a = s["args"]
        if a.get("query") == FILL:
            src = s.get("refines") or s.get("loosens")
            a["query"] = queries.get(s["i"]) or (by_i[src]["args"].get("query") if src else FILL)
        if a.get("question") == FILL:
            a["question"] = (out.get("question") or FILL).strip()
            s["outcome"]["answer"] = (out.get("answer") or FILL).strip()
        if a.get("album") == FILL:
            a["album"] = (out.get("album") or FILL).strip()
            s["outcome"]["album"] = a["album"]
            if out.get("album_is_new") and not s["outcome"]["created"]:   # "none fits" -> new album
                s["outcome"]["created"] = True
                spec["sampling"]["album_flipped"] = True
    return spec


def fill(llm, skel, persona, seed, feedback=()):
    existing = any(f["field"] == "album" and f["exists"] for f in skel["fill"])
    res = llm.json(build_prompt(skel, persona, feedback), FillExistingAlbum if existing else Fill, seed=seed)
    return merge(skel, res.output), res.meta
