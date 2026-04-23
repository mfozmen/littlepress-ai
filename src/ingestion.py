"""Deterministic ingestion — runs OCR + sentinel classification on
every image-only page *before* the agent gets a turn. Pure Python;
the LLM is called directly, not through the agent's tool-use loop.
This removes the surface where earlier versions of the tool printed
a per-page y/n confirm UI that Claude / GPT kept reconstructing from
training memory regardless of greeting prompts.

Contract: idempotent. Called on every ``littlepress`` launch (fresh
load or memory-restored). Pages whose text is already populated, or
whose ``hidden`` flag is set, are skipped — a re-ingest is free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.draft import Draft


@dataclass
class IngestReport:
    """What ``ingest_image_only_pages`` did."""

    text_pages: list[int] = field(default_factory=list)
    mixed_pages: list[int] = field(default_factory=list)
    blank_pages: list[int] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return len(self.text_pages) + len(self.mixed_pages) + len(self.blank_pages)


def ingest_image_only_pages(
    draft: Draft,
    llm_provider: Any,
    console: Any,
) -> IngestReport:
    """OCR every image-only, non-hidden page in ``draft``; apply the
    sentinel outcome; return a summary. Mutates ``draft`` in place.

    This task's body is a stub — Task 3 implements the loop.
    """
    return IngestReport()
