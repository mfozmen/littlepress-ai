"""Per-project memory — persist the Draft between launches.

Writes ``.book-gen/draft.json`` after every agent turn so that running
``child-book-generator same-draft.pdf`` again picks up where the last
session left off: title/author/cover/per-page layouts/already-approved
typo fixes all survive. The idea is the agent "doesn't learn the book
from scratch every time" (per the pivot plan in ``docs/PLAN.md``).

Atomic write via ``tempfile`` + ``os.replace`` so a crash mid-write can't
leave a half-written file behind. Corrupt or missing files degrade to
``load_draft() -> None`` so the REPL can fall back to a fresh ingest.

Preserve-child-voice: the child's page text, cover subtitle, and
back-cover text round-trip verbatim through JSON.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from src.draft import Draft, DraftPage

MEMORY_DIR = ".book-gen"
MEMORY_FILE = "draft.json"


def path(root: Path) -> Path:
    return Path(root) / MEMORY_DIR / MEMORY_FILE


def save_draft(root: Path, draft: Draft) -> None:
    """Atomically write ``draft`` to ``root/.book-gen/draft.json``."""
    target = path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".draft.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_to_dict(draft), f, indent=2)
        os.replace(tmp_name, target)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def load_draft(
    root: Path, *, expected_source: Path | None = None
) -> Draft | None:
    """Read the saved draft under ``root``, or ``None`` if missing / corrupt.

    If ``expected_source`` is given and the memory's ``source_pdf`` doesn't
    match, returns ``None`` so the REPL doesn't silently apply one book's
    metadata to another.
    """
    target = path(root)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        draft = _from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None
    if expected_source is not None and draft.source_pdf != Path(expected_source):
        return None
    return draft


def _to_dict(draft: Draft) -> dict:
    return {
        "source_pdf": str(draft.source_pdf),
        "title": draft.title,
        "author": draft.author,
        "cover_image": str(draft.cover_image) if draft.cover_image else None,
        "cover_subtitle": draft.cover_subtitle,
        "back_cover_text": draft.back_cover_text,
        "pages": [
            {
                "text": p.text,
                "image": str(p.image) if p.image else None,
                "layout": p.layout,
            }
            for p in draft.pages
        ],
    }


def _from_dict(data: dict) -> Draft:
    pages = [
        DraftPage(
            text=p.get("text", ""),
            image=Path(p["image"]) if p.get("image") else None,
            layout=p.get("layout", "image-top"),
        )
        for p in data.get("pages", [])
    ]
    cover = data.get("cover_image")
    return Draft(
        source_pdf=Path(data["source_pdf"]),
        title=data.get("title", ""),
        author=data.get("author", ""),
        cover_image=Path(cover) if cover else None,
        cover_subtitle=data.get("cover_subtitle", ""),
        back_cover_text=data.get("back_cover_text", ""),
        pages=pages,
    )
