from io import BytesIO

from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas

from src.pdf_ingest import extract_pages


def _write_pdf(tmp_path, per_page_texts):
    path = tmp_path / "draft.pdf"
    c = canvas.Canvas(str(path), pagesize=A5)
    for text in per_page_texts:
        c.setFont("Helvetica", 14)
        c.drawString(50, 400, text)
        c.showPage()
    c.save()
    return path


def test_extracts_raw_text_per_page_without_transforming(tmp_path):
    expected = ["Once upon a time,", "the little owl flew away."]
    pdf_path = _write_pdf(tmp_path, expected)

    pages = extract_pages(pdf_path)

    assert [p.strip() for p in pages] == expected


def test_empty_pdf_returns_empty_list(tmp_path):
    pdf_path = _write_pdf(tmp_path, [])

    assert extract_pages(pdf_path) == []


def test_preserves_child_voice_verbatim(tmp_path):
    """Typos, invented spellings, and odd grammar must pass through untouched.

    This is the core contract: we never silently fix the child's text.
    """
    quirky = ["the dragn he was sad", "BOOOM went the ship"]
    pdf_path = _write_pdf(tmp_path, quirky)

    pages = [p.strip() for p in extract_pages(pdf_path)]

    assert pages == quirky
