"""
User simulator: writes one owner message per spec turn.

Code decides *what* each message must convey and the exact phrases it must contain (surface forms);
the LLM only writes the sentence. It never sees tool names, handles or argument syntax.
"""
import hashlib
import re
from pathlib import Path
from string import Template

import yaml
from pydantic import BaseModel

from specs.query_purity import DATE_PATTERNS, MONTHS, WEEKDAYS, _has_phrase, _lexicon
from specs.spec_validator import date_core

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "realize.yaml").read_text())
REL = yaml.safe_load((ROOT / "config" / "relations.yaml").read_text())
PROMPT = Template((ROOT / "realize" / "prompts" / "user_sim.txt").read_text())
TOOL_NAMES = re.compile(r"\b(search_images|ask_gallery|apply_effect|make_collage|move_to_album|delete_images)\b")
HANDLE = re.compile(r"\br\d+\b")
ME = re.compile(r"\b(me|myself|i|i'm|i've|my selfies|selfies)\b", re.I)
US = re.compile(r"\b(us|we|we're|we've)\b", re.I)
SELF = re.compile(r"\b(i'm|i am|myself|of me|with me|me wearing|me in|me at|my selfies|selfies)\b", re.I)
NARROW = ["Ask to narrow the photos you just saw down to only those {x}.",
          "Ask for just the ones {x}, out of the photos you just saw.",
          "Ask to filter the photos you just saw to the ones {x}.",
          "Ask to keep only the ones {x} from the photos you just saw."]
STYLE = {
    "terse": "Very short, like a quick text: lowercase is fine, no greeting, no please.",
    "casual": "Everyday chat, relaxed and friendly.",
    "polite": "Polite and complete sentences (\"could you please ...\").",
    "chatty": "Casual, talkative wording (\"hey\", \"could you maybe\", \"awesome\"), still one short message. "
              "Chatty is about wording, not explanations: no reasons or backstory.",
}


class UserSimError(RuntimeError):
    """The user simulator could not write a message that passes the checks (a content failure, not an API one)."""


class UserMessage(BaseModel):
    message: str


# ---------------------------------------------------------------- surface forms
def number_forms(canon):
    """(singular, plural) aliases for a canonical relation."""
    aliases = [canon] + [a for a in REL[canon] if a != canon]
    plural = {a for a in aliases if (a.endswith("s") and a[:-1] in aliases) or
              (a.endswith("ies") and a[:-3] + "y" in aliases)}
    return [a for a in aliases if a not in plural], sorted(plural)


def sample_surface(spec, persona, style, rng):
    """How each fixed value is said in this episode."""
    people = {}
    counts = {}
    for p in persona["people"]:
        counts[p["relation"]] = counts.get(p["relation"], 0) + 1
    for s in spec["steps"]:
        for v in s.get("args", {}).get("people", []):
            if v in people:
                continue
            if v in REL:                                   # a relation word
                singular, plural = number_forms(v)
                forms = plural if counts.get(v, 0) >= 2 and plural else singular
                use_alias = rng.random() < CFG["relation_alias"]
                canon_form = v + "s" if forms is plural and v + "s" in forms else v
                people[v] = rng.choice([f for f in forms if f != canon_form] or forms) if use_alias else canon_form
            else:
                people[v] = v                              # a name, a named pet, or "me"
    effects = {}
    for s in spec["steps"]:
        if s.get("call") == "apply_effect":
            e = s["args"]["effect"]
            words = CFG["effect_words"][e]
            pool = words["synonyms"] if rng.random() < CFG["effect_synonym"] else words["name"]
            effects[e] = rng.choice(pool)
    return {"style": style, "people": people, "effects": effects}


# ---------------------------------------------------------------- turn intents
def pick(options, *key):
    """Deterministic choice, so the prompt (and its cache key) is stable per episode and turn."""
    return options[int(hashlib.sha256("/".join(map(str, key)).encode()).hexdigest(), 16) % len(options)]


def describe_target(spec, handle):
    """How the owner refers to a photo set."""
    if handle == "r0":
        return "the photos you have selected"
    src = next((s for s in spec["steps"] if s.get("out") == handle), None)
    if src is None:
        return "them"
    if "event" in src:
        return "the photos you just selected"
    if src.get("call") == "apply_effect" and describe_target(spec, src["args"]["images"]) == "the collage":
        return "the edited collage"
    return {"search_images": "those photos", "ask_gallery": "those photos",
            "apply_effect": "the edited copies", "make_collage": "the collage"}.get(src["call"], "them")


def people_phrase(values, surface):
    return " and ".join("me" if v == "me" else surface["people"][v] for v in values)


def all_effect_words():
    """Every way of naming an effect; a request that must not say which effect avoids all of them."""
    words = {w for e in CFG["effect_words"].values() for w in e["name"] + e["synonyms"]}
    return sorted(words, key=lambda w: (-len(w), w))      # total order: set order varies between processes


def turn_intent(spec, turn, surface):
    """(plain-language lines, required phrases, forbidden phrases) for one user turn.
    The user asks for the steps in spec["requests"] for this turn (default: the turn's own steps); round-2
    turns also answer a clarification, reselect after a collage-limit question, or retry without a filter."""
    by_i = {s["i"]: s for s in spec["steps"]}
    k = spec["turns"].index(turn)
    asked = spec["requests"][k] if "requests" in spec else [i for i in turn if "call" in by_i[i]]
    lines, required, forbidden = [], [], []
    first = by_i[turn[0]]
    prev = by_i.get(turn[0] - 1, {})

    def target(h):
        src = next((x for x in spec["steps"] if x.get("out") == h), None)
        if src and "event" in src and src["i"] not in turn:      # a selection that happens later: its source
            return describe_target(spec, src["from"])
        return describe_target(spec, h)

    if turn[0] == 1 and spec["initial_selection"]:
        lines.append(f"Before this message you selected {spec['initial_selection']['count']} photos in the app. "
                     "The app already tells the assistant, so don't say you selected them; just call them \"these\".")
    # round-2 turn openers
    if prev.get("expect") == "ask" and prev["about"] in ("album", "effect") and "call" in first:
        if prev["about"] == "album":
            a = first["args"]["album"]
            lines.append(f"The assistant asked which album. Answer with the album name: {a}"
                         + (" (a new album)." if first["outcome"]["created"] else " (one of your albums)."))
            required.append(a)
        else:
            word = surface["effects"][first["args"]["effect"]]
            lines.append(f"The assistant asked which effect. Answer: \"{word}\".")
            required.append(word)
    if prev.get("expect") == "ask" and prev["about"] == "select" and "event" in first:
        lines.append(f"The assistant asked you to pick fewer photos. You have now selected {first['count']} in the "
                     "app; just tell it to use these. Don't repeat what the assistant said and don't say you selected them.")
    for i in turn:
        s = by_i[i]
        if "event" in s and not (i == turn[0] and prev.get("about") == "select"):
            lines.append(f"You have just selected {s['count']} of {describe_target(spec, s['from'])} in the app. "
                         "The app already tells the assistant, so don't say you selected them; just call them \"these\".")
    for i in asked:
        s = by_i[i]
        if "call" not in s:
            continue
        a, call = s["args"], s["call"]
        withheld = next((x["about"] for x in spec["steps"] if x.get("expect") == "ask" and x["i"] == i - 1
                         and x["about"] in ("album", "effect") and i not in turn), None)
        if call == "search_images":
            if "loosens" in s:
                src = by_i[s["loosens"]]["args"]
                dropped = next(x for x in src if x not in a)
                what = {"people": "the people filter", "location": "the place", "date": "the date",
                        "query": "the description of what is in the photos"}[dropped]
                lines.append(f"Nothing was found. Ask to try again without {what} (keep everything else).")
                if dropped != "query":
                    val = src[dropped]
                    forbidden += [surface["people"].get(v, v) for v in val if v != "me"] if dropped == "people" \
                        else [date_core(val) if dropped == "date" else val]
                continue
            if "refines" in s:
                prev_args = by_i[s["refines"]]["args"]
                new = {x: v for x, v in a.items() if x not in prev_args}
                lines.append(pick(NARROW, spec["episode_id"], i).format(x=slot_text(new, surface)))
            else:
                new = a
                lines.append("Ask to see photos " + slot_text(a, surface) + ".")
            required += required_for(new, surface)
        elif call == "ask_gallery":
            lines.append(f"Ask this question in your own words (keep its meaning): {a['question']}")
        elif call == "apply_effect":
            if withheld == "effect":
                lines.append(f"Ask to add a photo effect or filter to {target(a['images'])}, but don't say which one.")
                forbidden += all_effect_words()
            else:
                word = surface["effects"][a["effect"]]
                lines.append(f"Ask to apply a \"{word}\" effect to {target(a['images'])}.")
                required.append(word)
        elif call == "make_collage":
            lines.append(f"Ask to make a collage of {target(a['images'])}.")
        elif call == "move_to_album":
            if withheld == "album":
                lines.append(f"Ask to move {target(a['images'])} into an album, but don't say which album.")
                forbidden.append(a["album"])
            else:
                lines.append(f"Ask to move {target(a['images'])} to "
                             + (f"a new album called \"{a['album']}\"." if s["outcome"]["created"]
                                else f"your album \"{a['album']}\"."))
                required.append(a["album"])
        elif call == "delete_images":
            lines.append(f"Ask to delete {target(a['images'])}.")
    return lines, required, forbidden


def slot_text(args, surface):
    parts = []
    if "people" in args:
        parts.append("of " + people_phrase(args["people"], surface))
    if "query" in args:
        parts.append(f"showing: {args['query']} (say this briefly in your own words, in about as many words; "
                     "don't elaborate)")
    if "location" in args:
        parts.append(f"from {args['location']}")
    if "date" in args:
        parts.append(f"from {date_core(args['date'])}")
    return ", ".join(parts)


def required_for(args, surface):
    req = []
    for v in args.get("people", []):
        req.append("<me>" if v == "me" else surface["people"][v])
    if "location" in args:
        req.append(args["location"])
    if "date" in args:
        req.append(date_core(args["date"]))
    return req


# ---------------------------------------------------------------- checks + call
def search_args_in_turn(spec, turn):
    by_i = {s["i"]: s for s in spec["steps"]}
    return [by_i[i]["args"] for i in turn if by_i[i].get("call") == "search_images"]


def extra_filters(msg, required, searches, persona):
    """People, places or dates in the message that no search in this turn has (the teacher would add them)."""
    if not searches:
        return []
    low = msg.lower()
    for r in sorted(required, key=len, reverse=True):   # required phrases (album names, ...) are allowed;
        if r != "<me>":                                 # longest first, so "Queenstown 2022" goes before "Queenstown"
            low = low.replace(r.lower(), " ")
    _, places, festivals, _ = _lexicon()
    allowed_people = {v.lower() for a in searches for v in a.get("people", [])}
    rel_of = {p["name"].lower(): p["relation"] for p in persona["people"]}
    allowed_people |= {w.lower() for v in list(allowed_people) if v in rel_of
                       for w in [rel_of[v]] + REL.get(rel_of[v], [])}
    names = {p["name"].lower() for p in persona["people"]} | {p["name"].lower() for p in persona["pets"] if p["name"]}
    rel_words = {w.lower() for c, al in REL.items() for w in [c] + al}
    v = []
    for n in sorted(names | rel_words):
        if n not in allowed_people and _has_phrase(low, n) and not any(
                n in {x.lower() for x in REL.get(p, [])} for p in allowed_people):
            v.append(f"don't mention {n!r}: this request is not about them")
    if US.search(low) and "me" not in allowed_people:
        v.append("don't say \"us\" or \"we\": the photos are not filtered by people")
    if SELF.search(low) and "me" not in allowed_people:
        v.append("don't put yourself in the photos (\"of me\", \"I'm wearing\"): the request is not about you")
    has_loc = any("location" in a for a in searches)
    if not has_loc and any(_has_phrase(low, p) for p in places):
        v.append("don't mention a place: this request has no place")
    has_date = any("date" in a for a in searches)
    words = re.findall(r"[a-z0-9']+", low)
    if not has_date and (any(_has_phrase(low, f) for f in festivals) or any(p.search(low) for p in DATE_PATTERNS)
                         or any(w in MONTHS or w in WEEKDAYS for w in words)):
        v.append("don't mention a date or time: this request has none")
    return v


def check_message(msg, required, searches=(), persona=None, forbidden=()):
    v = extra_filters(msg, required, searches, persona) if persona else []
    low = msg.lower()
    for f in forbidden:
        if f and re.search(rf"(?<![\w]){re.escape(f.lower())}(?![\w])", low):
            v.append(f"don't say \"{f}\" in this message")
    for r in required:
        if r == "<me>":
            if not ME.search(msg):
                v.append("refer to yourself (me / myself / I)")
        elif r.lower() not in low:
            v.append(f"include the exact words \"{r}\"")
    if HANDLE.search(msg):
        v.append("don't mention ids like r1")
    if TOOL_NAMES.search(msg):
        v.append("don't mention tool names")
    if not msg.strip():
        v.append("write a message")
    if len(msg.split()) > CFG["user_sim"]["max_words"]:
        v.append(f"keep it under {CFG['user_sim']['max_words']} words")
    return v


def render_persona(p):
    people = ", ".join(f"{x['name']} ({x['relation']})" for x in p["people"])
    pets = ", ".join(f"{x['name'] or 'an unnamed'} {x['species']}" for x in p["pets"]) or "none"
    return (f"You are {p['owner']['name']}, {p['owner']['age']}, from {p['owner']['home_city']}. "
            f"People in your life: {people}. Pets: {pets}.")


def build_user_prompt(spec, persona, turn, surface, history, extra="", feedback=""):
    """The user-simulator prompt for one turn (pure: no LLM). Also used by the golden tests."""
    lines, required, forbidden = turn_intent(spec, turn, surface)
    return PROMPT.substitute(
        persona=render_persona(persona), style=STYLE[surface["style"]],   # no motivation: no backstory to leak
        history=history or "(this is your first message)",
        intent="\n".join(f"- {l}" for l in lines) + (f"\n- {extra}" if extra else ""),
        required=", ".join("yourself (me / I)" if r == "<me>" else f"\"{r}\"" for r in required) or "(none)",
        forbidden=", ".join(f"\"{f}\"" for f in forbidden) or "(none)",
        feedback=feedback)


def write_message(llm, spec, persona, turn_no, turn, surface, history, extra=""):
    """One user message for a turn. Returns (message, meta, attempts)."""
    _, required, forbidden = turn_intent(spec, turn, surface)
    feedback = ""
    for attempt in range(CFG["user_sim"]["max_attempts"]):
        prompt = build_user_prompt(spec, persona, turn, surface, history, extra, feedback)
        res = llm.json(prompt, UserMessage, seed=f"{spec['episode_id']}/u{turn_no}/a{attempt}")
        msg = res.output["message"].strip()
        v = check_message(msg, required, search_args_in_turn(spec, turn), persona, forbidden)
        if not v:
            return msg, res.meta, attempt + 1
        feedback = "\n\nYour previous message was rejected. Fix: " + "; ".join(v)
    raise UserSimError(f"{spec['episode_id']} turn {turn_no}: user message failed checks: {v}")
