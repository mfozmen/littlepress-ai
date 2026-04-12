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
