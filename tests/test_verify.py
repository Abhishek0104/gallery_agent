import copy
import json

import numpy as np

from verify.verifier import verify

EPISODES = [json.loads(l) for l in open("data/episodes/episodes_v0.jsonl")]
PERSONAS = {}
for ep in EPISODES:
    PERSONAS[ep["spec"]["persona"]] = json.load(open(f"data/personas/{ep['spec']['persona']}.json"))


def fake_embed(texts):
    """Identical texts -> cosine 1; different texts -> 0."""
    uniq = sorted(set(texts))
    v = np.zeros((len(texts), len(uniq)), dtype=np.float32)
    for i, t in enumerate(texts):
        v[i, uniq.index(t)] = 1.0
    return v


def ep_by(eid):
    return copy.deepcopy(next(e for e in EPISODES if e["episode_id"] == eid))


def run(ep):
    return verify(ep, PERSONAS[ep["spec"]["persona"]], fake_embed)


def calls(ep):
    return [c for m in ep["messages"] if m["role"] == "assistant" for c in (m["tool_calls"] or [])]


def test_clean_episode_accepted():
    v = run(ep_by("ep_0047"))          # no query/question, all calls match
    assert v["accept"] and v["score"] == 1.0


def test_wrong_handle_rejected():
    ep = ep_by("ep_0047")
    calls(ep)[1]["args"]["images"] = "r9"
    v = run(ep)
    assert not v["accept"] and any(f.startswith("handle") for f in v["failures"])


def test_people_alias_ok_but_possessive_not():
    ep = ep_by("ep_0030")
    calls(ep)[0]["args"]["people"] = ["hubby"]           # alias of husband: fine
    assert not any(f.startswith("people") for f in run(ep)["failures"])
    calls(ep)[0]["args"]["people"] = ["my husband"]      # not a relation word
    assert any(f.startswith("people") for f in run(ep)["failures"])


def test_missing_call_scores_partially():
    ep = ep_by("ep_0047")
    last = [m for m in ep["messages"] if m["role"] == "assistant" and m["tool_calls"]][-1]
    last["tool_calls"] = last["tool_calls"][:-1]
    v = run(ep)
    assert not v["accept"] and 0 < v["groups"]["calls"] < 1


def test_answer_must_be_relayed():
    ep = ep_by("ep_0042")
    ep["messages"][-1]["content"] = "You went to Bali last year."
    v = run(ep)
    assert any(f.startswith("answer_relayed") for f in v["failures"])


def test_ids_in_reply_rejected():
    ep = ep_by("ep_0047")
    ep["messages"][-1]["content"] += " (saved as r3)"
    assert not run(ep)["accept"]


def test_invented_number_flagged():
    ep = ep_by("ep_0047")
    ep["messages"][-1]["content"] = "I found 12 photos of you in Miami and made a collage."
    v = run(ep)
    assert any(f.startswith("grounded_numbers") for f in v["failures"]) and v["accept"]   # soft check
