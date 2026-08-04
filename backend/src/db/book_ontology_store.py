"""SQLite persistence for versioned book ontologies and collection instances."""

from __future__ import annotations

import json
from typing import Any

from src.db.sqlite_db import get_connection
from src.models.book_ontology import (
    BookOntology,
    CollectionMember,
    OntologyProposal,
)


async def load(novel_id: str) -> BookOntology:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT ontology_json FROM book_ontologies WHERE novel_id = ?",
            (novel_id,),
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    if not row:
        return BookOntology(novel_id=novel_id)
    return BookOntology.model_validate_json(row["ontology_json"])


async def save_version(
    ontology: BookOntology,
    *,
    change_summary: str,
    source_chapters: list[int],
) -> None:
    payload = ontology.model_dump_json()
    source_json = json.dumps(sorted(set(source_chapters)), ensure_ascii=False)
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO book_ontologies (novel_id, current_version, ontology_json)
            VALUES (?, ?, ?)
            ON CONFLICT(novel_id) DO UPDATE SET
                current_version = excluded.current_version,
                ontology_json = excluded.ontology_json,
                updated_at = datetime('now')
            """,
            (ontology.novel_id, ontology.version, payload),
        )
        await conn.execute(
            """
            INSERT OR REPLACE INTO book_ontology_versions
                (novel_id, version, ontology_json, change_summary, source_chapters)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                ontology.novel_id,
                ontology.version,
                payload,
                change_summary,
                source_json,
            ),
        )
        await conn.commit()
    finally:
        await conn.close()


async def list_versions(novel_id: str) -> list[dict[str, Any]]:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT version, change_summary, source_chapters, created_at
            FROM book_ontology_versions
            WHERE novel_id = ? ORDER BY version DESC
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()
    return [
        {
            **dict(row),
            "source_chapters": json.loads(row["source_chapters"] or "[]"),
        }
        for row in rows
    ]


async def upsert_proposal(novel_id: str, proposal: OntologyProposal) -> int:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, proposal_json, occurrence_count, first_chapter
            FROM book_ontology_proposals
            WHERE novel_id = ? AND fingerprint = ?
            """,
            (novel_id, proposal.fingerprint),
        )
        existing = await cursor.fetchone()
        if existing:
            previous = OntologyProposal.model_validate_json(existing["proposal_json"])
            is_new_chapter = proposal.last_chapter != previous.last_chapter
            merged = proposal.model_copy(
                update={
                    "first_chapter": min(previous.first_chapter, proposal.first_chapter),
                    "last_chapter": max(previous.last_chapter, proposal.last_chapter),
                    "occurrence_count": int(existing["occurrence_count"])
                    + (1 if is_new_chapter else 0),
                    "status": previous.status,
                }
            )
            await conn.execute(
                """
                UPDATE book_ontology_proposals
                SET proposal_json = ?, occurrence_count = ?, last_chapter = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    merged.model_dump_json(),
                    merged.occurrence_count,
                    merged.last_chapter,
                    existing["id"],
                ),
            )
            proposal_id = int(existing["id"])
        else:
            cursor = await conn.execute(
                """
                INSERT INTO book_ontology_proposals
                    (novel_id, fingerprint, proposal_json, occurrence_count,
                     first_chapter, last_chapter, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    novel_id,
                    proposal.fingerprint,
                    proposal.model_dump_json(),
                    proposal.occurrence_count,
                    proposal.first_chapter,
                    proposal.last_chapter,
                    proposal.status,
                ),
            )
            proposal_id = int(cursor.lastrowid)
        await conn.commit()
        return proposal_id
    finally:
        await conn.close()


async def list_proposals(
    novel_id: str, status: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM book_ontology_proposals WHERE novel_id = ?"
    params: list[Any] = [novel_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY last_chapter DESC, id DESC"
    conn = await get_connection()
    try:
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
    finally:
        await conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["proposal"] = OntologyProposal.model_validate_json(
            item.pop("proposal_json")
        ).model_dump()
        result.append(item)
    return result


async def get_proposal(novel_id: str, proposal_id: int) -> OntologyProposal | None:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT proposal_json FROM book_ontology_proposals
            WHERE novel_id = ? AND id = ?
            """,
            (novel_id, proposal_id),
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    if not row:
        return None
    return OntologyProposal.model_validate_json(row["proposal_json"])


async def set_proposal_status(
    novel_id: str, proposal_id: int, status: str
) -> None:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT proposal_json FROM book_ontology_proposals
            WHERE novel_id = ? AND id = ?
            """,
            (novel_id, proposal_id),
        )
        row = await cursor.fetchone()
        if not row:
            raise KeyError(proposal_id)
        proposal = OntologyProposal.model_validate_json(row["proposal_json"])
        proposal.status = status  # type: ignore[assignment]
        await conn.execute(
            """
            UPDATE book_ontology_proposals
            SET status = ?, proposal_json = ?, updated_at = datetime('now')
            WHERE novel_id = ? AND id = ?
            """,
            (status, proposal.model_dump_json(), novel_id, proposal_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def replace_collection_members(
    novel_id: str,
    collection_id: str,
    members: list[CollectionMember],
    source_chapter: int,
) -> None:
    conn = await get_connection()
    try:
        await conn.execute(
            """
            DELETE FROM book_collection_members
            WHERE novel_id = ? AND collection_id = ?
            """,
            (novel_id, collection_id),
        )
        await conn.executemany(
            """
            INSERT OR REPLACE INTO book_collection_members
                (novel_id, collection_id, entity_name, member_json, source_chapter)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    novel_id,
                    collection_id,
                    member.entity_name,
                    member.model_dump_json(),
                    source_chapter,
                )
                for member in members
            ],
        )
        await conn.commit()
    finally:
        await conn.close()


async def list_collection_members(
    novel_id: str, collection_id: str
) -> list[CollectionMember]:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT member_json FROM book_collection_members
            WHERE novel_id = ? AND collection_id = ?
            ORDER BY id
            """,
            (novel_id, collection_id),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()
    return [CollectionMember.model_validate_json(row["member_json"]) for row in rows]
