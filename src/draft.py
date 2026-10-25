"""In-memory representation of a PDF draft being worked on in the REPL.

Unlike ``src/schema.Book`` (which validates the finished product before
rendering), ``Draft`` is deliberately lenient: a freshly-ingested PDF
may still be missing a title, cover, or per-page text. The REPL walks
the user through filling those gaps; only then is a ``Book`` built and
rendered.

Preserve-child-voice: nothing in this module rewrites page text. The
ingestion path just copies what ``src/pdf_ingest`` returned.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

from src.pdf_ingest import extract_images, extract_pages
from src.schema import VALID_COVER_STYLES, BackCover, Book, Cover, Page


@dataclass
class DraftPage:
    text: str = ""
    image: Path | None = None
    layout: str = "image-top"
    hidden: bool = False


@dataclass
class Draft:
    source_pdf: Path
    pages: list[DraftPage] = field(default_factory=list)
    title: str = ""
    author: str = ""
    cover_image: Path | None = None
    cover_subtitle: str = ""
    cover_style: str = "full-bleed"
    back_cover_text: str = ""


def from_pdf(pdf_path: Path, images_dir: Path) -> Draft:
    """Ingest ``pdf_path`` into a fresh ``Draft``.

    Text and images are extracted through ``src.pdf_ingest``; this
    function only zips them back together so they share a common page
    order. Missing text on a page is kept as an empty string; missing
    images stay ``None``.
    """
    pdf_path = Path(pdf_path)
    # Parse the PDF once and thread the reader through both extractors —
    # a fresh PdfReader per call is wasteful on large scanned drafts.
    reader = PdfReader(str(pdf_path))
    texts = extract_pages(pdf_path, reader=reader)
    images = extract_images(pdf_path, images_dir, reader=reader)
    n = max(len(texts), len(images))
    pages = [
        DraftPage(
            text=strip_page_number(texts[i], i + 1) if i < len(texts) else "",
            image=images[i] if i < len(images) else None,
        )
        for i in range(n)
    ]
    return Draft(source_pdf=pdf_path, pages=pages)


def strip_page_number(text: str, page_n: int) -> str:
    """Drop a trailing page number the child typed (``"...oynamış.\n2"``
    on page 2). The renderer prints its own, so it showed twice.

    A whitespace-separated final token equal to ``page_n`` goes, and so
    does a number alone on the first or last non-empty line when it is
    between 1 and ``page_n + 1``: a skipped number shifts the child's
    numbering behind the PDF's. Every other character is left as-is.
    """
    text = re.sub(rf"\s+{page_n}\s*\Z", "", text)
    lines = text.split("\n")
    filled = [i for i, line in enumerate(lines) if line.strip()]
    if len(filled) < 2:
        return text

    def is_page_number(line: str) -> bool:
        line = line.strip()
        return line.isdecimal() and 1 <= int(line) <= page_n + 1

    first, last = filled[0], filled[-1]
    if is_page_number(lines[last]):
        lines = lines[:last]
        filled.pop()
    if len(filled) > 1 and is_page_number(lines[first]):
        lines = lines[first + 1 :]
    return "\n".join(lines)


def slugify(text: str) -> str:
    """Produce a filesystem-safe filename from a title.

    Single source of truth — the REPL's ``/render`` flow and the
    agent's ``render_book`` tool both reach for this when mapping
    ``Draft.title`` to the ``.book-gen/output/<slug>.pdf`` path.
    """
    table = str.maketrans(
        {
            "ı": "i", "İ": "I", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U",
            "ş": "s", "Ş": "S", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C",
            " ": "_",
        }
    )
    cleaned = text.translate(table)
    return (
        "".join(ch for ch in cleaned if ch.isalnum() or ch in "_-").lower() or "book"
    )


def output_paths(output_dir: Path, title: str) -> tuple[Path, Path]:
    """The A5 and A4-booklet paths a render writes for ``title``.

    A PDF open in a Windows viewer is locked, so writing over it fails.
    Close the viewer window showing it and keep the stable name; only if
    that doesn't free the file, fall back to the first unlocked
    ``<slug>-N`` pair.
    """
    slug = slugify(title)
    n = 1
    while True:
        stem = slug if n == 1 else f"{slug}-{n}"
        pair = (
            (Path(output_dir) / f"{stem}.pdf").resolve(),
            (Path(output_dir) / f"{stem}_A4_booklet.pdf").resolve(),
        )
        locked = [p for p in pair if _is_locked(p)]
        if not locked:
            return pair
        if n == 1 and _close_viewers(locked) and _wait_unlocked(locked):
            return pair
        n += 1


#: How long to wait for a closed viewer to release the file. A viewer
#: asking "save changes?" keeps it locked; then the numbered name is used.
_LOCK_RELEASE_WAIT_SECONDS = 3.0
_WM_CLOSE = 0x0010


def _wait_unlocked(paths: list[Path]) -> bool:
    deadline = time.monotonic() + _LOCK_RELEASE_WAIT_SECONDS
    while any(_is_locked(p) for p in paths):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def _close_viewers(paths: list[Path], user32=None, pids=None) -> bool:
    """Ask the windows showing ``paths`` to close (same as clicking X).

    Only windows owned by a process that actually holds the lock (per
    the Windows Restart Manager) and whose title names the file are
    closed, so a browser tab or an unrelated app with a similar title is
    never touched. Returns whether any window was asked. Windows only;
    elsewhere viewers don't lock files.
    """
    import ctypes

    if user32 is None:
        if sys.platform != "win32":
            return False
        user32 = ctypes.windll.user32
    if pids is None:
        pids = _locking_pids(paths)
    if not pids:
        return False

    names = [p.name.lower() for p in paths]
    hits: list = []
    enum_proc = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )

    def visit(hwnd, _lparam):
        owner = ctypes.c_uint32()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if any(name in title.value.lower() for name in names):
                hits.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(visit), 0)
    for hwnd in hits:
        user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
    return bool(hits)


def _locking_pids(paths: list[Path], rm=None) -> set[int]:
    """Process ids holding ``paths`` open, via the Windows Restart
    Manager. Empty on any failure: then nothing gets closed."""
    import ctypes

    class _UniqueProcess(ctypes.Structure):
        _fields_ = [
            ("dwProcessId", ctypes.c_uint32),
            ("ProcessStartTime", ctypes.c_uint32 * 2),
        ]

    class _ProcessInfo(ctypes.Structure):  # RM_PROCESS_INFO
        _fields_ = [
            ("Process", _UniqueProcess),
            ("strAppName", ctypes.c_wchar * 256),
            ("strServiceShortName", ctypes.c_wchar * 64),
            ("ApplicationType", ctypes.c_int32),
            ("AppStatus", ctypes.c_uint32),
            ("TSSessionId", ctypes.c_uint32),
            ("bRestartable", ctypes.c_int32),
        ]

    rm = rm or ctypes.windll.rstrtmgr
    session = ctypes.c_uint32()
    key = ctypes.create_unicode_buffer(33)
    if rm.RmStartSession(ctypes.byref(session), 0, key):
        return set()
    try:
        files = (ctypes.c_wchar_p * len(paths))(*(str(p) for p in paths))
        if rm.RmRegisterResources(session, len(paths), files, 0, None, 0, None):
            return set()
        needed, count, reasons = ctypes.c_uint32(), ctypes.c_uint32(16), ctypes.c_uint32()
        infos = (_ProcessInfo * 16)()
        if rm.RmGetList(
            session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons)
        ):
            return set()
        return {infos[i].Process.dwProcessId for i in range(count.value)}
    finally:
        rm.RmEndSession(session)


def _is_locked(path: Path) -> bool:
    try:
        with open(path, "r+b"):
            return False
    except FileNotFoundError:
        return False
    except PermissionError:
        return True


def collect_input_pdf(source: Path, session_root: Path) -> Path:
    """Mirror ``source`` into ``<session_root>/.book-gen/input/`` and
    return the in-repo path.

    Why: the draft's ``source_pdf`` and persisted memory both key off
    the PDF's absolute path. If the original lives outside the project
    (Downloads, Desktop, …) a later move or ``rm`` breaks the session
    because we can't match what's saved. Copying into a path we control
    decouples the user's file-system hygiene from the project state.

    Name scheme: ``<stem>-<sha256[:16]><suffix>``. The content hash in
    the filename is deterministic (same bytes → same path → shared
    memory, correct: it's the same book) while collision-safe by
    construction (different bytes → different path → separate
    memories). Bare basenames would cross-wire two drafts that happen
    to share a name; that's the regression this helper protects
    against. 16 hex chars (64 bits) makes accidental collision
    effectively impossible while keeping the filename readable.

    Idempotent by same-hash-same-path: if the destination exists we
    return it without touching it. The helper assumes this directory
    is owned by Littlepress — a user hand-editing a file under
    ``.book-gen/input/`` would make the name-to-content invariant
    false; that's out of scope and will surface as a mismatched
    render. If the source is already inside the input directory
    (e.g. the user did ``/load .book-gen/input/draft-<hash>.pdf``)
    we return it unchanged so the helper never copies a file onto
    itself.
    """
    source = Path(source).resolve()
    input_dir = (Path(session_root) / ".book-gen" / "input").resolve()
    try:
        source.relative_to(input_dir)
        return source
    except ValueError:
        pass
    input_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    destination = input_dir / f"{source.stem}-{digest}{source.suffix}"
    if destination.is_file():
        return destination
    shutil.copyfile(source, destination)
    return destination


def next_version_number(output_dir: Path, slug: str) -> int:
    """Return the next ``.vN`` number for ``slug`` inside ``output_dir``.

    Scans ``<slug>.vN.pdf`` (A5) and ``<slug>.vN_A4_booklet.pdf``
    (booklet). A single render pairs its A5 and booklet under the same
    number, but separate invocations can leave booklet-less gaps (a
    bare ``/render`` at v1 followed by ``/render --impose`` at v2) —
    the counter still advances past whichever snapshot it finds.

    The separator is a literal dot on purpose: ``slugify`` never emits
    ``.``, so ``<slug>.vN.pdf`` can only have been produced by this
    versioner. That prevents a stable pointer for one slug (e.g. a book
    titled "Book-V1" → ``book-v1.pdf``) from being misread as a v1
    snapshot of a shorter slug.
    """
    if not output_dir.exists():
        return 1
    pattern = re.compile(rf"^{re.escape(slug)}\.v(\d+)(?:_A4_booklet)?\.pdf$")
    nums: set[int] = set()
    for p in output_dir.iterdir():
        m = pattern.match(p.name)
        if m:
            nums.add(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def to_book(draft: Draft, source_dir: Path) -> Book:
    """Project a ``Draft`` into the strict ``Book`` shape the renderer wants.

    Image paths on ``DraftPage`` are absolute; this helper rewrites them
    relative to ``source_dir`` (where the renderer resolves them).
    Images outside ``source_dir`` fall back to their absolute path so
    an external drawing still renders — preserve-child-voice also covers
    the child's drawings, which must not be dropped silently.
    """
    if not draft.title.strip():
        raise ValueError("Draft is missing a title.")
    if draft.cover_style not in VALID_COVER_STYLES:
        # The REPL path (Draft → to_book → Book → build_pdf) skips
        # load_book's validation, so a typo set directly on the draft
        # (e.g. ``fullbleed`` without the hyphen) would otherwise fall
        # through to the renderer's dispatch default and silently
        # render under the wrong template. Catch it here instead.
        raise ValueError(
            f"Invalid cover style '{draft.cover_style}'. "
            f"Valid styles: {sorted(VALID_COVER_STYLES)}."
        )
    source_dir = Path(source_dir)

    def _rel(p: Path | None) -> str | None:
        if p is None:
            return None
        try:
            return str(p.relative_to(source_dir)).replace("\\", "/")
        except ValueError:
            return str(p)

    schema_pages: list[Page] = []
    for p in draft.pages:
        if p.hidden:
            continue
        image_str = _rel(p.image)
        # Rule 1 of .claude/skills/select-page-layout: pages with no image
        # must render as text-only. Other pages keep the draft's layout
        # (default image-top) — the choose_layout tool can override.
        layout = "text-only" if image_str is None else p.layout
        schema_pages.append(Page(text=p.text, image=image_str, layout=layout))
    return Book(
        title=draft.title.strip(),
        author=draft.author.strip(),
        cover=Cover(
            image=_rel(draft.cover_image),
            subtitle=draft.cover_subtitle,
            style=draft.cover_style,
        ),
        back_cover=BackCover(text=draft.back_cover_text),
        pages=schema_pages,
        source_dir=source_dir,
    )
