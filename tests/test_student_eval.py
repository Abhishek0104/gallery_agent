"""End-to-end eval path without a server: a scripted 'student' behind the openai_compatible provider plays the
spec's planned calls through realize.run, and the verifier accepts the result."""
import json
import random

import yaml
from openai.types.chat import ChatCompletion

import llm as llm_mod
import realize.run as run
from realize.system_prompt import GUIDANCE_START
from tests.test_verify import fake_embed
from verify.verifier import verify

SPEC = next(json.loads(l) for l in open("data/specs/specs_v2.jsonl") if '"make_collage"' in l and '"refines"' in l)
PERSONA = json.load(open(f"data/personas/{SPEC['persona']}.json"))


def reply(content=None, call=None):
    msg = {"role": "assistant", "content": content}
    if call:
        msg["tool_calls"] = [{"id": f"c{random.random()}", "type": "function",
                              "function": {"name": call["call"], "arguments": json.dumps(call["args"])}}]
    return ChatCompletion.model_validate({"id": "x", "object": "chat.completion", "created": 0, "model": "gallery-lora",
                                         "choices": [{"index": 0, "finish_reason": "tool_calls" if call else "stop",
                                                      "message": msg}]})


class ScriptedStudent:
    """Replays the spec: in each turn, the planned calls in order (with the simulator's handle ids), then a reply."""

    def __init__(self, spec):
        self.spec, self.requests = spec, []
        self.chat = self.completions = self
        self.handles = {"r0": "r0"} if spec["initial_selection"] else {}
        self.n = 0

    def create(self, **kw):
        self.requests.append(kw)
        msgs = kw["messages"]
        last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
        turn = sum(1 for m in msgs if m["role"] == "user") - 1
        done = sum(1 for m in msgs[last_user:] if m["role"] == "assistant")
        by_i = {s["i"]: s for s in self.spec["steps"]}
        for s in (by_i[i] for i in self.spec["turns"][turn]):        # selection lines allocate handles first
            if "event" in s and s["out"] not in self.handles:
                self.n += 1
                self.handles[s["out"]] = f"r{self.n}"
        planned = [by_i[i] for i in self.spec["turns"][turn] if "call" in by_i[i]]
        if done < len(planned):
            step = dict(planned[done])
            args = dict(step["args"])
            if "images" in args:
                args["images"] = self.handles[args["images"]]
            if step.get("out"):
                self.n += 1
                self.handles[step["out"]] = f"r{self.n}"
            return reply(call={"call": step["call"], "args": args})
        return reply(content="Done.")


def test_student_eval_path(tmp_path, monkeypatch):
    cfg = yaml.safe_load(open("config/llm.yaml"))
    cfg["cache_dir"] = str(tmp_path)
    path = tmp_path / "llm.yaml"
    path.write_text(yaml.safe_dump(cfg))
    student = llm_mod.LLM("student", config_path=path)
    student._client = ScriptedStudent(SPEC)
    monkeypatch.setattr(run, "write_message", lambda *a, **k: ("please do it", {"served_model": "stub"}, 1))

    ep = run.realize(SPEC, PERSONA, "casual", None, student, random.Random(0), guided=False)
    sys_msgs = [r["messages"][0]["content"] for r in student._client.requests]
    assert all(GUIDANCE_START not in s for s in sys_msgs)                 # student never sees teacher guidance
    assert student._client.requests[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert ep["assistant_role"] == "student" and ep["teacher_guidance_version"] is None
    v = verify(ep, PERSONA, fake_embed)
    assert v["accept"], v["failures"]
