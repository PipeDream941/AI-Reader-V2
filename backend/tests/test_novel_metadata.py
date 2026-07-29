"""Tests for safe novel and volume metadata edits."""

from unittest.mock import patch

import pytest

from src.db import novel_store


class _NonClosingConnection:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_update_novel_and_volume_metadata(memory_db):
    await memory_db.execute(
        """INSERT INTO novels
           (id, title, author, file_hash, total_chapters, total_words)
           VALUES ('novel-1', '旧书名', '旧作者', 'hash', 2, 10)"""
    )
    await memory_db.executemany(
        """INSERT INTO chapters
           (novel_id, chapter_num, volume_num, volume_title, title, content)
           VALUES ('novel-1', ?, 1, '', ?, '正文')""",
        [(1, "第一章"), (2, "第二章")],
    )
    await memory_db.commit()

    async def get_test_connection():
        return _NonClosingConnection(memory_db)

    with patch("src.db.novel_store.get_connection", get_test_connection):
        novel = await novel_store.update_metadata(
            "novel-1",
            "新书名",
            "新作者",
        )
        volumes = await novel_store.list_volumes("novel-1")
        updated_volume = await novel_store.update_volume_title(
            "novel-1",
            1,
            "第一卷 初入江湖",
        )

    assert novel is not None
    assert novel["title"] == "新书名"
    assert novel["author"] == "新作者"
    assert volumes == [{
        "volume_num": 1,
        "title": "",
        "first_chapter": 1,
        "last_chapter": 2,
        "chapter_count": 2,
    }]
    assert updated_volume == {
        "volume_num": 1,
        "title": "第一卷 初入江湖",
        "chapter_count": 2,
    }

    rows = await memory_db.execute_fetchall(
        """SELECT DISTINCT volume_title
           FROM chapters WHERE novel_id = 'novel-1'"""
    )
    assert [row[0] for row in rows] == ["第一卷 初入江湖"]


@pytest.mark.asyncio
async def test_update_missing_volume_returns_none(memory_db):
    await memory_db.execute(
        """INSERT INTO novels
           (id, title, file_hash, total_chapters, total_words)
           VALUES ('novel-1', '书名', 'hash', 0, 0)"""
    )
    await memory_db.commit()

    async def get_test_connection():
        return _NonClosingConnection(memory_db)

    with patch("src.db.novel_store.get_connection", get_test_connection):
        result = await novel_store.update_volume_title(
            "novel-1",
            99,
            "不存在",
        )

    assert result is None
