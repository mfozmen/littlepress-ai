import json

import pytest

from src.schema import load_book


def _write_book(tmp_path, data):
    path = tmp_path / "book.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_rejects_empty_title(tmp_path):
    path = _write_book(tmp_path, {"title": "", "pages": []})

    with pytest.raises(ValueError, match="title"):
        load_book(path)


def test_rejects_missing_title(tmp_path):
    path = _write_book(tmp_path, {"pages": []})

    with pytest.raises(ValueError, match="title"):
        load_book(path)


def test_loads_minimal_book_with_defaults(tmp_path):
    path = _write_book(tmp_path, {"title": "My Book"})

    book = load_book(path)

    assert book.title == "My Book"
    assert book.author == ""
    assert book.pages == []
    assert book.cover.image is None
    assert book.cover.subtitle == ""
    assert book.back_cover.text == ""


def test_page_layout_defaults_to_image_top(tmp_path):
    path = _write_book(tmp_path, {
        "title": "T",
        "pages": [{"text": "hello"}],
    })

    book = load_book(path)

    assert book.pages[0].layout == "image-top"
    assert book.pages[0].text == "hello"
    assert book.pages[0].image is None


def test_rejects_invalid_layout(tmp_path):
    path = _write_book(tmp_path, {
        "title": "T",
        "pages": [{"text": "x", "layout": "spiral"}],
    })

    with pytest.raises(ValueError, match="invalid layout"):
        load_book(path)


def test_rejects_missing_image_file(tmp_path):
    path = _write_book(tmp_path, {
        "title": "T",
        "pages": [{"text": "x", "image": "images/ghost.png"}],
    })

    with pytest.raises(FileNotFoundError, match="ghost.png"):
        load_book(path)
