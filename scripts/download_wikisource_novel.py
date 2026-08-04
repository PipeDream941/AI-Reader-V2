#!/usr/bin/env python3
"""Download a chaptered public-domain novel from Wikisource.

The downloader uses the MediaWiki API instead of scraping rendered pages.  It
keeps the page revision id for every chapter and writes a manifest beside the
combined TXT file so a benchmark can always be traced back to its exact source.

Example (Water Margin, Yuan Wuya 120-chapter edition)::

    python scripts/download_wikisource_novel.py \
      --page-prefix '水滸傳 (120回本)' \
      --book-title '水滸傳（袁無涯一百二十回本）' \
      --chapters 120 \
      --output-dir ../corpora/shuihuzhuan-120
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from datetime import datetime, timezone
from pathlib import Path


API_URL = "https://zh.wikisource.org/w/api.php"
USER_AGENT = "AI-Reader-V2 public-domain benchmark downloader/1.0"


def _fetch_json(
    params: dict[str, str | int], retries: int = 5, *, post: bool = False
) -> dict:
    query = urllib.parse.urlencode(
        {**params, "format": "json", "formatversion": 2}
    ).encode("utf-8")
    request = urllib.request.Request(
        API_URL if post else f"{API_URL}?{query.decode('utf-8')}",
        data=query if post else None,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception as exc:  # pragma: no cover - network failure path
            last_error = exc
            if attempt + 1 < retries:
                retry_after = 0.0
                if isinstance(exc, HTTPError) and exc.code == 429:
                    try:
                        retry_after = float(exc.headers.get("Retry-After", "0"))
                    except (TypeError, ValueError):
                        retry_after = 0.0
                time.sleep(max(retry_after, min(40.0, 5.0 * (2**attempt))))
    assert last_error is not None
    raise last_error


def _replace_template(match: re.Match[str]) -> str:
    """Keep visible text from the small inline templates used in the source."""
    parts = [part.strip() for part in match.group(1).split("|")]
    if not parts:
        return ""
    name, *args = parts
    if not args:
        return ""
    if name in {"另", "異體字", "lang", "Lang"}:
        return args[-1]
    if name in {"Ruby", "ruby", "注音"}:
        return args[0]
    # Unknown formatting templates usually place the visible value last.
    return args[-1]


def _plain_wikitext(wikitext: str) -> tuple[str, str]:
    """Return ``(chapter_title, readable_text)`` from a chapter wikitext page."""
    title = ""
    novel_match = re.search(r"\{\{Novel\|[^|]*\|([^|}]*)", wikitext)
    if novel_match:
        title = novel_match.group(1).strip()

    text = re.sub(r"\{\{Novel\|.*?\}\}", "", wikitext, count=1, flags=re.DOTALL)
    text = text.replace("__TOC__", "")
    text = re.sub(r"</?(?:onlyinclude|poem|center|div|span)[^>]*>", "", text)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref\b[^>]*/>", "", text)

    # Resolve non-nested inline templates. Repeating handles adjacent/simple
    # nested templates without pretending to be a complete wikitext parser.
    template_pattern = re.compile(r"\{\{([^{}]*)\}\}")
    for _ in range(5):
        updated = template_pattern.sub(_replace_template, text)
        if updated == text:
            break
        text = updated

    text = re.sub(r"\[\[[^\]|]*\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text


def _page_title(prefix: str, chapter: int) -> str:
    return f"{prefix}/第{chapter:03d}回"


def download(
    page_prefix: str,
    book_title: str,
    chapters: int,
    output_dir: Path,
    delay_seconds: float = 0.05,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    chapter_records: list[dict] = []
    combined: list[str] = []

    requested_pages = [_page_title(page_prefix, chapter) for chapter in range(1, chapters + 1)]
    page_payloads: dict[str, dict] = {}
    # MediaWiki accepts up to 50 titles for normal clients. Three batched POSTs
    # are both faster and much kinder to the service than 120 parse requests.
    for start in range(0, len(requested_pages), 50):
        batch = requested_pages[start : start + 50]
        payload = _fetch_json(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "ids|content",
                "rvslots": "main",
                "titles": "|".join(batch),
                "redirects": 1,
                "maxlag": 5,
            },
            post=True,
        )
        if "error" in payload:
            raise RuntimeError(f"Wikisource API error: {payload['error']}")
        for page_data in payload.get("query", {}).get("pages", []):
            page_payloads[page_data["title"]] = page_data
        if delay_seconds and start + 50 < len(requested_pages):
            time.sleep(max(delay_seconds, 0.5))

    for chapter, page in enumerate(requested_pages, start=1):
        page_data = page_payloads.get(page)
        if not page_data or page_data.get("missing"):
            raise RuntimeError(f"Missing Wikisource chapter: {page}")
        revision = page_data["revisions"][0]
        wikitext = revision["slots"]["main"]["content"]
        chapter_title, content = _plain_wikitext(wikitext)
        if not content:
            raise RuntimeError(f"Empty chapter content: {page}")

        heading = chapter_title or f"第{chapter:03d}回"
        combined.append(f"{heading}\n\n{content}")
        chapter_records.append(
            {
                "chapter": chapter,
                "page": page,
                "revision_id": revision.get("revid"),
                "title": heading,
                "characters": len(content),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    book_text = "\n\n".join(combined) + "\n"
    text_path = output_dir / f"{book_title}.txt"
    text_path.write_text(book_text, encoding="utf-8")

    manifest = {
        "book_title": book_title,
        "edition": page_prefix,
        "source": f"https://zh.wikisource.org/wiki/{urllib.parse.quote(page_prefix)}",
        "source_api": API_URL,
        "license": "Public domain source text; Wikisource contributions CC BY-SA 4.0",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "chapter_count": len(chapter_records),
        "characters": len(book_text),
        "sha256": hashlib.sha256(book_text.encode("utf-8")).hexdigest(),
        "chapters": chapter_records,
    }
    manifest_path = output_dir / "source-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return text_path, manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-prefix", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--chapters", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--delay-seconds", type=float, default=0.05)
    args = parser.parse_args()

    text_path, manifest_path = download(
        page_prefix=args.page_prefix,
        book_title=args.book_title,
        chapters=args.chapters,
        output_dir=args.output_dir,
        delay_seconds=max(0.0, args.delay_seconds),
    )
    print(text_path)
    print(manifest_path)


if __name__ == "__main__":
    main()
