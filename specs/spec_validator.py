"""
Intent validator (code only), docs/intent_filler_design.md §8.

validate(spec, persona) -> list of (check, message); empty = accepted. Checks:
  schema     args match the registry (names, required, types); handles exist and are alive; no <fill> left
  effect     effect is in this episode's effect list
  people     values are me / persona names / relations (after alias normalization) / named pets
  slots      search args match the sampled slot pattern
  purity     query holds visual content only (shared check with the query pool)
  ask        question is self-contained and has an answer
  album      album name agrees with the sampled outcome (existing persona album vs a new name)
  counts     outcome counts obey the registry and the handle ledger
  turns      every step is in exactly one turn, in order; a selection starts a turn
"""
import json
import re


from registry import load_registry
from registry.args import SLOTS, date_core, normalize_person  # noqa: F401  (re-exported)
from specs.query_purity import check_query
from specs.spec_sampler import FILL

PRONOUNS = re.compile(r"\b(it|that|those|these|them|this|they|he|she|him|there)\b", re.I)
POSSESSIVES = re.compile(r"\b(its|her|his|hers|their)\b", re.I)


def self_contained(q):
    """No bare pronouns; a possessive is fine only after a named person ("Anika ... her birthday cake")."""
    if PRONOUNS.search(q):
        return False
    m = POSSESSIVES.search(q)
    return m is None or re.search(r"(?<!^)\b[A-Z][a-z]+", q[:m.start()]) is not None
MAX_ALBUM_CHARS = 30
MAX_ANSWER_CHARS = 200


def type_ok(value, typ):
    if typ == "str":
        return isinstance(value, str) and value.strip() != ""
    if typ == "list[str]":
        return isinstance(value, list) and value and all(isinstance(x, str) and x.strip() for x in value)
    if typ == "ImageSet":
        return isinstance(value, str) and re.fullmatch(r"r\d+", value) is not None
    if typ == "enum":
        return isinstance(value, str)
    return False


def validate(spec, persona):
    v = []
    add = lambda check, msg: v.append((check, msg))
    reg = load_registry()
    cmax = spec["config"]["collage_max"]

    if FILL in json.dumps(spec["steps"]) or spec.get("motivation") in (None, "", FILL):
        add("schema", "unfilled <fill> field")

    # handle ledger
    ledger = {}
    if spec["initial_selection"]:
        ledger["r0"] = {"count": spec["initial_selection"]["count"], "alive": True}

    names = {p["name"] for p in persona["people"]}
    relations = {p["relation"] for p in persona["people"]}
    pets = {p["name"] for p in persona["pets"] if p["name"]}
    patterns = iter(spec["sampling"]["slot_patterns"])
    prev_search = None

    for s in spec["steps"]:
        if "expect" in s or s.get("skipped"):             # assistant-only / asked-but-not-done steps
            continue
        if "event" in s:
            src = ledger.get(s["from"])
            if not src or not src["alive"]:
                add("schema", f"step {s['i']}: selection from unknown handle {s['from']}")
            elif not 1 <= s["count"] <= src["count"]:
                add("counts", f"step {s['i']}: selected {s['count']} of {src['count']}")
            ledger[s["out"]] = {"count": s["count"], "alive": True}
            continue

        tool = reg.get(s["call"])
        if tool is None:
            add("schema", f"step {s['i']}: unknown tool {s['call']}")
            continue
        spec_args = tool["model_facing"]["args"]
        args, out = s["args"], s["outcome"]
        for k in args:
            if k not in spec_args:
                add("schema", f"step {s['i']}: {s['call']} has no argument {k!r}")
        for k, a in spec_args.items():
            if a.get("required") and k not in args:
                add("schema", f"step {s['i']}: missing required {k!r}")
            if k in args and not type_ok(args[k], a["type"]):
                add("schema", f"step {s['i']}: {k}={args[k]!r} is not a {a['type']}")
        for k, a in spec_args.items():
            if a["type"] == "ImageSet" and k in args:
                h = ledger.get(args[k])
                if not h or not h["alive"]:
                    add("schema", f"step {s['i']}: handle {args[k]} does not exist or was deleted")
        src_count = ledger.get(args.get("images"), {}).get("count")

        if s["call"] == "search_images":
            if not args:
                add("schema", f"step {s['i']}: search with no arguments")
            got = "+".join(x for x in SLOTS if x in args)
            want = next(patterns, None)
            if got != want:
                add("slots", f"step {s['i']}: slots {got} != sampled {want}")
            for p in args.get("people", []):
                if p != "me" and p not in names and normalize_person(p) not in relations and p not in pets:
                    add("people", f"step {s['i']}: {p!r} is not me, a name, a relation or a named pet")
            if isinstance(args.get("query"), str):
                for check, msg in check_query(args["query"], persona, people="people" in args):
                    add("purity", f"step {s['i']}: query {args['query']!r}: {msg}")
            if "loosens" in s:
                src_args = next(x for x in spec["steps"] if x["i"] == s["loosens"])["args"]
                missing = [k for k in src_args if k not in args]
                if len(missing) != 1 or any(args.get(k) != v for k, v in src_args.items() if k in args) \
                        or any(k not in src_args for k in args):
                    add("slots", f"step {s['i']}: a loosened search must drop exactly one earlier filter")
            if "refines" in s and prev_search:
                for k, val in prev_search["args"].items():
                    if args.get(k) != val:
                        add("slots", f"step {s['i']}: refinement dropped or changed {k}")
                if out["count"] >= prev_search["outcome"]["count"]:
                    add("counts", f"step {s['i']}: refinement did not narrow the results")
            if out.get("count", 0) < 1 and out.get("error") != "no_results":
                add("counts", f"step {s['i']}: search needs 1+ results unless it is a no_results outcome")
            prev_search = s

        elif s["call"] == "ask_gallery":
            q, ans = args.get("question", ""), out.get("answer", "")
            if not self_contained(q or ""):
                add("ask", f"step {s['i']}: question not self-contained: {q!r}")
            if not ans or ans == FILL or len(ans) > MAX_ANSWER_CHARS:
                add("ask", f"step {s['i']}: missing or overlong answer")
            if not 1 <= out.get("count", 0) <= tool["pipeline"]["constraints"]["top_k"]:
                add("counts", f"step {s['i']}: ask count {out.get('count')} outside 1..top_k")

        elif s["call"] == "apply_effect":
            if args.get("effect") not in spec["config"]["effects"]:
                add("effect", f"step {s['i']}: effect {args.get('effect')!r} not offered in this episode")
            if src_count is not None and out["count"] != src_count:
                add("counts", f"step {s['i']}: effect output count != input")

        elif s["call"] == "make_collage":
            if src_count is not None and not 2 <= src_count <= cmax:
                add("counts", f"step {s['i']}: collage of {src_count} outside 2..{cmax}")
            if out["count"] != 1:
                add("counts", f"step {s['i']}: collage output must be 1 image")

        elif s["call"] == "move_to_album":
            album = args.get("album", "")
            existing = {a.lower() for a in persona["albums"]}
            if out["created"] and album.lower() in existing:
                add("album", f"step {s['i']}: new album {album!r} already exists")
            if not out["created"] and album not in persona["albums"]:
                add("album", f"step {s['i']}: {album!r} is not an existing album")
            if len(album) > MAX_ALBUM_CHARS:
                add("album", f"step {s['i']}: album name over {MAX_ALBUM_CHARS} characters")
            if out["album"] != album or (src_count is not None and out["count"] != src_count):
                add("counts", f"step {s['i']}: move outcome disagrees with args")

        elif s["call"] == "delete_images":
            if out["status"] == "cancelled":
                if out["count"] != 0:
                    add("counts", f"step {s['i']}: a cancelled delete deletes nothing")
            elif out["status"] != "deleted" or (src_count is not None and out["count"] != src_count):
                add("counts", f"step {s['i']}: a confirmed delete removes every image")
            elif args.get("images") in ledger:
                ledger[args["images"]]["alive"] = False

        if s.get("out"):
            ledger[s["out"]] = {"count": out.get("count", 1), "alive": True}

    # turns
    flat = [i for t in spec["turns"] for i in t]
    if flat != [s["i"] for s in spec["steps"]]:
        add("turns", f"turns {spec['turns']} do not cover the steps in order")
    starts = {t[0] for t in spec["turns"]}
    by_i = {s["i"]: s for s in spec["steps"]}
    for s in spec["steps"]:
        if "event" in s and s["i"] not in starts:
            add("turns", f"step {s['i']}: a selection must start a user turn")
        prev = by_i.get(s["i"] - 1, {})
        if s.get("call") == "delete_images" and prev.get("call") in ("make_collage", "apply_effect") \
                and s["i"] not in starts:
            add("turns", f"step {s['i']}: deleting something just created must start a user turn")
        if s.get("expect") == "ask" and not any(t[-1] == s["i"] for t in spec["turns"]):
            add("turns", f"step {s['i']}: a clarification question must end its turn")
    return v
