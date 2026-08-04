#!/usr/bin/env python3
"""Build the held-out 108-member benchmark from Wikisource chapter 71.

This script is deliberately separate from the ontology prompts and runtime
pipeline. The generated JSON is loaded only after model inference for scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from src.utils.chapter_splitter import split_chapters_ex


ENTRY_PATTERN = re.compile(
    r"([天地][\u3400-\u9fff]{1,3}星)"
    r"「([^」]+)」[　 \t]*([\u3400-\u9fff]{2,4})"
)


def build(corpus_path: Path) -> dict:
    source = corpus_path.read_text(encoding="utf-8")
    split = split_chapters_ex(source)
    if len(split.chapters) != 120:
        raise RuntimeError(f"Expected 120 chapters, got {len(split.chapters)}")
    chapter = split.chapters[70]
    start_marker = "石碣前面，書梁山泊天罡星三十六員："
    end_marker = "當時何道士辯驗天書"
    block = chapter.content[
        chapter.content.index(start_marker) : chapter.content.index(end_marker)
    ]
    matches = ENTRY_PATTERN.findall(block)
    if len(matches) != 108:
        raise RuntimeError(f"Expected 108 roster entries, got {len(matches)}")
    names = [name for _star, _nickname, name in matches]
    if len(set(names)) != 108:
        raise RuntimeError("Roster contains duplicate canonical names")

    members = []
    for rank, (star, nickname, name) in enumerate(matches, start=1):
        members.append(
            {
                "rank": rank,
                "group": "天罡" if rank <= 36 else "地煞",
                "star": star,
                "nickname": nickname,
                "name": name,
            }
        )
    return {
        "_meta": {
            "benchmark": "water-margin-108-roster",
            "source_file": corpus_path.name,
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "source_chapter": 71,
            "source_chapter_title": chapter.title,
            "expected_count": 108,
            "prompt_contamination_policy": "gold is loaded only after inference",
        },
        "members": members,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gold = build(args.corpus)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(gold, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
