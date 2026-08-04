"""Restore model-normalized tokens to the exact form used in source text."""

from __future__ import annotations

from dataclasses import dataclass

from src.models.book_ontology import CollectionExtraction

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - optional fallback for minimal installs
    OpenCC = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class CanonicalizationReport:
    corrected_names: int = 0
    corrected_values: int = 0
    unresolved_names: tuple[str, ...] = ()


class SourceTextCanonicalizer:
    """Map simplified/traditional model output back to a unique source token.

    No gold data or external knowledge is used. A correction is accepted only
    when all occurrences in the normalized source resolve to one exact original
    spelling of the same length. Ambiguous tokens remain untouched for review.
    """

    def __init__(self) -> None:
        self._to_simplified = OpenCC("t2s") if OpenCC is not None else None

    def canonicalize_token(self, token: str, source_text: str) -> str | None:
        token = token.strip()
        if not token:
            return None
        if token in source_text:
            return token
        if self._to_simplified is None:
            return None

        simplified_source = self._to_simplified.convert(source_text)
        simplified_token = self._to_simplified.convert(token)
        if len(simplified_source) != len(source_text):
            return None

        candidates: set[str] = set()
        start = 0
        while True:
            index = simplified_source.find(simplified_token, start)
            if index < 0:
                break
            candidate = source_text[index : index + len(simplified_token)]
            if self._to_simplified.convert(candidate) == simplified_token:
                candidates.add(candidate)
            start = index + 1
        if len(candidates) == 1:
            return next(iter(candidates))
        return None

    def canonicalize_collection(
        self, extraction: CollectionExtraction, source_text: str
    ) -> tuple[CollectionExtraction, CanonicalizationReport]:
        corrected_names = 0
        corrected_values = 0
        unresolved: list[str] = []
        members = []
        for member in extraction.members:
            exact_name = self.canonicalize_token(member.entity_name, source_text)
            if exact_name is None:
                exact_name = member.entity_name
                if member.entity_name not in source_text:
                    unresolved.append(member.entity_name)
            elif exact_name != member.entity_name:
                corrected_names += 1

            values = []
            for value in member.values:
                exact_value = self.canonicalize_token(value.value, source_text)
                if exact_value and exact_value != value.value:
                    corrected_values += 1
                values.append(
                    value.model_copy(update={"value": exact_value or value.value})
                )
            members.append(
                member.model_copy(
                    update={"entity_name": exact_name, "values": values}
                )
            )
        return (
            extraction.model_copy(update={"members": members}),
            CanonicalizationReport(
                corrected_names=corrected_names,
                corrected_values=corrected_values,
                unresolved_names=tuple(sorted(set(unresolved))),
            ),
        )
