"""LLM agents for discovering and populating book-specific structures."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.infra import config as _cfg
from src.infra.llm_client import LlmUsage, get_llm_client
from src.models.book_ontology import (
    BookOntology,
    CollectionExtraction,
    OntologyObservation,
    StructureDiscoveryResult,
)


_PROMPT_DIR = Path(__file__).resolve().parents[1] / "extraction" / "prompts"


DISCOVERY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["proposals"],
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "kind",
                    "label",
                    "parent_core_type",
                    "description",
                    "fields",
                    "evidence",
                    "confidence",
                    "expected_count",
                    "evidence_strength",
                ],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["category", "attribute", "relation", "collection"],
                    },
                    "label": {"type": "string"},
                    "parent_core_type": {
                        "type": "string",
                        "enum": ["person", "location", "item", "org", "concept", "event"],
                    },
                    "description": {"type": "string"},
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "key",
                                "label",
                                "description",
                                "value_type",
                                "required",
                            ],
                            "properties": {
                                "key": {"type": "string"},
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                                "value_type": {
                                    "type": "string",
                                    "enum": ["string", "number", "boolean", "entity_ref"],
                                },
                                "required": {"type": "boolean"},
                            },
                        },
                    },
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "expected_count": {"type": ["integer", "null"], "minimum": 1},
                    "evidence_strength": {
                        "type": "string",
                        "enum": ["explicit_list", "repeated_pattern", "inferred"],
                    },
                },
            },
        }
    },
}


COLLECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["collection_label", "source_chapter", "expected_count", "members"],
    "properties": {
        "collection_label": {"type": "string"},
        "source_chapter": {"type": "integer"},
        "expected_count": {"type": ["integer", "null"], "minimum": 1},
        "members": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["entity_name", "values", "evidence"],
                "properties": {
                    "entity_name": {"type": "string"},
                    "values": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["key", "value", "evidence"],
                            "properties": {
                                "key": {"type": "string"},
                                "value": {"type": "string"},
                                "evidence": {"type": "string"},
                            },
                        },
                    },
                    "evidence": {"type": "string"},
                },
            },
        },
    },
}


@dataclass(frozen=True)
class ScoutResult:
    score: int
    reasons: tuple[str, ...]

    @property
    def should_scan(self) -> bool:
        return self.score >= 3


class ChapterStructureScout:
    """Cheap, genre-neutral trigger that never calls an LLM."""

    _COUNT_PATTERN = re.compile(
        r"(?:共|計|合計|總計|分為|分作)[^。；\n]{0,18}"
        r"[零〇一二兩三四五六七八九十百千\d]+(?:員|人|位|名|種|級|品|類)"
    )
    _STRUCTURE_WORDS = re.compile(
        r"名冊|名單|譜系|排行|排序|座次|等級|品階|職司|計開|分組|分作|分為"
    )
    _REPEATED_SLOT = re.compile(r"「[^」\n]{1,12}」\s*[\u3400-\u9fff]{2,6}")
    _HEADING_COUNT = re.compile(
        r"^[　 \t]*[^。！？\n]{1,30}[零〇一二兩三四五六七八九十百千\d]+(?:員|人|位|名|種|級|品|類)[：:]?\s*$",
        re.MULTILINE,
    )

    def inspect(self, chapter_text: str) -> ScoutResult:
        reasons: list[str] = []
        score = 0
        count_hits = len(self._COUNT_PATTERN.findall(chapter_text))
        if count_hits:
            score += min(2, count_hits)
            reasons.append(f"bounded-count:{count_hits}")
        structure_hits = len(self._STRUCTURE_WORDS.findall(chapter_text))
        if structure_hits:
            score += min(2, structure_hits)
            reasons.append(f"structure-terms:{structure_hits}")
        repeated_hits = len(self._REPEATED_SLOT.findall(chapter_text))
        if repeated_hits >= 6:
            score += 3
            reasons.append(f"repeated-slots:{repeated_hits}")
        heading_hits = len(self._HEADING_COUNT.findall(chapter_text))
        if heading_hits >= 2:
            score += 2
            reasons.append(f"group-headings:{heading_hits}")
        return ScoutResult(score=score, reasons=tuple(reasons))

    def discovery_excerpt(self, chapter_text: str, max_chars: int = 4200) -> str:
        """Select evidence windows for structure discovery, preserving order.

        Discovery only needs enough repeated examples to infer a schema. Sending
        every narrative paragraph and every member wastes quota and can distract
        the first-stage agent. Population still receives the source needed to
        enumerate the selected collection.
        """
        lines = chapter_text.splitlines()
        priority: set[int] = set()
        repeated: list[int] = []
        for index, line in enumerate(lines):
            if (
                self._COUNT_PATTERN.search(line)
                or self._STRUCTURE_WORDS.search(line)
                or self._HEADING_COUNT.search(line)
            ):
                priority.add(index)
            if self._REPEATED_SLOT.search(line):
                repeated.append(index)

        if repeated:
            # A representative sample is sufficient to infer repeated slots.
            sample_positions = repeated[:8]
            if len(repeated) > 12:
                sample_positions += repeated[-4:]
            priority.update(sample_positions)

        selected: set[int] = set()
        for index in priority:
            selected.update(range(max(0, index - 2), min(len(lines), index + 3)))
        if not selected:
            return chapter_text[:max_chars]

        chunks: list[str] = []
        previous = -2
        for index in sorted(selected):
            if index != previous + 1 and chunks:
                chunks.append("[…省略与结构无关的叙事…]")
            chunks.append(lines[index])
            previous = index
        excerpt = "\n".join(chunks)
        return excerpt[:max_chars]


class BookOntologyAgent:
    def __init__(self, llm=None):
        self.llm = llm or get_llm_client()
        self.discovery_system = (
            _PROMPT_DIR / "ontology_discovery_system.txt"
        ).read_text(encoding="utf-8")
        self.collection_system = (
            _PROMPT_DIR / "ontology_collection_system.txt"
        ).read_text(encoding="utf-8")

    @staticmethod
    def _context_window() -> int:
        if _cfg.LLM_PROVIDER == "codex":
            return _cfg.CODEX_CONTEXT_WINDOW
        return _cfg.CONTEXT_WINDOW_SIZE

    @staticmethod
    def _timeout(minimum: int) -> int:
        if _cfg.LLM_PROVIDER == "codex":
            return max(minimum, _cfg.CODEX_MIN_TIMEOUT_SECONDS)
        return minimum

    async def discover(
        self,
        ontology: BookOntology,
        chapter_num: int,
        chapter_text: str,
        scout: ScoutResult,
    ) -> tuple[StructureDiscoveryResult, LlmUsage]:
        active = [
            {
                "id": item.id,
                "kind": item.kind,
                "label": item.label,
                "parent_core_type": item.parent_core_type,
                "description": item.description,
                "fields": [field.model_dump() for field in item.fields],
            }
            for item in ontology.definitions
            if item.status == "active"
        ]
        excerpt = ChapterStructureScout().discovery_excerpt(chapter_text)
        prompt = (
            f"## 当前本书本体 v{ontology.version}\n"
            f"{json.dumps(active, ensure_ascii=False, indent=2)}\n\n"
            f"## 本地结构信号（只用于提示关注，不是结论）\n"
            f"{json.dumps(list(scout.reasons), ensure_ascii=False)}\n\n"
            f"## 第 {chapter_num} 章结构证据窗口\n{excerpt}"
        )
        result, usage = await self.llm.generate(
            system=self.discovery_system,
            prompt=prompt,
            format=DISCOVERY_SCHEMA,
            temperature=0.0,
            max_tokens=min(_cfg.LLM_MAX_TOKENS, 2048),
            timeout=self._timeout(180),
            num_ctx=self._context_window(),
        )
        if isinstance(result, str):
            result = json.loads(result)
        return StructureDiscoveryResult.model_validate(result), usage

    async def populate_collection(
        self,
        proposal: OntologyObservation,
        chapter_num: int,
        chapter_text: str,
    ) -> tuple[CollectionExtraction, LlmUsage]:
        if proposal.kind != "collection":
            raise ValueError("populate_collection requires a collection proposal")
        definition = {
            "label": proposal.label,
            "description": proposal.description,
            "parent_core_type": proposal.parent_core_type,
            "expected_count": proposal.expected_count,
            "fields": [field.model_dump() for field in proposal.fields],
            "evidence": proposal.evidence,
        }
        prompt = (
            "## 集合定义\n"
            f"{json.dumps(definition, ensure_ascii=False, indent=2)}\n\n"
            f"## source_chapter\n{chapter_num}\n\n"
            f"## 本章原文\n{chapter_text}"
        )
        result, usage = await self.llm.generate(
            system=self.collection_system,
            prompt=prompt,
            format=COLLECTION_SCHEMA,
            temperature=0.0,
            max_tokens=_cfg.LLM_MAX_TOKENS,
            timeout=self._timeout(300),
            num_ctx=self._context_window(),
        )
        if isinstance(result, str):
            result = json.loads(result)
        extraction = CollectionExtraction.model_validate(result)
        # Provider responses occasionally echo a wrong chapter id. The caller's
        # chapter id is authoritative provenance.
        extraction.source_chapter = chapter_num
        return extraction, usage
