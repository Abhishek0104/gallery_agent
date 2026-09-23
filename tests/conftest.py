import pytest


@pytest.fixture(autouse=True)
def no_llm_overlay(monkeypatch):
    """Tests (and the golden snapshot) always see config/llm.yaml as committed, whatever the shell exports."""
    monkeypatch.delenv("LLM_OVERLAY", raising=False)
