"""Orchestrate ontology discovery, review, versioning and collection storage."""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from src.db import book_ontology_store
from src.models.book_ontology import (
    BookOntology,
    CollectionExtraction,
    CollectionReview,
    OntologyDefinition,
    OntologyObservation,
    OntologyProposal,
)
from src.services.book_ontology_agent import (
    BookOntologyAgent,
    ChapterStructureScout,
    ScoutResult,
)
from src.services.source_text_canonicalizer import SourceTextCanonicalizer


def _normalized_label(value: str) -> str:
    return re.sub(r"[\s·・、，,。:：/／_-]+", "", value).lower()


def proposal_fingerprint(observation: OntologyObservation) -> str:
    basis = "|".join(
        [
            observation.kind,
            observation.parent_core_type,
            _normalized_label(observation.label),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def definition_id(observation: OntologyObservation) -> str:
    return f"ontology-{proposal_fingerprint(observation)}"


class BookOntologyController:
    def __init__(self, agent: BookOntologyAgent | None = None):
        self.agent = agent
        self.scout = ChapterStructureScout()

    async def ingest_observations(
        self,
        novel_id: str,
        chapter_num: int,
        observations: list[OntologyObservation],
    ) -> list[int]:
        proposal_ids: list[int] = []
        for observation in observations:
            fingerprint = proposal_fingerprint(observation)
            proposal = OntologyProposal(
                fingerprint=fingerprint,
                observation=observation,
                first_chapter=chapter_num,
                last_chapter=chapter_num,
            )
            proposal_ids.append(
                await book_ontology_store.upsert_proposal(novel_id, proposal)
            )
        return proposal_ids

    async def scan_chapter(
        self,
        novel_id: str,
        chapter_num: int,
        chapter_text: str,
        *,
        force: bool = False,
        auto_activate: bool = True,
    ) -> dict:
        scout = self.scout.inspect(chapter_text)
        if not force and not scout.should_scan:
            return {
                "status": "skipped",
                "scout": {"score": scout.score, "reasons": list(scout.reasons)},
                "proposals": [],
                "collections": [],
            }

        agent = self.agent or BookOntologyAgent()
        ontology = await book_ontology_store.load(novel_id)
        active_collection_ids = {
            definition.id
            for definition in ontology.definitions
            if definition.kind == "collection" and definition.status == "active"
        }
        discovery, discovery_usage = await agent.discover(
            ontology, chapter_num, chapter_text, scout
        )
        proposal_ids = await self.ingest_observations(
            novel_id, chapter_num, discovery.proposals
        )

        collections: list[dict] = []
        total_prompt_tokens = discovery_usage.prompt_tokens
        total_completion_tokens = discovery_usage.completion_tokens
        for proposal_id, observation in zip(proposal_ids, discovery.proposals):
            if observation.kind != "collection":
                continue
            extraction, usage = await agent.populate_collection(
                observation, chapter_num, chapter_text
            )
            extraction, canonicalization = (
                SourceTextCanonicalizer().canonicalize_collection(
                    extraction, chapter_text
                )
            )
            total_prompt_tokens += usage.prompt_tokens
            total_completion_tokens += usage.completion_tokens
            review = self.review_collection(observation, extraction, chapter_text)
            collection_id = definition_id(observation)
            # A weak later extraction must never erase an already approved
            # complete snapshot. New candidates are retained for inspection;
            # an active collection is replaced only by another fully reviewed
            # explicit list.
            members_persisted = (
                review.decision == "activate"
                or collection_id not in active_collection_ids
            )
            if members_persisted:
                await book_ontology_store.replace_collection_members(
                    novel_id,
                    collection_id,
                    extraction.members,
                    source_chapter=chapter_num,
                )
            activated = False
            if auto_activate and review.decision == "activate":
                await self.activate_proposal(novel_id, proposal_id)
                activated = True
            collections.append(
                {
                    "proposal_id": proposal_id,
                    "collection_id": collection_id,
                    "label": observation.label,
                    "member_count": review.unique_member_count,
                    "review": review.model_dump(),
                    "canonicalization": {
                        "corrected_names": canonicalization.corrected_names,
                        "corrected_values": canonicalization.corrected_values,
                        "unresolved_names": list(canonicalization.unresolved_names),
                    },
                    "members_persisted": members_persisted,
                    "activated": activated,
                }
            )

        return {
            "status": "completed",
            "scout": {"score": scout.score, "reasons": list(scout.reasons)},
            "proposals": [
                {"id": pid, **proposal.model_dump()}
                for pid, proposal in zip(proposal_ids, discovery.proposals)
            ],
            "collections": collections,
            "usage": {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
            },
        }

    @staticmethod
    def review_collection(
        proposal: OntologyObservation,
        extraction: CollectionExtraction,
        chapter_text: str,
    ) -> CollectionReview:
        names = [member.entity_name.strip() for member in extraction.members]
        counts = Counter(name for name in names if name)
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        unique_names = set(counts)
        unsupported = sorted(name for name in unique_names if name not in chapter_text)

        required_fields = {field.key for field in proposal.fields if field.required}
        missing: dict[str, list[str]] = {}
        for member in extraction.members:
            present = {value.key for value in member.values if value.value.strip()}
            absent = sorted(required_fields - present)
            if absent:
                missing[member.entity_name] = absent

        reasons: list[str] = []
        expected = proposal.expected_count or extraction.expected_count
        if expected is not None and len(unique_names) != expected:
            reasons.append(f"成员数 {len(unique_names)} 与原文明示总数 {expected} 不一致")
        if duplicates:
            reasons.append(f"存在重复成员 {len(duplicates)} 个")
        if unsupported:
            reasons.append(f"有 {len(unsupported)} 个姓名无法在本章原文定位")
        if missing:
            reasons.append(f"有 {len(missing)} 个成员缺少必填属性")
        if proposal.evidence_strength != "explicit_list":
            reasons.append("不是完整明示名册，不能自动生效")
        if proposal.confidence < 0.85:
            reasons.append("结构发现置信度低于自动生效阈值 0.85")

        if not unique_names:
            decision = "reject"
            reasons.append("没有提取到任何成员")
        elif not reasons and expected is not None:
            decision = "activate"
        else:
            decision = "pending"

        return CollectionReview(
            decision=decision,
            unique_member_count=len(unique_names),
            expected_count=expected,
            duplicate_names=duplicates,
            missing_required_fields=missing,
            reasons=reasons,
        )

    async def activate_proposal(
        self, novel_id: str, proposal_id: int
    ) -> BookOntology:
        proposal = await book_ontology_store.get_proposal(novel_id, proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)

        ontology = await book_ontology_store.load(novel_id)
        target_id = definition_id(proposal.observation)
        existing = next(
            (
                definition
                for definition in ontology.definitions
                if definition.id == target_id
                or (
                    definition.kind == proposal.observation.kind
                    and definition.parent_core_type
                    == proposal.observation.parent_core_type
                    and _normalized_label(definition.label)
                    == _normalized_label(proposal.observation.label)
                )
            ),
            None,
        )
        if existing:
            known_fields = {field.key for field in existing.fields}
            existing.fields.extend(
                field
                for field in proposal.observation.fields
                if field.key not in known_fields
            )
            if proposal.observation.evidence not in existing.evidence:
                existing.evidence.append(proposal.observation.evidence)
            existing.confidence = max(existing.confidence, proposal.observation.confidence)
            summary = f"合并候选到“{existing.label}”"
        else:
            ontology.definitions.append(
                OntologyDefinition(
                    id=target_id,
                    kind=proposal.observation.kind,
                    label=proposal.observation.label,
                    parent_core_type=proposal.observation.parent_core_type,
                    description=proposal.observation.description,
                    fields=proposal.observation.fields,
                    discovered_at_chapter=proposal.first_chapter,
                    confidence=proposal.observation.confidence,
                    evidence=[proposal.observation.evidence],
                )
            )
            summary = f"新增结构“{proposal.observation.label}”"

        ontology.version += 1
        await book_ontology_store.save_version(
            ontology,
            change_summary=summary,
            source_chapters=[proposal.first_chapter, proposal.last_chapter],
        )
        await book_ontology_store.set_proposal_status(
            novel_id, proposal_id, "active" if not existing else "merged"
        )
        return ontology

    async def reject_proposal(self, novel_id: str, proposal_id: int) -> None:
        await book_ontology_store.set_proposal_status(
            novel_id, proposal_id, "rejected"
        )

    async def current_context(self, novel_id: str) -> str:
        ontology = await book_ontology_store.load(novel_id)
        active = [item for item in ontology.definitions if item.status == "active"]
        if not active:
            return ""
        lines = [f"### 本书已确认结构（v{ontology.version}）"]
        lines.append("以下分类由前文证据确认；优先映射到现有结构，不要创建近义重复项。")
        for item in active[:30]:
            field_labels = "、".join(field.label for field in item.fields)
            suffix = f"；成员属性：{field_labels}" if field_labels else ""
            lines.append(
                f"- {item.label}（{item.kind}/{item.parent_core_type}）："
                f"{item.description}{suffix}"
            )
        return "\n".join(lines)
