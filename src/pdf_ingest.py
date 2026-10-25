"""Ingest a PDF draft (scanned handwriting + drawings) into book.json + images/.

This module is the ingestion layer for the dynamic pipeline. It MUST preserve
the child's original text — no silent cleaning, rewriting, or "polishing".
See .claude/skills/preserve-child-voice/SKILL.md for the full contract.
"""

from pathlib import Path

from pypdf import PdfReader


def extract_pages(pdf_path: Path, *, reader: PdfReader | None = None) -> list[str]:
    """Return the raw text of each page, in order, with no transformations.

    Callers who also need ``extract_images`` on the same PDF should open
    a single ``PdfReader`` and pass it to both functions to avoid parsing
    the file twice.
    """
    reader = reader or PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def extract_images(
    pdf_path: Path, out_dir: Path, *, reader: PdfReader | None = None
) -> list[Path | None]:
    """Extract the first embedded image of each PDF page into out_dir.

    Returns one entry per page: a Path to the saved image, or None when the
    page has no embedded image. Pages with multiple images keep only the
    first one that isn't a blank single-tone background.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    reader = reader or PdfReader(str(pdf_path))
    results: list[Path | None] = []
    for i, page in enumerate(reader.pages, start=1):
        images = list(page.images)
        if not images:
            results.append(None)
            continue
        src = _first_illustration(images)
        ext = _extension_for(src)
        dest = out_dir / f"page-{i:02d}.{ext}"
        dest.write_bytes(src.data)
        results.append(dest)
    return results


def _first_illustration(images: list):
    """Skip full-page "paper" backgrounds some drafts lay under the real
    picture; a lone image is kept even if blank."""
    if len(images) == 1:
        return images[0]
    return next((im for im in images if not _is_blank(im.image)), images[0])


def _is_blank(image) -> bool:
    low, high = image.convert("L").getextrema()
    # Small spread tolerates JPEG noise on a flat background.
    return high - low <= 8


def _extension_for(image_file) -> str:
    """Pick a file extension that matches the raw bytes, not the PDF's label.

    pypdf's ``image_file.name`` can be extensionless on some PDFs (e.g. ``Im0``),
    which would make a ``.name``-based suffix silently save JPEG bytes under
    ``.png``. Derive from PIL's detected format instead.
    """
    fmt = (getattr(image_file.image, "format", None) or "PNG").lower()
    return "jpg" if fmt == "jpeg" else fmt
