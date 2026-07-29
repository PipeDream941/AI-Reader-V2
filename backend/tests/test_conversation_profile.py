"""Tests for persisting the model profile used by a Q&A response."""

from unittest.mock import patch

import pytest

from src.db import conversation_store


class _NonClosingConnection:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_assistant_message_keeps_codex_profile(memory_db):
    await memory_db.execute(
        """INSERT INTO novels
           (id, title, file_hash, total_chapters, total_words)
           VALUES ('novel-1', '书名', 'hash', 0, 0)"""
    )
    await memory_db.execute(
        """INSERT INTO conversations (id, novel_id, title)
           VALUES ('conv-1', 'novel-1', '测试问答')"""
    )
    await memory_db.commit()

    async def get_test_connection():
        return _NonClosingConnection(memory_db)

    with patch(
        "src.db.conversation_store.get_connection",
        get_test_connection,
    ):
        await conversation_store.add_message(
            "conv-1",
            "assistant",
            "回答",
            "[]",
            llm_model="gpt-5.6-luna",
            reasoning_effort="low",
        )
        messages = await conversation_store.list_messages("conv-1")

    assert messages == [{
        "id": 1,
        "conversation_id": "conv-1",
        "role": "assistant",
        "content": "回答",
        "sources": [],
        "llm_model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "created_at": messages[0]["created_at"],
    }]
