#!/usr/bin/env python3
"""Run a held-out adaptive-ontology benchmark against one structure-rich chapter."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from src.infra import config
from src.infra.llm_client import LlmUsage
from src.models.book_ontology import BookOntology, OntologyObservation
from src.services.book_ontology_agent import BookOntologyAgent, ChapterStructureScout
from src.services.book_ontology_controller import BookOntologyController
from src.services.source_text_canonicalizer import SourceTextCanonicalizer
from src.utils.chapter_splitter import split_chapters_ex


def _value_for(
    member,
    proposal,
    *,
    key_hints: tuple[str, ...],
    label_fragments: tuple[str, ...],
) -> str:
    # Prefer semantic keys: a generic label fragment such as “号” also appears
    # in “星宿名号” and would otherwise misclassify the star as a nickname.
    keys = {
        field.key
        for field in proposal.fields
        if any(hint in field.key.lower() for hint in key_hints)
    }
    if not keys:
        keys = {
            field.key
            for field in proposal.fields
            if any(fragment in field.label for fragment in label_fragments)
        }
    for value in member.values:
        if value.key in keys:
            return value.value
    return ""


def _ratio(correct: int, total: int) -> float:
    return round(correct / total, 4) if total else 0.0


def score(proposal, extraction, review, gold: dict) -> dict:
    expected = gold["members"]
    expected_by_name = {item["name"]: item for item in expected}
    predicted_by_name = {item.entity_name: item for item in extraction.members}
    expected_names = set(expected_by_name)
    predicted_names = set(predicted_by_name)
    true_names = expected_names & predicted_names
    precision = _ratio(len(true_names), len(predicted_names))
    recall = _ratio(len(true_names), len(expected_names))
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0

    ordered_names = [item.entity_name for item in extraction.members]
    ordered_correct = sum(
        actual == expected_item["name"]
        for actual, expected_item in zip(ordered_names, expected)
    )
    star_correct = 0
    nickname_correct = 0
    attr_total = 0
    for name in true_names:
        predicted = predicted_by_name[name]
        expected_item = expected_by_name[name]
        star = _value_for(
            predicted,
            proposal,
            key_hints=("star_designation", "star_name"),
            label_fragments=("星宿名", "星名"),
        )
        nickname = _value_for(
            predicted,
            proposal,
            key_hints=("nickname", "epithet"),
            label_fragments=("绰", "綽"),
        )
        if star:
            star_correct += star == expected_item["star"]
        if nickname:
            nickname_correct += nickname == expected_item["nickname"]
        attr_total += 1

    metrics = {
        "expected_count_detected": proposal.expected_count == 108,
        "unique_member_count": len(predicted_names),
        "name_precision": precision,
        "name_recall": recall,
        "name_f1": f1,
        "ordered_name_accuracy": _ratio(ordered_correct, 108),
        "star_accuracy": _ratio(star_correct, attr_total),
        "nickname_accuracy": _ratio(nickname_correct, attr_total),
        "missing_names": sorted(expected_names - predicted_names),
        "extra_names": sorted(predicted_names - expected_names),
        "review_decision": review.decision,
    }
    metrics["pass"] = all(
        [
            metrics["expected_count_detected"],
            metrics["unique_member_count"] == 108,
            precision == 1.0,
            recall == 1.0,
            metrics["ordered_name_accuracy"] == 1.0,
            metrics["star_accuracy"] == 1.0,
            metrics["nickname_accuracy"] == 1.0,
            review.decision == "activate",
        ]
    )
    return metrics


async def run(args) -> dict:
    if args.provider == "codex":
        config.CODEX_REASONING_EFFORT = args.reasoning_effort
        config.CODEX_MODEL = args.model
        config.switch_to_codex()
    source = args.corpus.read_text(encoding="utf-8")
    chapters = split_chapters_ex(source).chapters
    chapter = chapters[args.chapter - 1]
    scout = ChapterStructureScout().inspect(chapter.content)
    agent = BookOntologyAgent()
    if args.reuse_proposal_report:
        previous = json.loads(args.reuse_proposal_report.read_text(encoding="utf-8"))
        proposal = OntologyObservation.model_validate(previous["proposal"])
        discovery_usage = LlmUsage()
        print(
            f"[1/2] reusing held-out discovery proposal: {proposal.label}",
            file=sys.stderr,
            flush=True,
        )
    else:
        print(
            f"[1/2] discovering structure in chapter {args.chapter} "
            f"(scout score={scout.score}, "
            f"excerpt chars={len(ChapterStructureScout().discovery_excerpt(chapter.content))})",
            file=sys.stderr,
            flush=True,
        )
        discovery, discovery_usage = await agent.discover(
            BookOntology(novel_id="benchmark"),
            args.chapter,
            chapter.content,
            scout,
        )
        collections = [item for item in discovery.proposals if item.kind == "collection"]
        if not collections:
            return {
                "pass": False,
                "stage": "discovery",
                "error": "No collection proposal discovered",
                "scout": {"score": scout.score, "reasons": list(scout.reasons)},
                "discovery": discovery.model_dump(),
            }
        proposal = max(collections, key=lambda item: item.expected_count or 0)
    print(
        f"[2/2] populating collection: {proposal.label} "
        f"(expected={proposal.expected_count})",
        file=sys.stderr,
        flush=True,
    )
    extraction, extraction_usage = await agent.populate_collection(
        proposal, args.chapter, chapter.content
    )
    extraction, canonicalization = SourceTextCanonicalizer().canonicalize_collection(
        extraction, chapter.content
    )
    review = BookOntologyController.review_collection(
        proposal, extraction, chapter.content
    )
    # Gold is intentionally loaded only after both inference calls finish.
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if source_hash != gold["_meta"]["source_sha256"]:
        raise RuntimeError("Corpus does not match the held-out gold source hash")
    metrics = score(proposal, extraction, review, gold)
    print(
        f"[done] members={metrics['unique_member_count']} "
        f"recall={metrics['name_recall']} pass={metrics['pass']}",
        file=sys.stderr,
        flush=True,
    )
    return {
        "benchmark": gold["_meta"]["benchmark"],
        "provider": args.provider,
        "model": args.model or "provider-default",
        "reasoning_effort": args.reasoning_effort,
        "chapter": args.chapter,
        "scout": {"score": scout.score, "reasons": list(scout.reasons)},
        "proposal": proposal.model_dump(),
        "review": review.model_dump(),
        "canonicalization": {
            "corrected_names": canonicalization.corrected_names,
            "corrected_values": canonicalization.corrected_values,
            "unresolved_names": list(canonicalization.unresolved_names),
        },
        "extraction": extraction.model_dump(),
        "metrics": metrics,
        "usage": {
            "prompt_tokens": discovery_usage.prompt_tokens + extraction_usage.prompt_tokens,
            "completion_tokens": discovery_usage.completion_tokens + extraction_usage.completion_tokens,
            "total_tokens": discovery_usage.total_tokens + extraction_usage.total_tokens,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chapter", type=int, default=71)
    parser.add_argument("--provider", choices=["codex", "configured"], default="codex")
    parser.add_argument("--model", default="")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument(
        "--reuse-proposal-report",
        type=Path,
        help="Reuse the proposal from an earlier report and skip discovery inference",
    )
    args = parser.parse_args()
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
