"""Versioned, book-specific ontology models.

The extraction database keeps a stable core fact model.  These models describe
the adaptive layer above it: reusable categories, attributes, relations and
bounded collections discovered from a particular book.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


CoreEntityType = Literal["person", "location", "item", "org", "concept", "event"]
OntologyKind = Literal["category", "attribute", "relation", "collection"]
ProposalStatus = Literal["pending", "active", "rejected", "merged", "deprecated"]
EvidenceStrength = Literal["explicit_list", "repeated_pattern", "inferred"]


class OntologyField(BaseModel):
    key: str
    label: str
    description: str = ""
    value_type: Literal["string", "number", "boolean", "entity_ref"] = "string"
    required: bool = False

    @field_validator("key")
    @classmethod
    def _normalize_key(cls, value: str) -> str:
        value = value.strip().lower().replace(" ", "_")
        if not value:
            raise ValueError("ontology field key cannot be empty")
        return value


class OntologyObservation(BaseModel):
    """A non-authoritative structure candidate emitted during chapter extraction."""

    kind: OntologyKind
    label: str
    parent_core_type: CoreEntityType
    description: str = ""
    fields: list[OntologyField] = []
    evidence: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_count: int | None = Field(default=None, ge=1)
    evidence_strength: EvidenceStrength = "inferred"


class OntologyDefinition(BaseModel):
    id: str
    kind: OntologyKind
    label: str
    parent_core_type: CoreEntityType
    parent_id: str | None = None
    description: str = ""
    fields: list[OntologyField] = []
    aliases: list[str] = []
    discovered_at_chapter: int
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: list[str] = []
    status: ProposalStatus = "active"


class BookOntology(BaseModel):
    novel_id: str
    version: int = 0
    definitions: list[OntologyDefinition] = []


class OntologyProposal(BaseModel):
    fingerprint: str
    observation: OntologyObservation
    first_chapter: int
    last_chapter: int
    occurrence_count: int = 1
    status: ProposalStatus = "pending"


class CollectionMemberValue(BaseModel):
    key: str
    value: str
    evidence: str = ""


class CollectionMember(BaseModel):
    entity_name: str
    values: list[CollectionMemberValue] = []
    evidence: str = ""


class CollectionExtraction(BaseModel):
    collection_label: str
    source_chapter: int
    expected_count: int | None = Field(default=None, ge=1)
    members: list[CollectionMember] = []


class StructureDiscoveryResult(BaseModel):
    proposals: list[OntologyObservation] = []


class CollectionReview(BaseModel):
    decision: Literal["activate", "pending", "reject"]
    unique_member_count: int
    expected_count: int | None = None
    duplicate_names: list[str] = []
    missing_required_fields: dict[str, list[str]] = {}
    reasons: list[str] = []
