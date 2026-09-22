"""openai_compatible provider, against a fake OpenAI client (no server needed)."""
import json

import numpy as np
import pytest
import yaml
from openai.types import CreateEmbeddingResponse
from openai.types.chat import ChatCompletion
from pydantic import BaseModel

import llm as llm_mod


class Echo(BaseModel):
    text: str


def completion(message, finish="stop", model="served-model"):
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", **message}}]})


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.chat = self
        self.completions = self
        self.embeddings = self

    def create(self, **kw):
        self.requests.append(kw)
        if "input" in kw:
            data = [{"object": "embedding", "index": i, "embedding": [float(len(t)), 1.0]} for i, t in enumerate(kw["input"])]
            return CreateEmbeddingResponse.model_validate({"object": "list", "data": data, "model": kw["model"],
                                                           "usage": {"prompt_tokens": 1, "total_tokens": 1}})
        return self.replies.pop(0)


@pytest.fixture
def client(tmp_path):
    cfg = {"cache_dir": str(tmp_path), "roles": {
        "teacher": {"provider": "openai_compatible", "base_url": "http://localhost:8000/v1", "model": "m",
                    "max_tokens": 100, "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        "embedding": {"provider": "openai_compatible", "base_url": "http://localhost:8001/v1", "model": "e"}}}
    path = tmp_path / "llm.yaml"
    path.write_text(yaml.safe_dump(cfg))

    def make(role, replies=()):
        c = llm_mod.LLM(role, config_path=path)
        c.cache_dir = tmp_path
        c._client = FakeClient(replies)
        return c
    return make


def test_json_uses_json_schema_response_format(client):
    c = client("teacher", [completion({"content": '{"text": "hi"}'})])
    res = c.json("say hi", Echo, seed=1)
    req = c._client.requests[0]
    assert res.output == {"text": "hi"} and res.meta["served_model"] == "served-model"
    assert req["response_format"]["type"] == "json_schema" and req["extra_body"]["chat_template_kwargs"]
    assert req["messages"][-1] == {"role": "user", "content": "say hi"}


def test_chat_tool_calls_round_trip(client):
    call = {"id": "call_1", "type": "function", "function": {"name": "search_images", "arguments": '{"location": "Goa"}'}}
    c = client("teacher", [completion({"content": None, "tool_calls": [call]}, finish="tool_calls"),
                           completion({"content": "Found 3 photos from Goa."})])
    tools = [{"name": "search_images", "description": "Find photos", "parameters": {"type": "object", "properties": {}}}]
    contents = [c.user_message("show goa photos")]
    r1 = c.chat("sys", contents, tools, seed="s/0")
    assert r1.calls == [("search_images", {"location": "Goa"}, "call_1")] and r1.text == ""
    contents.append(r1.content)
    contents.extend(c.tool_results([("search_images", "call_1", {"id": "r1", "count": 3})]))
    r2 = c.chat("sys", contents, tools, seed="s/1")
    assert r2.calls == [] and r2.text == "Found 3 photos from Goa."
    req = c._client.requests[1]
    assert req["messages"][0] == {"role": "system", "content": "sys"}
    assert req["messages"][-1]["role"] == "tool" and req["messages"][-1]["tool_call_id"] == "call_1"
    assert req["tools"][0]["type"] == "function" and req["tool_choice"] == "auto"
    # cached: a replay makes no request
    again = c.chat("sys", contents, tools, seed="s/1")
    assert again.text == r2.text and len(c._client.requests) == 2


@pytest.mark.parametrize("message,finish,err", [
    ({"content": "x"}, "length", llm_mod.TruncatedResponseError),
    ({"content": "x"}, "content_filter", llm_mod.BlockedResponseError),
    ({"content": ""}, "stop", llm_mod.EmptyResponseError),
    ({"content": None, "tool_calls": [{"id": "c", "type": "function",
                                       "function": {"name": "f", "arguments": "{not json"}}]}, "tool_calls",
     llm_mod.BadOutputError),
])
def test_bad_outputs_raise(client, message, finish, err):
    c = client("teacher", [completion(message, finish=finish)])
    with pytest.raises(err):
        c.chat("sys", [c.user_message("hi")], [], seed="bad")


def test_embeddings_endpoint(client):
    c = client("embedding")
    v = c.embed(["a", "abcd"])
    assert v.shape == (2, 2) and np.allclose(np.linalg.norm(v, axis=1), 1)
    assert c._client.requests[0]["model"] == "e" and "dimensions" not in c._client.requests[0]


def test_gemini_message_shapes_unchanged():
    """realize/run.py now asks the client for messages; Gemini's must match the shape its cache was built on."""
    g = llm_mod.LLM("teacher")
    assert g.provider == "gemini"
    assert g.user_message("hi") == {"role": "user", "parts": [{"text": "hi"}]}
    assert g.tool_results([("f", "id1", {"a": 1}), ("g", None, {})]) == [{"role": "user", "parts": [
        {"function_response": {"name": "f", "response": {"a": 1}, "id": "id1"}},
        {"function_response": {"name": "g", "response": {}}}]}]
