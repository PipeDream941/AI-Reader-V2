"""Tests for adaptive, book-specific ontology discovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.db import book_ontology_store
from src.infra.llm_client import LlmUsage
from src.models.book_ontology import (
    BookOntology,
    CollectionExtraction,
    CollectionMember,
    CollectionMemberValue,
    OntologyField,
    OntologyObservation,
    StructureDiscoveryResult,
)
from src.services.book_ontology_agent import ChapterStructureScout
from src.services.book_ontology_controller import BookOntologyController
from src.services.source_text_canonicalizer import SourceTextCanonicalizer


def _collection_observation(expected_count: int = 2) -> OntologyObservation:
    return OntologyObservation(
        kind="collection",
        label="巡守名录",
        parent_core_type="person",
        description="原文列出的巡守成员及其代号",
        fields=[
            OntologyField(
                key="code_name",
                label="代号",
                description="成员在名录中的代号",
                required=True,
            )
        ],
        evidence="共计二员，列名如下",
        confidence=0.96,
        expected_count=expected_count,
        evidence_strength="explicit_list",
    )


def _collection_extraction() -> CollectionExtraction:
    return CollectionExtraction(
        collection_label="巡守名录",
        source_chapter=9,
        expected_count=2,
        members=[
            CollectionMember(
                entity_name="沈青",
                values=[
                    CollectionMemberValue(
                        key="code_name", value="青灯", evidence="青灯沈青"
                    )
                ],
                evidence="青灯沈青",
            ),
            CollectionMember(
                entity_name="顾白",
                values=[
                    CollectionMemberValue(
                        key="code_name", value="白羽", evidence="白羽顾白"
                    )
                ],
                evidence="白羽顾白",
            ),
        ],
    )


def test_structure_scout_triggers_on_generic_bounded_roster():
    text = """
    計開巡守二員：
    「青燈」沈青　「白羽」顧白
    名冊既定，二人各守其位。
    """
    scout = ChapterStructureScout().inspect(text)
    # A tiny two-entry example lacks the six-slot boost, but forcing repeated
    # roster lines should cross the real scan threshold.
    rich = ChapterStructureScout().inspect(text + "\n" + text + "\n" + text)
    assert scout.score > 0
    assert rich.should_scan


def test_discovery_prompts_do_not_contain_benchmark_answer():
    prompt_dir = Path(__file__).resolve().parents[1] / "src" / "extraction" / "prompts"
    prompt = (
        (prompt_dir / "ontology_discovery_system.txt").read_text(encoding="utf-8")
        + (prompt_dir / "ontology_collection_system.txt").read_text(encoding="utf-8")
    )
    for leaked_term in ("水浒", "水滸", "梁山", "一百单八", "一百單八", "天罡", "地煞"):
        assert leaked_term not in prompt


def test_review_activates_only_complete_explicit_collection():
    observation = _collection_observation()
    extraction = _collection_extraction()
    chapter = "计开巡守二员：青灯沈青，白羽顾白。"

    review = BookOntologyController.review_collection(
        observation, extraction, chapter
    )

    assert review.decision == "activate"
    assert review.unique_member_count == 2
    assert review.reasons == []


def test_review_keeps_incomplete_collection_pending():
    observation = _collection_observation(expected_count=3)
    extraction = _collection_extraction()
    review = BookOntologyController.review_collection(
        observation, extraction, "青灯沈青，白羽顾白，共计三员。"
    )
    assert review.decision == "pending"
    assert any("成员数" in reason for reason in review.reasons)


def test_source_canonicalizer_restores_exact_traditional_spelling():
    extraction = CollectionExtraction(
        collection_label="名录",
        source_chapter=1,
        expected_count=1,
        members=[
            CollectionMember(
                entity_name="公孙胜",
                values=[
                    CollectionMemberValue(
                        key="star", value="天闲星", evidence="天闲星公孙胜"
                    )
                ],
            )
        ],
    )
    canonical, report = SourceTextCanonicalizer().canonicalize_collection(
        extraction, "天閑星「入雲龍」公孫勝"
    )
    assert canonical.members[0].entity_name == "公孫勝"
    assert canonical.members[0].values[0].value == "天閑星"
    assert report.corrected_names == 1
    assert report.corrected_values == 1


class _NonClosingConnection:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


class _FakeOntologyAgent:
    async def discover(self, ontology, chapter_num, chapter_text, scout):
        return (
            StructureDiscoveryResult(proposals=[_collection_observation()]),
            LlmUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )

    async def populate_collection(self, proposal, chapter_num, chapter_text):
        return (
            _collection_extraction(),
            LlmUsage(prompt_tokens=80, completion_tokens=40, total_tokens=120),
        )


class _IncompleteOntologyAgent:
    async def discover(self, ontology, chapter_num, chapter_text, scout):
        return (
            StructureDiscoveryResult(proposals=[_collection_observation(3)]),
            LlmUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    async def populate_collection(self, proposal, chapter_num, chapter_text):
        extraction = _collection_extraction()
        extraction.members = extraction.members[:1]
        extraction.expected_count = 3
        return (
            extraction,
            LlmUsage(prompt_tokens=8, completion_tokens=4, total_tokens=12),
        )


@pytest.mark.asyncio
async def test_controller_versions_approved_structure_and_stores_members(memory_db):
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES ('novel-1', '测试小说')"
    )
    await memory_db.commit()

    async def _connection():
        return _NonClosingConnection(memory_db)

    with patch("src.db.book_ontology_store.get_connection", _connection):
        controller = BookOntologyController(agent=_FakeOntologyAgent())
        result = await controller.scan_chapter(
            "novel-1",
            9,
            "计开巡守二员：青灯沈青，白羽顾白。" * 8,
            force=True,
            auto_activate=True,
        )
        ontology = await book_ontology_store.load("novel-1")
        members = await book_ontology_store.list_collection_members(
            "novel-1", ontology.definitions[0].id
        )

    assert result["collections"][0]["activated"] is True
    assert ontology.version == 1
    assert ontology.definitions[0].label == "巡守名录"
    assert [member.entity_name for member in members] == ["沈青", "顾白"]


@pytest.mark.asyncio
async def test_pending_rescan_does_not_overwrite_active_collection(memory_db):
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES ('novel-2', '测试小说')"
    )
    await memory_db.commit()

    async def _connection():
        return _NonClosingConnection(memory_db)

    with patch("src.db.book_ontology_store.get_connection", _connection):
        first = BookOntologyController(agent=_FakeOntologyAgent())
        await first.scan_chapter(
            "novel-2",
            9,
            "计开巡守二员：青灯沈青，白羽顾白。" * 8,
            force=True,
            auto_activate=True,
        )
        ontology = await book_ontology_store.load("novel-2")

        second = BookOntologyController(agent=_IncompleteOntologyAgent())
        result = await second.scan_chapter(
            "novel-2",
            10,
            "计开巡守三员：青灯沈青，另一员尚未列名。" * 8,
            force=True,
            auto_activate=True,
        )
        members = await book_ontology_store.list_collection_members(
            "novel-2", ontology.definitions[0].id
        )

    assert result["collections"][0]["review"]["decision"] == "pending"
    assert result["collections"][0]["members_persisted"] is False
    assert [member.entity_name for member in members] == ["沈青", "顾白"]
