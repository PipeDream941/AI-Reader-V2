"""Tests for the Codex batch-size safety boundary."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.analysis import (
    _enforce_codex_batch_limit,
    _ensure_active_provider_ready,
)
from src.infra import config


def test_codex_batch_limit_accepts_exact_limit(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_MAX_BATCH_CHAPTERS", 10)

    _enforce_codex_batch_limit(34, 43)


def test_codex_batch_limit_rejects_oversized_range(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_MAX_BATCH_CHAPTERS", 10)

    with pytest.raises(HTTPException) as exc_info:
        _enforce_codex_batch_limit(34, 44)

    assert exc_info.value.status_code == 400
    assert "10 章" in exc_info.value.detail
    assert "11 章" in exc_info.value.detail


def test_batch_limit_does_not_affect_other_providers(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "CODEX_MAX_BATCH_CHAPTERS", 10)

    _enforce_codex_batch_limit(1, 100)


def test_zero_disables_codex_batch_limit(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_MAX_BATCH_CHAPTERS", 0)

    _enforce_codex_batch_limit(1, 100)


@pytest.mark.asyncio
async def test_codex_preflight_rejects_missing_login(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex")
    monkeypatch.setattr(config, "CODEX_BIN", "codex-test")
    status = {
        "available": True,
        "authenticated": False,
        "version": "codex-cli 1.2.3",
        "auth_method": "",
        "error": "Codex CLI 尚未登录",
    }

    with patch(
        "src.infra.codex_exec_client.check_codex_cli",
        new=AsyncMock(return_value=status),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _ensure_active_provider_ready()

    assert exc_info.value.status_code == 503
    assert "尚未登录" in exc_info.value.detail


@pytest.mark.asyncio
async def test_non_codex_preflight_does_not_spawn_cli(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")

    with patch(
        "src.infra.codex_exec_client.check_codex_cli",
        new=AsyncMock(),
    ) as check:
        await _ensure_active_provider_ready()

    check.assert_not_awaited()
