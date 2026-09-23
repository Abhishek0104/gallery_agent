"""LLM_OVERLAY: local roles replace config/llm.yaml roles whole; preflight and verify.run follow it."""
import copy
import json

import pytest

import llm as llm_mod
import scripts.preflight_cuda as pre
from verify.run import compare_verdicts


def test_overlay_replaces_roles_whole(monkeypatch):
    base = llm_mod.load_config()
    monkeypatch.setenv("LLM_OVERLAY", "config/llm_local.yaml")
    cfg = llm_mod.load_config()
    assert cfg["roles"]["user_sim"]["provider"] == "openai_compatible"
    assert "thinking" in cfg["roles"]["user_sim"] and cfg["roles"]["user_sim"]["thinking"] == "none"
    assert cfg["roles"]["embedding"] == {"provider": "local", "model": "Qwen/Qwen3-Embedding-0.6B"}   # no stale keys
    for role in ("generation", "filler", "teacher", "cleanup", "student"):
        assert cfg["roles"][role] == base["roles"][role]
    assert set(cfg["overlay"]["serve"]) == {"student", "user_sim"}
    assert llm_mod.LLM("user_sim").model == "Qwen/Qwen3-8B"


def test_overlay_empty_or_missing(monkeypatch):
    monkeypatch.setenv("LLM_OVERLAY", "")
    assert "overlay" not in llm_mod.load_config()                   # set but empty = no overlay
    monkeypatch.setenv("LLM_OVERLAY", "config/nope.yaml")
    with pytest.raises(llm_mod.LLMError):
        llm_mod.load_config()


def test_preflight_needs_gemini_key_only_for_gemini_roles(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pre, "ROOT", tmp_path)                       # no .env there
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    trn = {"output_dir": "runs/x"}

    def errors():
        pre.ERRORS.clear()
        pre.check_env(trn)
        return [e for e in pre.ERRORS if "GEMINI" in e]

    assert errors()                                                  # Gemini user_sim + embedding, no key
    monkeypatch.setenv("LLM_OVERLAY", "config/llm_local.yaml")
    assert not errors()
    pre.ERRORS.clear()


def test_serve_plan(monkeypatch, capsys):
    monkeypatch.setenv("LLM_OVERLAY", "config/llm_local.yaml")
    pre.serve_plan()
    assert capsys.readouterr().out.splitlines() == ["student gallery-lora 8000 0.25 - -",
                                                    "user_sim Qwen/Qwen3-8B 8001 0.55 8192 -"]
    monkeypatch.setenv("LLM_OVERLAY", "")
    pre.serve_plan()
    assert capsys.readouterr().out == ""


def test_compare_verdicts(capsys):
    old = [json.loads(l) for l in open("data/episodes/verified_v2c.jsonl")][:5]
    new = copy.deepcopy(old)
    new[0]["accept"] = not new[0]["accept"]
    new[0]["score"] -= 0.1
    compare_verdicts(new, old, "v2c")
    out = capsys.readouterr().out
    assert "accept agrees 4/5" in out and new[0]["episode_id"] in out and "mean |score diff| 0.0200" in out
