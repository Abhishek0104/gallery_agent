"""Interactive CLI without a model: a scripted backend drives realize.interactive.Session through the simulator."""
import json

import pytest

import realize.interactive as it
from llm import BadOutputError, ChatResult
from realize.system_prompt import GUIDANCE_START, system_prompt, tool_declarations

PERSONAS = it.load_personas()
PERSONA = PERSONAS["persona_08"]


def call(name, **args):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": "c", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


class Scripted:
    """Returns the queued assistant messages in order; records what it was sent."""
    name = "scripted"

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def chat(self, system, contents, tools, seed):
        self.seen.append((system, list(contents), tools))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return ChatResult(r, {"served_model": "scripted"})

    user_message = staticmethod(it.LocalBackend.user_message)
    tool_results = staticmethod(it.LocalBackend.tool_results)


def session(backend, selection=None, seed=5):
    return it.Session(backend, PERSONA, seed, selection, show=lambda *_: None)


def results(s):
    return [m["content"] for m in s.messages if m["role"] == "tool"]


def test_on_device_prompt_and_tools():
    b = Scripted(say("Hi!"))
    s = session(b)
    s.send("hello")
    system, _, tools = b.seen[0]
    assert system == system_prompt() and GUIDANCE_START not in system
    assert tools == tool_declarations(s.config)
    assert set(s.config) == {"collage_max", "effects"}


def test_search_select_delete_handles():
    b = Scripted(call("search_images", location="Goa"), say("Found them."),
                 call("delete_images", images="r2"), say("Deleted."))
    s = session(b)
    s.send("goa pics")
    n = results(s)[0]["count"]
    assert results(s)[0] == {"id": "r1", "count": n} and 1 <= n <= 150
    assert s.select(1) == "[user selected 1 photo → r2]"
    s.send("delete these")
    assert s.messages[-4]["content"] == "[user selected 1 photo → r2]\ndelete these"
    assert results(s)[1] == {"status": "deleted", "count": 1}
    assert not s.sim.ledger["r2"]["alive"] and s.sim.ledger["r1"]["alive"]
    with pytest.raises(ValueError):
        s.select(n + 1)                  # last alive set is r1


def test_initial_selection_and_counts_are_seeded_per_call():
    a, b = session(Scripted(call("search_images", query="x"), say(".")), selection=6), None
    a.send("find x")
    assert a.messages[0]["content"].startswith("[user selected 6 photos → r0]\n")
    b = session(Scripted(call("search_images", query="y"), say(".")), selection=6)
    b.send("find y")
    assert results(a)[0]["count"] == results(b)[0]["count"]      # same seed, same call index


def test_forced_outcomes():
    b = Scripted(call("search_images", query="x"), say("Nothing found."),
                 call("search_images", query="y"), say("."),
                 call("delete_images", images="r1"), say("Cancelled."))
    s = session(b)
    s.outcome("count=0")                 # a zero count is the registry's empty outcome, never a 0-photo handle
    s.send("find x")
    assert results(s)[0] == {"error": "no_results"} and s.sim.ledger == {}
    s.outcome("cancelled")               # not an outcome of search: stays pending until the delete
    s.send("find y")
    assert s.sim.force == {"names": ["cancelled"], "set": {}}
    s.send("delete them")
    assert results(s)[2] == {"status": "cancelled", "count": 0} and s.sim.ledger["r1"]["alive"]
    assert s.sim.force is None


def test_force_fields_and_album_created():
    b = Scripted(call("ask_gallery", question="Was Aisha in Leh?"), say("Yes."),
                 call("move_to_album", images="r1", album="leh trip 2024"), say("Moved."))
    s = session(b)
    s.outcome('answer="Yes, in March" count=2')
    s.send("was aisha in leh?")
    assert results(s)[0] == {"answer": "Yes, in March", "id": "r1", "count": 2}
    s.send("move them to my leh trip 2024 album")
    assert results(s)[1] == {"status": "moved", "count": 2, "album": "leh trip 2024", "created": False}
    assert it.parse_force("created=false answer=Yes") == {"names": [], "set": {"created": False, "answer": "Yes"}}


def test_collage_count_zero_refused_and_limit():
    tool = it.registry_tools()["make_collage"]
    assert it.apply_force(tool, {"status": "created", "count": 1}, it.parse_force("count=0"))[0] is None
    s = session(Scripted(call("search_images", query="x"), say("."), call("make_collage", images="r1"), say(".")))
    s.outcome("count=40")
    s.send("find x")
    s.send("collage")
    assert results(s)[1] == {"error": "too_many_images", "max": s.config["collage_max"]}


def test_bad_output_rolls_back_the_turn():
    b = Scripted(call("search_images", query="x"), BadOutputError("garbage"), call("search_images", query="x"), say("ok"))
    s = session(b)
    with pytest.raises(BadOutputError):
        s.send("find x")
    assert s.messages == [] and s.sim.ledger == {} and s.sim.calls == 0 and s.script == []
    s.send("find x")
    assert results(s) == [{"id": "r1", "count": results(s)[0]["count"]}] and len(s.script) == 1


def test_save_and_replay_reports_differences():
    s = session(Scripted(call("search_images", location="Goa"), say("Found."),
                         call("make_collage", images="r2"), say("Done.")))
    s.send("goa pics")
    s.select(2)
    s.outcome("count=1")
    s.send("collage of these")
    rec = json.loads(json.dumps(s.record("collage check")))
    assert rec["interactive"]["script"][1:3] == [{"select": 2}, {"outcome": "count=1"}]
    assert rec["system_prompt"] == system_prompt() and rec["teacher_guidance_version"] is None

    shown = []
    same = Scripted(call("search_images", location="Goa"), say("Found."), call("make_collage", images="r2"), say("Done."))
    other = Scripted(call("search_images", location="goa"), say("Found."), say("Which photos?"))
    (r1,) = it.replay(same, [rec], show=shown.append)
    assert shown[-1] == "--- calls identical in 2/2 message(s)"
    assert r1["messages"] == rec["messages"] and r1["interactive"]["replay_of"] == rec["episode_id"]
    it.replay(other, [rec], show=shown.append)
    assert shown[-1] == "--- calls identical in 0/2 message(s)"
    assert any(x.startswith("  ≠ was: make_collage") for x in shown)


def test_parse_local_output():
    tools = [{"type": "function", "function": t} for t in tool_declarations({"collage_max": 6, "effects": ["warm"]})]
    text = ("<tool_call>\n<function=search_images>\n<parameter=people>\n[\"Aisha\"]\n</parameter>\n"
            "<parameter=location>\nGoa\n</parameter>\n</function>\n</tool_call><|im_end|>")
    msg = it.parse_output(text, tools, 1)
    assert ChatResult(msg, {}).calls == [("search_images", {"people": ["Aisha"], "location": "Goa"}, "call_1")]
    assert it.parse_output("Sure!<|im_end|>", tools, 2) == {"role": "assistant", "content": "Sure!"}
    with pytest.raises(BadOutputError):
        it.parse_output("Sure, let me", tools, 3)                 # truncated
    hf = it.hf_messages("sys", [{"role": "user", "content": "hi"}, msg,
                               *it.LocalBackend.tool_results([("search_images", "call_1", {"id": "r1", "count": 3})])])
    assert hf[2]["tool_calls"][0]["function"]["arguments"] == {"people": ["Aisha"], "location": "Goa"}
    assert hf[3] == {"role": "tool", "name": "search_images", "content": '{"id": "r1", "count": 3}'}


def test_malformed_calls_roll_back_and_are_kept():
    b = Scripted(call("search_images", query="x"), say("."),
                 call("delete_images", images=["r1"]),          # a list, not a handle
                 call("move_to_album", images="r1"),             # required album missing
                 call("move_to_album", images="r1", album="Goa"), say("Moved."))
    s = session(b)
    s.send("find x")
    before = (json.dumps(s.sim.ledger), len(s.messages), len(s.script))
    for text in ("delete them", "move them"):
        with pytest.raises(Exception):
            s.send(text)
        assert (json.dumps(s.sim.ledger), len(s.messages), len(s.script)) == before
    assert [e["say"] for e in s.errors] == ["delete them", "move them"]
    assert s.errors[1]["calls"] == [{"name": "move_to_album", "args": {"images": "r1"}}]
    s.send("move them to Goa")
    assert s.record()["interactive"]["errors"] == s.errors


def test_replay_uses_the_saved_config(monkeypatch):
    s = session(Scripted(call("search_images", query="x"), say(".")))
    s.send("find x")
    rec = json.loads(json.dumps(s.record()))
    rec["spec"]["config"] = {"collage_max": 12, "effects": ["sepia"]}
    b = Scripted(call("search_images", query="x"), say("."))
    (r,) = it.replay(b, [rec], show=lambda *_: None)
    assert r["spec"]["config"] == rec["spec"]["config"] and b.seen[0][2] == tool_declarations(rec["spec"]["config"])


class FakeOpenAI:
    """/v1/models + chat.completions.create, recording requests."""

    def __init__(self, served):
        self.served, self.requests = served, []
        self.chat = self.completions = self.models = self

    def list(self):
        return type("L", (), {"data": [type("M", (), {"id": m})() for m in self.served]})()

    def create(self, **kw):
        from tests.test_student_eval import reply
        self.requests.append(kw)
        return reply(content="Hello!")


def vllm(monkeypatch, tmp_path, served, want=None):
    import yaml
    import llm as llm_mod

    cfg = yaml.safe_load(open("config/llm.yaml"))
    cfg["cache_dir"] = str(tmp_path)
    (tmp_path / "llm.yaml").write_text(yaml.safe_dump(cfg))
    fake = FakeOpenAI(served)

    def make(role):
        x = llm_mod.LLM(role, config_path=tmp_path / "llm.yaml")
        x._client = fake
        return x
    monkeypatch.setattr(it, "LLM", make)
    return it.VLLMBackend("http://gpu:8000", want), fake


def test_vllm_backend_picks_model_and_never_caches(monkeypatch, tmp_path):
    b, fake = vllm(monkeypatch, tmp_path, ["Qwen/Qwen3.5-0.8B", "gallery-lora"])
    assert b.llm.model == "gallery-lora" and b.llm.spec["base_url"] == "http://gpu:8000/v1"
    s = session(b)
    s.send("hi")
    s.send("hi")                                                   # same request twice: both reach the server
    assert len(fake.requests) == 2 and fake.requests[0]["model"] == "gallery-lora"
    assert fake.requests[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert fake.requests[0]["temperature"] == 0
    assert not (tmp_path / "chat").exists()
    with pytest.raises(SystemExit):
        vllm(monkeypatch, tmp_path, ["a", "b"])                    # configured name not served, ambiguous
    assert vllm(monkeypatch, tmp_path, ["only-one"])[0].llm.model == "only-one"
