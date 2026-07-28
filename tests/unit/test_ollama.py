"""Tests for the local-Ollama client's offline logic (no server required)."""

from __future__ import annotations

from cost_running.infrastructure import ollama


def test_absent_server_reports_unavailable(monkeypatch):
    # With no models reachable, every read degrades to a safe empty/None/False.
    monkeypatch.setattr(ollama, "available_models", lambda timeout=1.5: [])
    assert ollama.is_available() is False
    assert ollama.pick_model() is None
    # generate short-circuits to None when no model can be chosen.
    monkeypatch.setattr(ollama, "pick_model", lambda *a, **k: None)
    assert ollama.generate("hello") is None


def test_pick_prefers_exact_then_code_model(monkeypatch):
    monkeypatch.setattr(
        ollama,
        "available_models",
        lambda timeout=1.5: ["llama3.2:1b", "qwen2.5-coder:latest", "mistral:latest"],
    )
    # The configured default (a code model) is present, so it wins.
    monkeypatch.delenv("COST_RUNNING_OLLAMA_MODEL", raising=False)
    assert ollama.pick_model() == "qwen2.5-coder:latest"


def test_pick_falls_back_to_code_preference(monkeypatch):
    # The default coder tag is absent; the code-model preference still finds one.
    monkeypatch.setattr(
        ollama,
        "available_models",
        lambda timeout=1.5: ["llama3.2:1b", "mistral:latest"],
    )
    monkeypatch.delenv("COST_RUNNING_OLLAMA_MODEL", raising=False)
    # mistral and llama3 are both in the preference list; mistral ranks first.
    assert ollama.pick_model() == "mistral:latest"


def test_env_override_wins_when_installed(monkeypatch):
    monkeypatch.setattr(
        ollama,
        "available_models",
        lambda timeout=1.5: ["qwen2.5-coder:latest", "gemma3:4b"],
    )
    monkeypatch.setenv("COST_RUNNING_OLLAMA_MODEL", "gemma3:4b")
    assert ollama.pick_model() == "gemma3:4b"
