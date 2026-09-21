import json

from pydantic import BaseModel

import llm as llm_mod


class Echo(BaseModel):
    text: str


def test_cache_records_served_model(tmp_path, monkeypatch):
    calls = []

    def fake(client, system, prompt, schema, seed):
        calls.append(prompt)
        return {"text": prompt.upper()}, "fake-model-001"

    monkeypatch.setitem(llm_mod.BACKENDS, "gemini", fake)
    client = llm_mod.LLM("generation")
    client.cache_dir = tmp_path

    first = client.json("hi", Echo, seed=1)
    again = client.json("hi", Echo, seed=1)          # served from cache
    assert calls == ["hi"]
    assert first.output == again.output == {"text": "HI"}
    assert first.meta["served_model"] == again.meta["served_model"] == "fake-model-001"
    entry = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert entry["served_model"] == "fake-model-001" and entry["model"] == client.model

    client.json("hi", Echo, seed=2)                   # new seed -> new call
    assert len(calls) == 2
