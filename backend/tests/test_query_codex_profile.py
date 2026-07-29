"""Tests for request-scoped Codex profiles in novel Q&A."""

from unittest.mock import AsyncMock, patch

import pytest

from src.infra import config
from src.infra.codex_exec_client import CodexExecClient
from src.services.query_service import _get_query_client


@pytest.mark.asyncio
async def test_query_can_override_codex_model_and_effort(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_BIN", "codex-test")
    monkeypatch.setattr(config, "CODEX_MIN_TIMEOUT_SECONDS", 42)
    profile = {
        "model": "gpt-5.6-sol",
        "display_name": "GPT-5.6 Sol",
        "reasoning_effort": "high",
    }

    with patch(
        "src.infra.codex_exec_client.validate_codex_profile",
        new=AsyncMock(return_value=profile),
    ):
        client, selected = await _get_query_client(
            "gpt-5.6-sol",
            "high",
        )

    assert isinstance(client, CodexExecClient)
    assert client.codex_model == "gpt-5.6-sol"
    assert client.reasoning_effort == "high"
    assert client.min_timeout_seconds == 42
    assert selected == profile


@pytest.mark.asyncio
async def test_query_rejects_profile_override_for_non_codex(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")

    with pytest.raises(ValueError, match="不支持"):
        await _get_query_client("gpt-5.6-sol", "low")
