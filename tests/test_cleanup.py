import json

from realize.cleanup import deletion_only, split_events, text_items, user_turn_checks, check_edit

EP = next(json.loads(l) for l in open("data/episodes/episodes_r2.jsonl") if json.loads(l)["episode_id"] == "r2_0001")
PERSONA = json.load(open(f"data/personas/{EP['spec']['persona']}.json"))


def test_split_events_keeps_app_lines():
    ev, text = split_events("[user selected 3 photos → r0]\nHey, could you apply a filter to these?")
    assert ev == ["[user selected 3 photos → r0]"] and text == "Hey, could you apply a filter to these?"


def test_deletion_only():
    before = "Hey, could you apply a filter to these? I took them for my street photography."
    assert deletion_only(before, "Hey, could you apply a filter to these?")
    assert not deletion_only(before, "Hey, can you make these look vintage for my blog?")
    assert not deletion_only(before, "")


def test_checks_reject_losing_required_words():
    checks = user_turn_checks(EP, PERSONA)
    items = text_items(EP)
    k, role, text = next(i for i in items if i[1] == "user" and checks.get(i[0], ([],))[0])
    required = checks[k][0][0]
    assert check_edit(EP, PERSONA, checks, k, role, text, text) == []
    stripped = text.replace(required, "").strip() or "ok"
    assert check_edit(EP, PERSONA, checks, k, role, text, stripped)


def test_formatting_only_edit_is_not_a_change(monkeypatch):
    from realize import cleanup

    class FakeLLM:
        def json(self, prompt, schema, seed):
            items = cleanup.text_items(EP)
            msgs = [{"index": n, "text": t.replace(".", " .") + "  "} for n, (_, _, t) in enumerate(items, 1)]
            return type("R", (), {"output": {"messages": msgs}, "meta": {"served_model": "fake"}})()

    new = cleanup.clean_episode(FakeLLM(), EP, PERSONA)
    assert new["cleanup"]["changed_messages"] == [] and new["messages"] == EP["messages"]


def test_edit_cannot_drop_request_words_or_questions():
    from realize.cleanup import check_edit
    ep = next(json.loads(l) for l in open("data/episodes/episodes_v2.jsonl") if json.loads(l)["episode_id"] == "ep_0068")
    p = json.load(open(f"data/personas/{ep['spec']['persona']}.json"))
    k = 3                                                  # "Based on 3 photos from your renovation, ... Dulux."
    before = ep["messages"][k]["content"]
    assert any("renovation" in x for x in check_edit(ep, p, {}, k, "assistant", before,
                                                      before.replace(" from your renovation", "")))
    assert check_edit(ep, p, {}, k, "assistant", "Which album? Found 3.", "Found 3.")


def test_edit_cannot_remove_the_request_itself():
    from realize.cleanup import check_edit
    ep = next(json.loads(l) for l in open("data/episodes/episodes_v2.jsonl") if json.loads(l)["episode_id"] == "ep_0093")
    p = json.load(open(f"data/personas/{ep['spec']['persona']}.json"))
    before = ep["messages"][0]["content"]                   # "... photos of Barnaby? I'd like to delete them."
    assert any("delete" in x for x in check_edit(ep, p, {}, 0, "user", before, "Hey, can you show me photos of Barnaby?"))
