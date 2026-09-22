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
from specs.spec_validator import date_core, normalize_person, self_contained

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "verify.yaml").read_text())
SELECTION = re.compile(r"\[user selected \d+ photos? → (r\d+)\]")
HANDLE = re.compile(r"\br\d+\b")
NUMBER = re.compile(r"\b\d[\d,.:/-]*\b")
CODE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9-]{4,}\b|\b\d[\d,.:/-]*\b")
DELETED = re.compile(r"\b(delet\w*|recycle|removed|trash)", re.I)
EXACT_ARGS = ("people", "location", "date", "effect", "album")


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
    if key == "people" and isinstance(v, list):
        return sorted(normalize_person(x).lower() for x in v)
    if key == "date":
        return (date_core(v) or "").lower()
    if isinstance(v, str):
        return v.strip().lower()
    return v


# ---------------------------------------------------------------- verify
def verify(ep, persona, embed):
    spec = ep["spec"]
    checks, failures = [], []

    def check(group, name, score, hard=True, msg=None, threshold=1.0):
        ok = score >= threshold - 1e-9
        checks.append({"group": group, "check": name, "score": round(float(score), 3), "hard": hard, "pass": ok})
        if not ok and msg:
            failures.append(f"{name}: {msg}")

    planned = [s for s in spec["steps"] if "call" in s]
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
        if "images" in pa:
            want = sim.map.get(pa["images"])
            check("args", f"handle:{p['i']}", float(aa.get("images") == want),
                  msg=f"step {p['i']} {p['call']} images={aa.get('images')!r}, expected {want!r}")
        for key in EXACT_ARGS:
            if key in pa or key in aa:
                ok = norm_arg(key, pa.get(key)) == norm_arg(key, aa.get(key))
                check("args", f"{key}:{p['i']}", float(ok),
                      msg=f"step {p['i']} {key}: spec={pa.get(key)!r} teacher={aa.get(key)!r}")
        if p["call"] == "search_images":
            extra = [k for k in aa if k not in pa]
            check("args", f"no_extra_slots:{p['i']}", float(not extra),
                  msg=f"step {p['i']} extra search args {extra}")

    # query + question similarity
    q_pairs = [(p, a) for p, a in pairs if "query" in p["args"] or "query" in a["args"]]
    k_pairs = [(p, a) for p, a in pairs if p["call"] == "ask_gallery"]
    # embed only non-empty pairs; a missing side scores 0
    to_embed = [(p["args"].get("query"), a["args"].get("query")) for p, a in q_pairs]
    to_embed += [(p["args"]["question"], a["args"].get("question")) for p, a in k_pairs]
    texts = [x for sp, tp in to_embed if sp and tp for x in (sp, tp)]
    vecs = iter(embed(texts)) if texts else iter(())
    cosines = [float(next(vecs) @ next(vecs)) if sp and tp else 0.0 for sp, tp in to_embed]
    for n, (p, a) in enumerate(q_pairs):
        sq, tq = p["args"].get("query"), a["args"].get("query")
        if not sq or not tq:
            check("query", f"query:{p['i']}", 0.0, msg=f"step {p['i']} query spec={sq!r} teacher={tq!r}")
            continue
        cos = cosines[n]
        check("query", f"query_similarity:{p['i']}", cos, threshold=CFG["query_threshold"],
              msg=f"step {p['i']} {sq!r} vs {tq!r} (cos {cos:.2f})")
        impure = check_query(tq, persona, people="people" in a["args"])
        check("query", f"query_purity:{p['i']}", float(not impure),
              msg=f"step {p['i']} teacher query {tq!r}: {[m for _, m in impure]}")
    for n, (p, a) in enumerate(k_pairs):
        tq = a["args"].get("question", "")
        cos = cosines[len(q_pairs) + n]
        check("ask", f"question_similarity:{p['i']}", cos, threshold=CFG["question_threshold"],
              msg=f"step {p['i']} {p['args']['question']!r} vs {tq!r} (cos {cos:.2f})")
        check("ask", f"question_self_contained:{p['i']}", float(self_contained(tq)),
              msg=f"step {p['i']} question not self-contained: {tq!r}")

    # replies
    seen = ""                                  # tool results and user messages so far (numbers can be recalled)
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
