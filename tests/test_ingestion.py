"""Deterministic ingestion — OCR + sentinel classification runs
between ``from_pdf`` and the first agent turn. The LLM does the
vision work but from a pure Python caller; no agent tool-use loop,
no chance for the model to reconstruct the old confirm UI from
training memory.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from rich.console import Console

from src.draft import Draft, DraftPage


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100, no_color=True)


def _tiny_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 40), (10, 20, 30)).save(path)
    return path


class _ScriptedLLM:
    """Pops the next canned reply on each ``chat`` call. Mirrors the
    shape used by ``tests/test_agent_tools.py`` and
    ``tests/test_review_loop.py``."""

    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": dict(kwargs)})
        return self._replies.pop(0)


def test_ingest_empty_draft_returns_empty_report(tmp_path):
    from src.ingestion import ingest_image_only_pages, IngestReport

    draft = Draft(source_pdf=tmp_path / "x.pdf", pages=[])
    llm = _ScriptedLLM([])

    report = ingest_image_only_pages(draft, llm, _console())

    assert isinstance(report, IngestReport)
    assert report.text_pages == []
    assert report.mixed_pages == []
    assert report.blank_pages == []
    assert report.errors == []
    assert llm.calls == []
