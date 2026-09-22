import json

import pytest

from export.build_export import check_record, to_record
from export.render_check import parse_completion

EP = next(json.loads(l) for l in open("data/episodes/episodes_r2.jsonl"))
VERDICT = {"score": 1.0, "accept": True}
TOOLS = [{"type": "function", "function": {"name": "search_images", "description": "",
          "parameters": {"type": "object", "properties": {"people": {"type": "array", "items": {"type": "string"}},
                                                          "date": {"type": "string"}}}}}]


def test_record_shape_and_no_guidance():
    r = to_record(EP, "r2", VERDICT)
    check_record(r)
    assert r["messages"][0] == {"role": "system", "content": EP["system_prompt"]}
    calls = [m for m in r["messages"] if m.get("tool_calls")]
    assert all(isinstance(m["tool_calls"][0]["function"]["arguments"], dict) for m in calls)
    assert all("id" not in m["tool_calls"][0] for m in calls)


def test_guidance_leak_fails_loudly():
    r = to_record(EP, "r2", VERDICT)
    r["messages"][0]["content"] += " <teacher_guidance> ..."
    with pytest.raises(AssertionError):
        check_record(r)


def test_xml_params_parsed_by_schema():
    text = ("<tool_call>\n<function=search_images>\n<parameter=people>\n[\"Riya\"]\n</parameter>\n"
            "<parameter=date>\n2022\n</parameter>\n</function>\n</tool_call><|im_end|>")
    assert parse_completion(text, TOOLS) == ("call", "search_images", {"people": ["Riya"], "date": "2022"})
    assert parse_completion("Here are 3 photos.<|im_end|>", TOOLS) == ("text", "Here are 3 photos.", None)
