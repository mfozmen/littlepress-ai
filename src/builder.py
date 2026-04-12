from pathlib import Path
from reportlab.pdfgen.canvas import Canvas

from .config import PAGE_SIZE
from .fonts import register_fonts
from .pages import draw_cover, draw_page, draw_back_cover, draw_blank
from .schema import Book


def build_pdf(book: Book, output_path: Path) -> None:
    register_fonts()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    c = Canvas(str(output_path), pagesize=PAGE_SIZE)
    c.setTitle(book.title)
    if book.author:
        c.setAuthor(book.author)

    draw_cover(c, book)
    draw_blank(c)  # inside-front cover left blank

    for i, page in enumerate(book.pages, start=1):
        draw_page(c, page, book.source_dir, number=i)

    # ensure even page count before the back cover
    total = 2 + len(book.pages) + 1
    if total % 2:
        draw_blank(c)

    draw_back_cover(c, book)
    c.save()
