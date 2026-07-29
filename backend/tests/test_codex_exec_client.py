"""Tests for the Codex CLI LLM adapter."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.infra.codex_exec_client import (
    CodexExecClient,
    _build_instruction,
    _parse_codex_catalog,
    _parse_jsonl,
    check_codex_cli,
    get_codex_model_catalog,
    validate_codex_profile,
)
from src.infra.llm_client import LLMError, LLMTimeoutError


def test_parse_jsonl_extracts_final_message_and_usage():
    output = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "test"}),
        json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"answer":"ok"}'},
        }),
        json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 123, "output_tokens": 45},
        }),
    ]).encode()

    content, usage = _parse_jsonl(output)

    assert content == '{"answer":"ok"}'
    assert usage.prompt_tokens == 123
    assert usage.completion_tokens == 45
    assert usage.total_tokens == 168


def test_structured_instruction_forbids_tools_and_placeholders():
    instruction = _build_instruction(
        "system",
        "prompt",
        max_tokens=100,
        structured=True,
    )
    assert "Do not inspect the filesystem" in instruction
    assert "never use ellipses" in instruction
    assert "complete, parseable JSON" in instruction


def test_default_command_does_not_pin_a_model():
    command = CodexExecClient(model="")._command(None)

    assert "--model" not in command
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert 'web_search="disabled"' in command
    assert command[-1] == "-"


def test_parse_codex_catalog_filters_hidden_retiring_and_ultra():
    output = json.dumps({
        "models": [
            {
                "slug": "gpt-5.6-sol",
                "display_name": "GPT-5.6-Sol",
                "description": "frontier",
                "visibility": "list",
                "priority": 1,
                "default_reasoning_level": "low",
                "supported_reasoning_levels": [
                    {"effort": "low"},
                    {"effort": "max"},
                    {"effort": "ultra"},
                ],
                "upgrade": None,
            },
            {
                "slug": "retiring",
                "visibility": "list",
                "supported_reasoning_levels": [{"effort": "low"}],
                "upgrade": {"model": "gpt-5.6-sol"},
            },
            {
                "slug": "hidden",
                "visibility": "hide",
                "supported_reasoning_levels": [{"effort": "low"}],
            },
        ],
    })

    models = _parse_codex_catalog(output)

    assert models == [{
        "slug": "gpt-5.6-sol",
        "display_name": "GPT-5.6-Sol",
        "description": "frontier",
        "default_reasoning_level": "low",
        "supported_reasoning_levels": ["low", "max"],
    }]


@pytest.mark.asyncio
async def test_catalog_uses_cli_visible_models():
    output = json.dumps({
        "models": [{
            "slug": "gpt-test",
            "display_name": "GPT Test",
            "description": "",
            "visibility": "list",
            "priority": 1,
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [
                {"effort": "low"},
                {"effort": "medium"},
            ],
            "upgrade": None,
        }],
    })
    with patch(
        "src.infra.codex_exec_client._run_status_command",
        new=AsyncMock(return_value=(0, output)),
    ):
        catalog = await get_codex_model_catalog(
            "codex-catalog-test",
            force_refresh=True,
        )

    assert catalog["source"] == "cli"
    assert catalog["models"][0]["slug"] == "gpt-test"


@pytest.mark.asyncio
async def test_validate_codex_profile_rejects_unsupported_effort():
    output = json.dumps({
        "models": [{
            "slug": "gpt-test",
            "display_name": "GPT Test",
            "visibility": "list",
            "priority": 1,
            "default_reasoning_level": "low",
            "supported_reasoning_levels": [{"effort": "low"}],
            "upgrade": None,
        }],
    })
    with patch(
        "src.infra.codex_exec_client._run_status_command",
        new=AsyncMock(return_value=(0, output)),
    ):
        await get_codex_model_catalog(
            "codex-validation-test",
            force_refresh=True,
        )
        with pytest.raises(ValueError, match="不支持"):
            await validate_codex_profile(
                "gpt-test",
                "high",
                "codex-validation-test",
            )


@pytest.mark.asyncio
async def test_check_codex_cli_reports_chatgpt_login():
    with patch(
        "src.infra.codex_exec_client._run_status_command",
        new=AsyncMock(side_effect=[
            (0, "codex-cli 1.2.3"),
            (0, "Logged in using ChatGPT"),
        ]),
    ):
        status = await check_codex_cli("codex-test")

    assert status == {
        "available": True,
        "authenticated": True,
        "version": "codex-cli 1.2.3",
        "auth_method": "chatgpt",
        "error": "",
    }


@pytest.mark.asyncio
async def test_check_codex_cli_reports_missing_binary():
    with patch(
        "src.infra.codex_exec_client._run_status_command",
        new=AsyncMock(return_value=(127, "")),
    ):
        status = await check_codex_cli("missing-codex")

    assert status["available"] is False
    assert status["authenticated"] is False
    assert "未找到" in str(status["error"])


@pytest.mark.asyncio
async def test_check_codex_cli_reports_unlaunchable_binary():
    with patch(
        "src.infra.codex_exec_client._run_status_command",
        new=AsyncMock(return_value=(126, "")),
    ):
        status = await check_codex_cli("/path/not-executable")

    assert status["available"] is False
    assert status["authenticated"] is False
    assert "无法正常启动" in str(status["error"])


@pytest.mark.asyncio
async def test_generate_uses_strict_schema_and_parses_json():
    process = AsyncMock()
    process.returncode = 0
    event = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": '{"answer":"ok"}'},
    }
    completed = {
        "type": "turn.completed",
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }
    process.communicate.return_value = (
        (json.dumps(event) + "\n" + json.dumps(completed) + "\n").encode(),
        b"",
    )

    with patch("asyncio.create_subprocess_exec", return_value=process) as create:
        client = CodexExecClient(
            codex_bin="/usr/local/bin/codex",
            model="gpt-test",
            min_timeout_seconds=1,
        )
        result, usage = await client.generate(
            system="system",
            prompt="prompt",
            format={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        )

    assert result == {"answer": "ok"}
    assert usage.total_tokens == 12
    args = create.call_args.args
    assert args[0] == "/usr/local/bin/codex"
    assert "--output-schema" in args
    assert args[args.index("--model") + 1] == "gpt-test"
    assert 'model_reasoning_effort="low"' in args
    process.communicate.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_skips_non_strict_schema_but_still_parses_json():
    process = AsyncMock()
    process.returncode = 0
    event = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": '{"answer":"ok"}'},
    }
    process.communicate.return_value = (
        (json.dumps(event) + "\n").encode(),
        b"",
    )

    with patch("asyncio.create_subprocess_exec", return_value=process) as create:
        client = CodexExecClient(min_timeout_seconds=1)
        result, _usage = await client.generate(
            system="system",
            prompt="prompt",
            format={"type": "object"},
        )

    assert result == {"answer": "ok"}
    assert "--output-schema" not in create.call_args.args


@pytest.mark.asyncio
async def test_generate_reports_error_without_echoing_agent_output():
    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = (
        (
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "private novel output"},
            })
            + "\n"
            + json.dumps({"type": "error", "message": "authentication required"})
        ).encode(),
        b"",
    )

    with patch("asyncio.create_subprocess_exec", return_value=process):
        client = CodexExecClient(min_timeout_seconds=1)
        with pytest.raises(LLMError, match="authentication required") as exc_info:
            await client.generate(system="system", prompt="prompt")

    assert "private novel output" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_generate_kills_process_on_timeout():
    process = AsyncMock()
    process.returncode = None
    process.communicate.side_effect = asyncio.TimeoutError
    process.kill = Mock()

    with patch("asyncio.create_subprocess_exec", return_value=process):
        client = CodexExecClient(min_timeout_seconds=0)
        with pytest.raises(LLMTimeoutError):
            await client.generate(system="system", prompt="prompt", timeout=0)

    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_kills_process_when_caller_cancels():
    process = AsyncMock()
    process.returncode = None
    process.communicate.side_effect = asyncio.CancelledError
    process.kill = Mock()

    with patch("asyncio.create_subprocess_exec", return_value=process):
        client = CodexExecClient(min_timeout_seconds=1)
        with pytest.raises(asyncio.CancelledError):
            await client.generate(system="system", prompt="prompt")

    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
