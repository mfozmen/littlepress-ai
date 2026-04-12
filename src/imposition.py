"""Imposes A5 pages onto A4 sheets in saddle-stitch booklet order (2-up)."""
from pathlib import Path
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import A4, landscape
from pypdf import PdfReader  # optional; fallback below


def _booklet_order(n_pages: int) -> list[int | None]:
    pad = (4 - n_pages % 4) % 4
    total = n_pages + pad
    pages: list[int | None] = list(range(1, n_pages + 1)) + [None] * pad

    order: list[int | None] = []
    left = 0
    right = total - 1
    while left < right:
        order.append(pages[right]); order.append(pages[left])
        left += 1; right -= 1
        order.append(pages[left]); order.append(pages[right])
        left += 1; right -= 1
    return order


def impose_a5_to_a4(src_pdf: Path, dst_pdf: Path) -> None:
    from pypdf import PdfReader
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.pagesizes import A4
    import pypdf

    reader = PdfReader(str(src_pdf))
    n = len(reader.pages)
    order = _booklet_order(n)

    # Use pypdf to merge 2-up onto A4 landscape
    from pypdf import PdfWriter, Transformation, PageObject
    writer = PdfWriter()
    A4_W, A4_H = A4  # portrait
    sheet_w, sheet_h = A4_H, A4_W  # landscape
    half = sheet_w / 2

    i = 0
    while i < len(order):
        sheet = PageObject.create_blank_page(width=sheet_w, height=sheet_h)
        left_idx = order[i]
        right_idx = order[i + 1] if i + 1 < len(order) else None

        for slot, idx in enumerate([left_idx, right_idx]):
            if idx is None:
                continue
            page = reader.pages[idx - 1]
            pw = float(page.mediabox.width)
            ph = float(page.mediabox.height)
            scale = min(half / pw, sheet_h / ph)
            tw = pw * scale
            th = ph * scale
            tx = slot * half + (half - tw) / 2
            ty = (sheet_h - th) / 2
            t = Transformation().scale(scale).translate(tx, ty)
            sheet.merge_transformed_page(page, t)
        writer.add_page(sheet)
        i += 2

    with open(dst_pdf, "wb") as fh:
        writer.write(fh)
