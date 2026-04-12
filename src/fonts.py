import os
import sys
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .config import FONT_REGULAR, FONT_BOLD

SEARCH_DIRS = [
    Path(__file__).resolve().parent.parent / "fonts",
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/Library/Fonts"),
]

FILES = {
    FONT_REGULAR: "DejaVuSans.ttf",
    FONT_BOLD: "DejaVuSans-Bold.ttf",
}


def _find(filename: str) -> Path | None:
    for base in SEARCH_DIRS:
        candidate = base / filename
        if candidate.is_file():
            return candidate
    return None


def register_fonts() -> None:
    missing = []
    for name, filename in FILES.items():
        path = _find(filename)
        if path is None:
            missing.append(filename)
            continue
        pdfmetrics.registerFont(TTFont(name, str(path)))
    if missing:
        msg = (
            "DejaVu fonts not found: "
            + ", ".join(missing)
            + "\nDownload from https://dejavu-fonts.github.io/ "
            "and place the .ttf files in ./fonts/."
        )
        raise FileNotFoundError(msg)
