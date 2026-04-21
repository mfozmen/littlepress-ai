"""Tests for the ``/prune`` slash command."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from src.providers.llm import find
from src.repl import Repl


def _scripted(lines):
    it = iter(lines)

    def read():
        try:
            return next(it)
        except StopIteration as e:
            raise EOFError from e

    return read


def _make(tmp_path, lines):
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    repl = Repl(
        read_line=_scripted(lines),
        console=console,
        provider=find("none"),
        session_root=tmp_path,
    )
    return repl, buf


def _touch(path: Path, size: int = 8) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_prune_without_draft_tells_user_to_load(tmp_path):
    repl, buf = _make(tmp_path, ["/prune", "/exit"])
    repl.run()

    assert "/load" in buf.getvalue().lower()


def test_prune_removes_orphans_and_old_snapshots(tmp_path):
    from src.draft import Draft, DraftPage

    images = tmp_path / ".book-gen" / "images"
    out = tmp_path / ".book-gen" / "output"
    cover = _touch(images / "cover.png")
    orphan = _touch(images / "cover-retry.png")
    v1 = _touch(out / "book.v1.pdf")
    v2 = _touch(out / "book.v2.pdf")
    v3 = _touch(out / "book.v3.pdf")
    v4 = _touch(out / "book.v4.pdf")
    _touch(out / "book.pdf")  # stable pointer

    repl, buf = _make(tmp_path, ["/prune", "/exit"])
    repl._draft = Draft(
        source_pdf=tmp_path / "draft.pdf",
        title="Book",
        cover_image=cover,
        pages=[DraftPage(text="hi", image=None)],
    )
    repl.run()

    # Default keep=3 — v1 dropped, v2/v3/v4 kept.
    assert not v1.exists()
    assert v2.exists() and v3.exists() and v4.exists()
    # Orphan image pruned.
    assert not orphan.exists()
    assert cover.exists()
    # User sees what was removed.
    assert "1" in buf.getvalue() and "orphan" in buf.getvalue().lower()


def test_prune_dry_run_keeps_files(tmp_path):
    from src.draft import Draft

    images = tmp_path / ".book-gen" / "images"
    orphan = _touch(images / "leftover.png")

    repl, buf = _make(tmp_path, ["/prune --dry-run", "/exit"])
    repl._draft = Draft(source_pdf=tmp_path / "draft.pdf", title="Book")
    repl.run()

    # Nothing actually removed.
    assert orphan.exists()
    # But the user sees a preview.
    output = buf.getvalue().lower()
    assert "dry" in output or "would" in output


def test_prune_honours_keep_flag(tmp_path):
    from src.draft import Draft

    out = tmp_path / ".book-gen" / "output"
    v1 = _touch(out / "book.v1.pdf")
    v2 = _touch(out / "book.v2.pdf")
    v3 = _touch(out / "book.v3.pdf")

    repl, _ = _make(tmp_path, ["/prune --keep 1", "/exit"])
    repl._draft = Draft(source_pdf=tmp_path / "draft.pdf", title="Book")
    repl.run()

    # Only v3 survives.
    assert not v1.exists()
    assert not v2.exists()
    assert v3.exists()


def test_prune_noop_says_nothing_to_remove(tmp_path):
    from src.draft import Draft

    repl, buf = _make(tmp_path, ["/prune", "/exit"])
    repl._draft = Draft(source_pdf=tmp_path / "draft.pdf", title="Book")
    repl.run()

    output = buf.getvalue().lower()
    assert "nothing" in output or "clean" in output or "no" in output
