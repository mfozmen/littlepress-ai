"""Project memory: persist the Draft so the agent doesn't re-ask each launch."""

from pathlib import Path

from src import memory
from src.draft import Draft, DraftPage


def _make_draft(tmp_path):
    img = tmp_path / "images" / "page-01.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    return Draft(
        source_pdf=tmp_path / "draft.pdf",
        title="The Brave Owl",
        author="Yusuf",
        cover_image=img,
        cover_subtitle="  written by a kid  ",
        back_cover_text="\nthe end!\n",
        pages=[
            DraftPage(text="once upon a time", image=img, layout="image-top"),
            DraftPage(text="the end", image=None, layout="text-only"),
        ],
    )


def test_save_then_load_roundtrips_every_field(tmp_path):
    draft = _make_draft(tmp_path)

    memory.save_draft(tmp_path, draft)
    restored = memory.load_draft(tmp_path)

    assert restored is not None
    assert restored.source_pdf == draft.source_pdf
    assert restored.title == draft.title
    assert restored.author == draft.author
    assert restored.cover_image == draft.cover_image
    # preserve-child-voice: whitespace on cover_subtitle / back_cover_text
    # is child-voice content and must round-trip verbatim.
    assert restored.cover_subtitle == draft.cover_subtitle
    assert restored.back_cover_text == draft.back_cover_text
    assert len(restored.pages) == len(draft.pages)
    for a, b in zip(restored.pages, draft.pages):
        assert a.text == b.text
        assert a.image == b.image
        assert a.layout == b.layout


def test_load_missing_memory_returns_none(tmp_path):
    assert memory.load_draft(tmp_path) is None


def test_load_corrupt_memory_returns_none(tmp_path):
    target = memory.path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")

    assert memory.load_draft(tmp_path) is None


def test_save_overwrites_previous_memory(tmp_path):
    draft = _make_draft(tmp_path)
    memory.save_draft(tmp_path, draft)

    draft.title = "Changed Title"
    memory.save_draft(tmp_path, draft)

    restored = memory.load_draft(tmp_path)
    assert restored.title == "Changed Title"


def test_save_is_atomic_no_partial_file_left_behind(tmp_path):
    draft = _make_draft(tmp_path)

    memory.save_draft(tmp_path, draft)

    book_dir = tmp_path / ".book-gen"
    entries = sorted(p.name for p in book_dir.iterdir())
    # Only draft.json (and whatever sibling files this test creates); no
    # stray .session.*.tmp or similar.
    assert "draft.json" in entries
    assert all(not e.endswith(".tmp") for e in entries)


def test_load_rejects_memory_for_a_different_pdf(tmp_path):
    """A memory written for drafts/A.pdf must not be applied when the
    user launches with drafts/B.pdf."""
    draft = _make_draft(tmp_path)
    memory.save_draft(tmp_path, draft)

    # Same .book-gen directory but query for a different source PDF.
    other_pdf = tmp_path / "unrelated.pdf"
    assert memory.load_draft(tmp_path, expected_source=other_pdf) is None


def test_load_accepts_memory_matching_expected_source(tmp_path):
    draft = _make_draft(tmp_path)
    memory.save_draft(tmp_path, draft)

    restored = memory.load_draft(tmp_path, expected_source=draft.source_pdf)
    assert restored is not None
    assert restored.title == draft.title


def test_save_handles_draft_without_cover_image(tmp_path):
    draft = Draft(
        source_pdf=tmp_path / "d.pdf",
        title="No Cover",
        pages=[DraftPage(text="hi")],
    )

    memory.save_draft(tmp_path, draft)
    restored = memory.load_draft(tmp_path)

    assert restored.cover_image is None
    assert restored.pages[0].image is None


def test_save_cleans_up_tmp_file_when_serialization_fails(tmp_path, monkeypatch):
    """Atomic write must not leave stray tmp files on failure."""
    import pytest

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(memory.json, "dump", boom)

    with pytest.raises(RuntimeError):
        memory.save_draft(tmp_path, _make_draft(tmp_path))

    book_dir = tmp_path / ".book-gen"
    if book_dir.exists():
        assert [p.name for p in book_dir.iterdir()] == []


def test_load_ignores_non_object_json(tmp_path):
    target = memory.path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('["not", "an", "object"]', encoding="utf-8")

    assert memory.load_draft(tmp_path) is None


def test_load_returns_none_when_fields_have_wrong_types(tmp_path):
    """An otherwise-parseable file but with wrong shapes (e.g. pages not
    a list) falls back to None instead of crashing the REPL."""
    target = memory.path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Missing source_pdf — _from_dict will KeyError.
    target.write_text('{"title": "x"}', encoding="utf-8")

    assert memory.load_draft(tmp_path) is None
