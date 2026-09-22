"""
Backstory cleanup for existing episodes: remove reasons / backstory from user messages and any echo of it in
assistant replies, changing nothing else. One LLM call per episode (all text messages together, so echoes are
removed consistently), then code checks every edit.

    python -m realize.cleanup --tag v2      # episodes_v2.jsonl -> episodes_v2c.jsonl (+ cleanup report)

Code guarantees: app event lines ("[user selected N photos → rK]"), tool calls and tool results are untouched;
an edit may only remove words (≥ 90% of the edited words already in the original, never longer); the turn's
required phrases stay and forbidden / extra people, places and dates stay out (the user-simulator checks);
assistant numbers are unchanged. An edit that fails is retried with feedback, then the original is kept.
"""
import argparse
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Template
from typing import List

from pydantic import BaseModel

from llm import LLM, BadOutputError
from registry import tools
from realize.user_sim import check_message, search_args_in_turn, turn_intent

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "episodes"
PROMPT = Template((ROOT / "realize" / "prompts" / "cleanup.txt").read_text())
EVENT = re.compile(r"^\[user selected \d+ photos? → r\d+\]$")
WORD = re.compile(r"[a-z0-9']+")
NUMBER = re.compile(r"\b\d[\d,.:/-]*\b")
MAX_ATTEMPTS = 3
VERSION = 1


class Edited(BaseModel):
    index: int
    text: str


class Cleaned(BaseModel):
    messages: List[Edited]


def split_events(content):
    """User message -> (app event lines, typed text)."""
    lines = content.split("\n")
    events = [l for l in lines if EVENT.match(l.strip())]
    return events, "\n".join(l for l in lines if not EVENT.match(l.strip())).strip()


def deletion_only(before, after):
    """True if `after` is `before` with words removed (a few connective/punctuation fixes allowed)."""
    b, a = WORD.findall(before.lower()), WORD.findall(after.lower())
    if not a or len(a) > len(b):
        return False
    pool = Counter(b)
    kept = sum(min(c, pool[w]) for w, c in Counter(a).items())
    return kept / len(a) >= 0.9


def text_items(ep):
    """[(message index, role, text)] for user typed text and assistant replies."""
    items = []
    for k, m in enumerate(ep["messages"]):
        if m["role"] == "user":
            items.append((k, "user", split_events(m["content"])[1]))
        elif m["role"] == "assistant" and m["content"]:
            items.append((k, "assistant", m["content"]))
    return items


def user_turn_checks(ep, persona):
    """message index -> (required, forbidden, searches) for user messages that map 1:1 onto spec turns."""
    spec = ep["spec"]
    users = [k for k, m in enumerate(ep["messages"]) if m["role"] == "user"]
    if len(users) != len(spec["turns"]):
        return {}
    out = {}
    for k, turn in zip(users, spec["turns"]):
        _, required, forbidden = turn_intent(spec, turn, ep["surface"])
        out[k] = (required, forbidden, search_args_in_turn(spec, turn))
    return out


STOP = set("the a an and or of to in on at for from with my our your his her their this that these those it is are was "
           "were be been can could would will just some any all me you we they i".split())


def grounding_words(ep, k):
    """Content words of the tool calls and results in the turn this message belongs to: the request's substance."""
    msgs = ep["messages"]
    start = max(i for i in range(k + 1) if msgs[i]["role"] == "user")
    end = next((i for i in range(k + 1, len(msgs)) if msgs[i]["role"] == "user"), len(msgs))
    blob = []
    for m in msgs[start:end]:
        for c in m.get("tool_calls") or []:
            blob.append(json.dumps(c["args"], ensure_ascii=False))
        if m["role"] == "tool":
            blob.append(json.dumps(m["content"], ensure_ascii=False))
    return {w for w in WORD.findall(" ".join(blob).lower()) if len(w) > 2 and w not in STOP}


# words that ask for each tool (registry conversation.request_words); if the original message asks for a tool the
# turn calls, the edit must keep asking
TOOL_WORDS = {name: set(t.conversation.get("request_words", [])) for name, t in tools().items()}


def turn_tools(ep, k):
    """Tools this user message asks for: the calls made in its turn, plus what the spec says the user requested
    there (a no_results turn asks for steps that are then skipped)."""
    msgs = ep["messages"]
    end = next((i for i in range(k + 1, len(msgs)) if msgs[i]["role"] == "user"), len(msgs))
    tools = {c["name"] for m in msgs[k:end] for c in (m.get("tool_calls") or [])}
    spec = ep["spec"]
    users = [i for i, m in enumerate(msgs) if m["role"] == "user"]
    if k in users and len(users) == len(spec["turns"]):
        by_i = {s["i"]: s for s in spec["steps"]}
        n = users.index(k)
        asked = spec["requests"][n] if "requests" in spec else spec["turns"][n]
        tools |= {by_i[i]["call"] for i in asked if "call" in by_i[i]}
    return tools


def check_edit(ep, persona, checks, k, role, before, after):
    v = []
    if not after.strip():
        return ["message became empty"]
    if after != before and not deletion_only(before, after):
        v.append("only delete words; don't add or rephrase")
    lost = sorted((grounding_words(ep, k) & set(WORD.findall(before.lower()))) - set(WORD.findall(after.lower())))
    if lost:
        v.append(f"keep the words the request depends on: {lost}")
    if role == "assistant" and after.count("?") < before.count("?"):
        v.append("keep the assistant's questions")
    if role == "user":
        bw, aw = set(WORD.findall(before.lower())), set(WORD.findall(after.lower()))
        for tool in turn_tools(ep, k):
            words = TOOL_WORDS.get(tool, set())
            if bw & words and not aw & words:
                v.append(f"keep the part that asks for {tool.replace('_', ' ')} (it is a request, not backstory)")
    if role == "user" and k in checks:                   # only violations the edit introduces
        required, forbidden, searches = checks[k]
        already = set(check_message(before, required, searches, persona, forbidden))
        v += [x for x in check_message(after, required, searches, persona, forbidden) if x not in already]
    if role == "assistant" and sorted(NUMBER.findall(before)) != sorted(NUMBER.findall(after)):
        v.append("keep every number in assistant messages")
    return v


def clean_episode(llm, ep, persona):
    items = text_items(ep)
    checks = user_turn_checks(ep, persona)
    listing = "\n".join(f"[{n}] {role.upper()}: {text}" for n, (_, role, text) in enumerate(items, 1))
    feedback, edits, meta, echo_rejected = "", {}, None, False
    for attempt in range(MAX_ATTEMPTS):
        res = llm.json(PROMPT.substitute(messages=listing, feedback=feedback), Cleaned,
                       seed=f"cleanup/{ep['episode_id']}/a{attempt}")
        meta = res.meta
        got = {e["index"]: e["text"].strip() for e in res.output["messages"]}
        problems = []
        for n, (k, role, text) in enumerate(items, 1):
            if k in edits:
                continue                                     # accepted in an earlier attempt
            after = got.get(n, text)
            if WORD.findall(after.lower()) == WORD.findall(text.lower()):
                after = text                                 # whitespace / punctuation only: not an edit
            v = check_edit(ep, persona, checks, k, role, text, after)
            if v:
                problems.append(f"message [{n}]: " + "; ".join(v))
                echo_rejected |= role == "assistant"
            else:
                edits[k] = after
        if not problems:
            break
        feedback = "\n## Your previous edit was rejected — fix these (or return the message unchanged)\n" + \
                   "\n".join(f"- {p}" for p in problems)
    failed = [k for k, _, _ in items if k not in edits]
    if echo_rejected:
        # the assistant echoes backstory that can't be removed safely (e.g. it is the question it asks):
        # editing only the user side would leave the reply referring to something never said
        new = json.loads(json.dumps(ep))
        new["cleanup"] = {"version": VERSION, "changed_messages": [], "backstory_remains": True,
                          "reason": "assistant echo could not be removed without breaking the reply",
                          "served_model": meta and meta["served_model"]}
        return new

    new = json.loads(json.dumps(ep))
    changed = []
    for k, role, text in items:
        after = edits.get(k, text)
        if after == text:
            continue
        changed.append(k)
        if role == "user":
            events, _ = split_events(ep["messages"][k]["content"])
            new["messages"][k]["content"] = "\n".join(events + [after])
        else:
            new["messages"][k]["content"] = after
    new["cleanup"] = {"version": VERSION, "changed_messages": changed, "kept_original": failed,
                      "served_model": meta and meta["served_model"]}
    return new


def verifier_regressions(before, after, persona, embed):
    """Checks (hard or soft) that pass on the original episode but fail after cleanup."""
    from verify.verifier import verify

    passed = {c["check"] for c in verify(before, persona, embed)["checks"] if c["pass"]}
    return sorted(c["check"] for c in verify(after, persona, embed)["checks"] if not c["pass"] and c["check"] in passed)


def main(tag):
    episodes = [json.loads(l) for l in (OUT / f"episodes_{tag}.jsonl").read_text().splitlines()]
    personas = {json.loads(f.read_text())["persona_id"]: json.loads(f.read_text())
                for f in (ROOT / "data" / "personas").glob("persona_*.json")}
    llm, emb = LLM("cleanup"), LLM("embedding")

    def one(ep):
        persona = personas[ep["spec"]["persona"]]
        try:
            new = clean_episode(llm, ep, persona)
        except BadOutputError as e:                          # keep the original episode, record why
            return {**ep, "cleanup": {"version": VERSION, "changed_messages": [], "error": str(e)}}
        if new["cleanup"]["changed_messages"]:
            worse = verifier_regressions(ep, new, persona, emb.embed)
            if worse:                                        # any check that passed before must still pass
                return {**ep, "cleanup": {"version": VERSION, "changed_messages": [], "reverted": worse,
                                          "served_model": new["cleanup"]["served_model"]}}
        return new

    with ThreadPoolExecutor(max_workers=4) as pool:
        cleaned = list(pool.map(one, episodes))
    out_tag = f"{tag}c"
    (OUT / f"episodes_{out_tag}.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in cleaned))
    changed = [e for e in cleaned if e["cleanup"]["changed_messages"]]
    roles = Counter(e["messages"][k]["role"] for e in changed for k in e["cleanup"]["changed_messages"])
    kept = sum(len(e["cleanup"].get("kept_original", [])) for e in cleaned)
    errors = [e["episode_id"] for e in cleaned if "error" in e["cleanup"]]
    reverted = {e["episode_id"]: e["cleanup"]["reverted"] for e in cleaned if e["cleanup"].get("reverted")}
    remains = [e["episode_id"] for e in cleaned if e["cleanup"].get("backstory_remains")]
    # rerun the user-message checks on every mapped user message, before and after
    def violations(eps):
        n = 0
        for e in eps:
            p = personas[e["spec"]["persona"]]
            for k, (req, forb, searches) in user_turn_checks(e, p).items():
                n += bool(check_message(split_events(e["messages"][k]["content"])[1], req, searches, p, forb))
        return n
    report = {"tag": tag, "out_tag": out_tag, "episodes": len(cleaned), "episodes_changed": len(changed),
              "messages_changed": dict(roles), "edits_rejected_kept_original": kept, "llm_errors": errors,
              "reverted_by_verifier": reverted, "backstory_remains": remains,
              "user_messages_failing_checks": {"before": violations(episodes), "after": violations(cleaned)}}
    (OUT / f"_cleanup_{tag}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    main(ap.parse_args().tag)
