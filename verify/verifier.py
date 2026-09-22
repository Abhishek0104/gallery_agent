"""
Verifier (code): score a realized episode against its spec. docs/verifier_design.md.

    verify(episode, persona, embed) -> {"score", "accept", "groups": {...}, "checks": [...], "failures": [...]}

`embed(texts) -> unit vectors` is injected (the embedding LLM role in production, a stub in tests).
Every check yields a score in [0, 1]; hard checks must be 1 (or pass their threshold) for accept.
"""
import difflib
import json
import re
from pathlib import Path

import yaml

from sim.simulator import Simulator
from specs.query_purity import check_query
from registry import tools
from registry.args import ARG_TYPES, exact_args, normalized, semantic_args
from specs.spec_validator import self_contained

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "verify.yaml").read_text())
SELECTION = re.compile(r"\[user selected \d+ photos? → (r\d+)\]")
HANDLE = re.compile(r"\br\d+\b")
NUMBER = re.compile(r"\b\d[\d,.:/-]*\b")
CODE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9-]{4,}\b|\b\d[\d,.:/-]*\b")
DELETED = re.compile(r"\b(delet\w*|recycle|removed|trash)", re.I)
NOTHING = re.compile(r"\b(no photos|no results|nothing|couldn't find|could not find|didn't find|none|no matching|"
                     r"not find|wasn't able|no images)\b", re.I)
LOOSEN = re.compile(r"\b(try|without|remov\w*|broad\w*|widen|different|another|drop\w*|loosen\w*|instead)\b", re.I)
KEPT = re.compile(r"\b(cancel\w*|not deleted|nothing was deleted|nothing deleted|kept|still (there|in)|no photos were)\b", re.I)
PICK = re.compile(r"\b(select|choose|pick)\b", re.I)
TOOLS = tools()


# ---------------------------------------------------------------- replay
def replay(ep):
    """Rebuild the simulator from the messages: selection lines + tool calls, in order."""
    sim = Simulator(ep["spec"])
    selects = iter([s for s in ep["spec"]["steps"] if "event" in s])
    for m in ep["messages"]:
        if m["role"] == "user":
            for h in SELECTION.findall(m["content"] or ""):
                if h != "r0":
                    sim.select(next(selects))
        elif m["role"] == "assistant":
            for c in m["tool_calls"] or []:
                sim.call(c["name"], c["args"])
    return sim


def turns(messages):
    """Split messages into user turns: [user message(s) + everything until the next user message]."""
    out = []
    for m in messages:
        if m["role"] == "user" or not out:
            out.append([])
        out[-1].append(m)
    return out


def norm_arg(key, v):
    """A value normalized for exact comparison (config/arg_types.yaml compare.normalize)."""
    return normalized(key, v)


# ---------------------------------------------------------------- verify
def verify(ep, persona, embed):
    spec = ep["spec"]
    checks, failures = [], []

    def check(group, name, score, hard=True, msg=None, threshold=1.0):
        ok = score >= threshold - 1e-9
        checks.append({"group": group, "check": name, "score": round(float(score), 3), "hard": hard, "pass": ok})
        if not ok and msg:
            failures.append(f"{name}: {msg}")

    planned = [s for s in spec["steps"] if "call" in s and not s.get("skipped")]
    actual = [c for m in ep["messages"] if m["role"] == "assistant" for c in (m["tool_calls"] or [])]
    sim = replay(ep)

    # calls: align tool names in order
    sm = difflib.SequenceMatcher(None, [p["call"] for p in planned], [a["name"] for a in actual], autojunk=False)
    pairs = [(planned[i + k], actual[j + k]) for i, j, n in sm.get_matching_blocks() for k in range(n)]
    denom = max(len(planned), len(actual)) or 1
    check("calls", "call_sequence", len(pairs) / denom,
          msg=f"planned {[p['call'] for p in planned]} vs actual {[a['name'] for a in actual]}")

    # args: handles + exact args
    for p, a in pairs:
        pa, aa = p["args"], a["args"]
        handle_arg = TOOLS[p["call"]].io.consumes if p["call"] in TOOLS else None
        if handle_arg and handle_arg in pa:
            want = sim.map.get(pa[handle_arg])
            check("args", f"handle:{p['i']}", float(aa.get(handle_arg) == want),
                  msg=f"step {p['i']} {p['call']} {handle_arg}={aa.get(handle_arg)!r}, expected {want!r}")
        for key in exact_args():
            if key in pa or key in aa:
                ok = norm_arg(key, pa.get(key)) == norm_arg(key, aa.get(key))
                check("args", f"{key}:{p['i']}", float(ok),
                      msg=f"step {p['i']} {key}: spec={pa.get(key)!r} teacher={aa.get(key)!r}")
        if TOOLS[p["call"]].raw["pipeline"].get("sample") == "slot_filters":    # search: no extra filters
            extra = [k for k in aa if k not in pa]
            check("args", f"no_extra_slots:{p['i']}", float(not extra),
                  msg=f"step {p['i']} extra search args {extra}")

    # free-text arguments (query, question): embedding similarity + their own checks (config/arg_types.yaml)
    semantic = [(arg, [(p, a) for p, a in pairs if arg in p["args"] or arg in a["args"]]) for arg in semantic_args()]
    to_embed = [(p["args"].get(arg), a["args"].get(arg)) for arg, ps in semantic for p, a in ps]
    texts = [x for sp, tp in to_embed if sp and tp for x in (sp, tp)]      # embed only non-empty pairs
    vecs = iter(embed(texts)) if texts else iter(())
    cosines = iter([float(next(vecs) @ next(vecs)) if sp and tp else 0.0 for sp, tp in to_embed])
    for arg, ps in semantic:
        t = ARG_TYPES[arg]
        group, thr = t.get("group", arg), t["compare"]["semantic"]
        for p, a in ps:
            sv, tv, cos = p["args"].get(arg), a["args"].get(arg), next(cosines)
            if t["compare"].get("missing_fails") and (not sv or not tv):
                check(group, f"{arg}:{p['i']}", 0.0, msg=f"step {p['i']} {arg} spec={sv!r} teacher={tv!r}")
                continue
            tv = tv if tv is not None else ""
            check(group, f"{arg}_similarity:{p['i']}", cos, threshold=thr,
                  msg=f"step {p['i']} {sv!r} vs {tv!r} (cos {cos:.2f})")
            if t.get("purity"):
                impure = check_query(tv, persona, people="people" in a["args"])
                check(group, f"{arg}_purity:{p['i']}", float(not impure),
                      msg=f"step {p['i']} teacher {arg} {tv!r}: {[m for _, m in impure]}")
            if t.get("self_contained"):
                check(group, f"{arg}_self_contained:{p['i']}", float(self_contained(tv)),
                      msg=f"step {p['i']} {arg} not self-contained: {tv!r}")

    # replies
    # tool results and user messages so far (numbers can be recalled), plus the limits in the tool descriptions
    seen = f"2 {spec['config']['collage_max']} "
    for t_no, turn in enumerate(turns(ep["messages"]), 1):
        texts_ = [m["content"] for m in turn if m["role"] == "assistant" and m["content"]]
        final = turn[-1]
        check("replies", f"reply:{t_no}", float(final["role"] == "assistant" and bool(final["content"])),
              msg=f"turn {t_no} does not end with a text reply")
        leaked = [h for t in texts_ for h in HANDLE.findall(t)]
        check("replies", f"no_ids:{t_no}", float(not leaked), msg=f"turn {t_no} reply shows ids {leaked}")
        seen += " ".join(json.dumps(m["content"], ensure_ascii=False) for m in turn if m["role"] in ("tool", "user"))
        known = seen
        nums = [x for t in texts_ for x in NUMBER.findall(t)]
        ungrounded = [x for x in nums if x not in known]
        check("replies", f"grounded_numbers:{t_no}", 1 - len(ungrounded) / len(nums) if nums else 1.0, hard=False,
              msg=f"turn {t_no} numbers not in tool results: {ungrounded}")
        if any(m["role"] == "tool" and m["name"] == "delete_images" for m in turn):
            check("replies", f"says_deleted:{t_no}", float(any(DELETED.search(t) for t in texts_)), hard=False,
                  msg=f"turn {t_no} reply doesn't say what was deleted")
        for k, m in enumerate(turn):
            if m["role"] == "tool" and m["name"] == "ask_gallery":
                answer = m["content"].get("answer", "")
                later = " ".join(x["content"] for x in turn[k + 1:] if x["role"] == "assistant" and x["content"])
                tokens = CODE.findall(answer)
                hit = [t for t in tokens if t in later]
                check("replies", f"answer_relayed:{t_no}", len(hit) / len(tokens) if tokens else float(bool(later)),
                      msg=f"turn {t_no} answer tokens {tokens} missing from reply")

    # round 2: turns where the assistant must not call, and what it must say
    conv = turns(ep["messages"])
    by_i = {s["i"]: s for s in spec["steps"]}
    special = [(k, by_i[t[-1]]) for k, t in enumerate(spec["turns"]) if "expect" in by_i[t[-1]]]
    cancelled = [(k, s) for k, t in enumerate(spec["turns"]) for s in (by_i[i] for i in t)
                 if s.get("call") == "delete_images" and s["outcome"].get("status") == "cancelled"]
    if special or cancelled:
        same = len(conv) == len(spec["turns"])
        check("round2", "turn_structure", float(same), msg=f"{len(conv)} conversation turns vs {len(spec['turns'])} planned")
    if special and len(conv) == len(spec["turns"]):
        effects_offered = [e.replace("_", " ") for e in spec["config"]["effects"]]
        for k, s in special:
            turn = conv[k]
            calls_ = [c for m in turn if m["role"] == "assistant" for c in (m["tool_calls"] or [])]
            reply = " ".join(m["content"] for m in turn if m["role"] == "assistant" and m["content"])
            if s["expect"] == "ask":
                # the turn may run earlier planned calls (search, effect, ...); nothing beyond them before asking
                planned_here = [by_i[i]["call"] for i in spec["turns"][k]
                                if "call" in by_i[i] and not by_i[i].get("skipped")]
                got = [c["name"] for c in calls_]
                check("round2", f"no_call_when_asking:{k + 1}", float(got == planned_here),
                      msg=f"turn {k + 1} should run {planned_here} then ask about {s['about']}; ran {got}")
                asks = "?" in reply
                if s["about"] == "album":
                    ok = asks and "album" in reply.lower()
                elif s["about"] == "effect":
                    ok = asks and all(e in reply.lower().replace("-", " ").replace("&", "and") for e in effects_offered)
                else:
                    ok = bool(PICK.search(reply)) and str(s["max"]) in reply
                check("round2", f"asks_right_thing:{k + 1}", float(ok), hard=False,
                      msg=f"turn {k + 1} {s['about']} question: {reply[:120]!r}")
            else:                                       # report after no_results: nothing after the failed search
                last_search = [n for n, c in enumerate(calls_) if c["name"] == "search_images"]
                after = calls_[last_search[-1] + 1:] if last_search else calls_
                check("round2", f"stops_after_no_results:{k + 1}", float(not after),
                      msg=f"turn {k + 1} kept going after no results: {[c['name'] for c in after]}")
                ok = bool(NOTHING.search(reply)) and bool(LOOSEN.search(reply))
                check("round2", f"reports_no_results:{k + 1}", float(ok), hard=False,
                      msg=f"turn {k + 1} no_results reply: {reply[:120]!r}")
    if cancelled and len(conv) == len(spec["turns"]):
        for k, s in cancelled:
            reply = " ".join(m["content"] for m in conv[k] if m["role"] == "assistant" and m["content"])
            check("round2", f"acknowledges_cancel:{k + 1}", float(bool(KEPT.search(reply))), hard=False,
                  msg=f"turn {k + 1} cancelled reply: {reply[:120]!r}")

    # realization flags (recomputed by the replay)
    flags = sorted(set(sim.flags) | set(ep.get("flags", [])) | ({"incomplete"} if not sim.done() else set()))
    check("flags", "no_flags", float(not flags), msg=f"flags {flags}")

    groups = {}
    for c in checks:
        groups.setdefault(c["group"], []).append(c["score"])
    group_scores = {g: sum(v) / len(v) for g, v in groups.items()}
    w = {g: CFG["weights"][g] for g in group_scores}
    score = sum(group_scores[g] * w[g] for g in w) / sum(w.values())
    accept = all(c["pass"] for c in checks if c["hard"])
    return {"episode_id": ep["episode_id"], "score": round(score, 3), "accept": accept,
            "groups": {g: round(s, 3) for g, s in group_scores.items()}, "checks": checks, "failures": failures}
