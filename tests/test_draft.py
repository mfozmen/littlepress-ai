from PIL import Image
from reportlab.lib.pagesizes import A5
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pathlib import Path

import pytest

from src.draft import (
    Draft,
    DraftPage,
    collect_input_pdf,
    from_pdf,
    next_version_number,
    output_paths,
    slugify,
    strip_page_number,
    to_book,
)


def _make_png(path, color):
    Image.new("RGB", (80, 60), color).save(path)
    return path


def _write_pdf(tmp_path, pages):
    """Each page dict: optional 'text', optional 'image' (PIL color tuple)."""
    path = tmp_path / "draft.pdf"
    c = canvas.Canvas(str(path), pagesize=A5)
    for i, page in enumerate(pages):
        if page.get("image") is not None:
            img = tmp_path / f"_src_{i}.png"
            _make_png(img, page["image"])
            c.drawImage(ImageReader(str(img)), 50, 200, width=200, height=150)
        if page.get("text"):
            c.setFont("Helvetica", 14)
            c.drawString(50, 400, page["text"])
        c.showPage()
    c.save()
    return path


def test_from_pdf_combines_text_and_images(tmp_path):
    pdf = _write_pdf(
        tmp_path,
        [
            {"text": "page 1 text", "image": (255, 0, 0)},
            {"text": "page 2 text"},
            {"image": (0, 0, 255)},
        ],
    )

    draft = from_pdf(pdf, tmp_path / "images")

    assert isinstance(draft, Draft)
    assert draft.source_pdf == pdf
    assert len(draft.pages) == 3
    assert "page 1 text" in draft.pages[0].text
    assert draft.pages[0].image is not None
    assert draft.pages[0].image.exists()
    assert "page 2 text" in draft.pages[1].text
    assert draft.pages[1].image is None
    assert draft.pages[2].text.strip() == ""
    assert draft.pages[2].image is not None


def test_from_pdf_empty_pdf_yields_no_pages(tmp_path):
    pdf = _write_pdf(tmp_path, [])

    draft = from_pdf(pdf, tmp_path / "images")

    assert draft.pages == []


def test_from_pdf_preserves_child_voice_verbatim(tmp_path):
    """Integration with the preserve-child-voice contract: text must pass
    through untouched."""
    quirky = [{"text": "the dragn he was sad bcuz no frends"}]
    pdf = _write_pdf(tmp_path, quirky)

    draft = from_pdf(pdf, tmp_path / "images")

    assert draft.pages[0].text.strip() == "the dragn he was sad bcuz no frends"


def test_draft_page_image_defaults_to_none():
    p = DraftPage(text="hello")
    assert p.image is None


def test_from_pdf_parses_the_file_only_once(tmp_path, monkeypatch):
    """Regression guard: ``from_pdf`` must share a single ``PdfReader``
    between text and image extraction. A fresh reader per call is wasteful
    on large scanned drafts (flagged in review of #1 and #7)."""
    pdf = _write_pdf(tmp_path, [{"text": "one", "image": (255, 0, 0)}])

    from src import draft as draft_mod

    counter = {"n": 0}
    real = draft_mod.PdfReader

    def counting_reader(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(draft_mod, "PdfReader", counting_reader)

    draft_mod.from_pdf(pdf, tmp_path / "images")

    assert counter["n"] == 1


def test_to_book_marks_imageless_pages_as_text_only():
    """select-page-layout rule 1: no image → layout must be 'text-only'.

    Leaving the schema default 'image-top' on an imageless page renders
    an empty image slot and lies in the stored book.json.
    """
    draft = Draft(
        source_pdf=Path("x.pdf"),
        title="Book",
        pages=[
            DraftPage(text="no drawing on this page"),
            DraftPage(text="this one has one", image=Path("images/p.png")),
        ],
    )

    book = to_book(draft, Path("."))

    assert book.pages[0].layout == "text-only"
    assert book.pages[1].layout == "image-top"


def test_to_book_requires_title(tmp_path):
    draft = Draft(source_pdf=tmp_path / "x.pdf", pages=[DraftPage(text="hi")])
    # Empty title and whitespace-only title both rejected.
    with pytest.raises(ValueError, match="title"):
        to_book(draft, tmp_path)
    draft.title = "   "
    with pytest.raises(ValueError, match="title"):
        to_book(draft, tmp_path)


def test_to_book_keeps_images_outside_source_dir_as_absolute(tmp_path):
    # Unusual but possible: user /load'd from one dir and images were
    # extracted elsewhere (future --images-dir flag, etc.). The renderer
    # should still be able to resolve them. relative_to() raises ValueError
    # when the image isn't under source_dir, and to_book falls back to the
    # absolute path.
    outside_img = tmp_path / "elsewhere" / "page-01.png"
    outside_img.parent.mkdir(parents=True)
    outside_img.write_bytes(b"")

    draft = Draft(
        source_pdf=tmp_path / "d.pdf",
        pages=[DraftPage(text="hi", image=outside_img)],
        title="Book",
    )
    source_dir = tmp_path / ".book-gen"
    source_dir.mkdir()

    book = to_book(draft, source_dir)
    # Absolute path preserved because relative_to(source_dir) raises.
    assert book.pages[0].image == str(outside_img)


def test_slugify_falls_back_to_book_when_only_symbols():
    assert slugify("***") == "book"
    assert slugify("") == "book"


def test_slugify_lowercases_and_ascii_folds_turkish():
    assert slugify("Küçük Ejderha") == "kucuk_ejderha"
    assert slugify("ÖZNUR") == "oznur"


# --- next_version_number -------------------------------------------------


def test_next_version_returns_one_for_fresh_output_dir(tmp_path):
    """No previous renders → first version is v1."""
    assert next_version_number(tmp_path, "book") == 1


def test_next_version_returns_one_for_missing_output_dir(tmp_path):
    """Brand-new session has no output/ yet; helper handles it."""
    missing = tmp_path / "does" / "not" / "exist"
    assert next_version_number(missing, "book") == 1


def test_next_version_bumps_past_highest_existing(tmp_path):
    (tmp_path / "book.v1.pdf").write_bytes(b"a")
    (tmp_path / "book.v2.pdf").write_bytes(b"b")
    (tmp_path / "book.v5.pdf").write_bytes(b"e")

    # Gaps are respected — the version space is monotonic, never reused.
    assert next_version_number(tmp_path, "book") == 6


def test_next_version_counts_booklet_snapshots_too(tmp_path):
    """A5 and its booklet share a number when both ship together;
    standalone A5 renders leave booklet gaps, but the counter still
    advances past whatever snapshot (A5 or booklet) it finds."""
    (tmp_path / "book.v3_A4_booklet.pdf").write_bytes(b"b")

    assert next_version_number(tmp_path, "book") == 4


def test_next_version_ignores_other_slugs(tmp_path):
    (tmp_path / "other.v7.pdf").write_bytes(b"x")
    (tmp_path / "book.v1.pdf").write_bytes(b"y")

    # An unrelated slug's versions don't pollute this slug's counter.
    assert next_version_number(tmp_path, "book") == 2


def test_next_version_ignores_stable_copies_and_non_versioned_files(tmp_path):
    """A stable ``<slug>.pdf`` alone (from the pre-versioning era or
    after the user deleted the snapshots) must not be mistaken for a
    versioned file — otherwise the counter breaks on first render."""
    (tmp_path / "book.pdf").write_bytes(b"a")
    (tmp_path / "readme.md").write_text("x")

    assert next_version_number(tmp_path, "book") == 1


# --- collect_input_pdf ---------------------------------------------------


def test_collect_input_pdf_copies_external_file_into_book_gen_input(tmp_path):
    """A PDF dropped in Downloads/Desktop should be copied into
    ``<session-root>/.book-gen/input/<stem>-<hash>.pdf`` so the session
    keys off a path we control — the user's Downloads folder can be
    cleaned without destroying memory."""
    source = tmp_path / "Downloads" / "story.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"PDF-CONTENT")

    session_root = tmp_path / "project"
    session_root.mkdir()

    collected = collect_input_pdf(source, session_root)

    input_dir = session_root / ".book-gen" / "input"
    assert collected.parent == input_dir
    # Hash is baked into the filename so repeat collections find the
    # same path without a byte-compare.
    assert collected.stem.startswith("story-")
    assert collected.suffix == ".pdf"
    assert collected.read_bytes() == b"PDF-CONTENT"
    # Original stays where the user left it — we don't move, we copy.
    assert source.is_file()


def test_collect_input_pdf_is_idempotent_for_identical_content(tmp_path):
    """Reloading the same PDF on a later run must not crash or rewrite
    — same bytes produce the same hashed name, so the existing file is
    returned untouched."""
    source = tmp_path / "story.pdf"
    source.write_bytes(b"SAME-BYTES")
    session_root = tmp_path / "project"
    session_root.mkdir()

    first = collect_input_pdf(source, session_root)
    mtime_before = first.stat().st_mtime_ns

    # Touch the source to a different mtime; content is identical.
    source.write_bytes(b"SAME-BYTES")

    second = collect_input_pdf(source, session_root)

    assert first == second
    # Same-hash destination was reused, not rewritten — mtime unchanged.
    assert second.stat().st_mtime_ns == mtime_before


def test_collect_input_pdf_distinguishes_different_content_same_name(tmp_path):
    """Two PDFs happen to share a basename (``draft.pdf``) but have
    different content. Collecting both must produce two distinct
    in-repo paths — otherwise their memories would cross-wire."""
    a = tmp_path / "a" / "draft.pdf"
    b = tmp_path / "b" / "draft.pdf"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_bytes(b"CONTENT-A")
    b.write_bytes(b"CONTENT-B")
    session_root = tmp_path / "project"
    session_root.mkdir()

    collected_a = collect_input_pdf(a, session_root)
    collected_b = collect_input_pdf(b, session_root)

    assert collected_a != collected_b
    assert collected_a.read_bytes() == b"CONTENT-A"
    assert collected_b.read_bytes() == b"CONTENT-B"


def test_collect_input_pdf_noop_when_path_already_under_input(tmp_path):
    """``/load .book-gen/input/draft-<hash>.pdf`` (user pointing at the
    already-collected copy) must not recurse into itself — return the
    path unchanged."""
    session_root = tmp_path / "project"
    input_dir = session_root / ".book-gen" / "input"
    input_dir.mkdir(parents=True)
    already_in_place = input_dir / "draft-deadbeef.pdf"
    already_in_place.write_bytes(b"BYTES")

    collected = collect_input_pdf(already_in_place, session_root)

    assert collected == already_in_place
    assert collected.read_bytes() == b"BYTES"


def test_collect_input_pdf_creates_input_dir_if_missing(tmp_path):
    """First-ever collection on a brand-new session must create
    ``.book-gen/input/`` — there's no directory yet."""
    source = tmp_path / "story.pdf"
    source.write_bytes(b"X")
    session_root = tmp_path / "fresh"

    collected = collect_input_pdf(source, session_root)

    assert collected.parent.is_dir()
    assert collected.parent == session_root / ".book-gen" / "input"


def test_next_version_does_not_confuse_slugs_ending_with_version_digits(tmp_path):
    """``slugify`` preserves hyphens and digits, so a book titled
    'Book-V1' slugs to 'book-v1'. Its stable ``book-v1.pdf`` must not
    be misread as a v1 snapshot of a different 'book' slug. Using a
    dot separator — which slugify can never emit — isolates the two
    namespaces by construction."""
    # Stable PDF for slug "book-v1" (e.g. a book titled "Book-V1").
    (tmp_path / "book-v1.pdf").write_bytes(b"x")

    # Unrelated slug; should start at v1.
    assert next_version_number(tmp_path, "book") == 1


def test_draft_page_defaults_to_visible():
    from src.draft import DraftPage

    page = DraftPage(text="hi")

    assert page.hidden is False


def test_draft_page_can_be_hidden():
    from src.draft import DraftPage

    page = DraftPage(text="hi", hidden=True)

    assert page.hidden is True


def test_to_book_excludes_hidden_pages(tmp_path):
    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        title="Story",
        pages=[
            DraftPage(text="page 1"),
            DraftPage(text="page 2", hidden=True),
            DraftPage(text="page 3"),
        ],
    )

    book = to_book(draft, tmp_path)

    assert [p.text for p in book.pages] == ["page 1", "page 3"]


# --- strip_page_number ---------------------------------------------------
# The child typed the page number at the end of each page; the renderer
# prints its own, so the book showed it twice. Maintainer-approved:
# drop ONLY a trailing token equal to the page's own number.


@pytest.mark.parametrize(
    "text, page_n, expected",
    [
        ("Ve o onla oynamış.\n2", 2, "Ve o onla oynamış."),
        ("Ve o onla oynamış. 2", 2, "Ve o onla oynamış."),
        ("oynamış.\n7 \n", 7, "oynamış."),
        # Another number, or a number that is part of a word, stays.
        ("Üç kedi olmuş. 3", 5, "Üç kedi olmuş. 3"),
        ("Saat 12", 2, "Saat 12"),
        # Text that is nothing but the number may be the child's content.
        ("4", 4, "4"),
        ("", 1, ""),
        # A number alone on the first or last line is a page number even
        # when it lags the PDF page: the child skipped a number on page 7
        # of Yavru Kedi 1, so page 8 reads "7" and page 9 reads "8".
        ("7\nVe bir tane daha kedi gelmiş.", 8, "Ve bir tane daha kedi gelmiş."),
        ("8\nYazar:Poyraz ÖZMEN ", 9, "Yazar:Poyraz ÖZMEN "),
        ("  \n 3 \n Onu çok sevmiş.", 4, " Onu çok sevmiş."),
        ("oynamış.\n6\n", 7, "oynamış."),
        # One ahead is still a page number; anything beyond is content.
        ("Bir gün\n5", 4, "Bir gün"),
        ("12\nkedi", 3, "12\nkedi"),
        ("Saat\n12", 2, "Saat\n12"),
        ("0\nkedi", 3, "0\nkedi"),
        # Never strip a page down to nothing.
        ("4\n5", 5, "4"),
    ],
)
def test_strip_page_number(text, page_n, expected):
    assert strip_page_number(text, page_n) == expected


def test_from_pdf_drops_the_typed_page_number(tmp_path):
    path = tmp_path / "draft.pdf"
    c = canvas.Canvas(str(path), pagesize=A5)
    for n, line in enumerate(["Once upon", "they played."], start=1):
        c.setFont("Helvetica", 14)
        c.drawString(50, 400, line)
        c.drawString(50, 100, str(n))
        c.showPage()
    c.save()

    draft = from_pdf(path, tmp_path / "images")

    assert [p.text.strip() for p in draft.pages] == ["Once upon", "they played."]


# --- output_paths --------------------------------------------------------


def test_output_paths_use_the_title_slug(tmp_path):
    a5, booklet = output_paths(tmp_path, "Yavru Kedi - 1")

    assert a5 == (tmp_path / "yavru_kedi_-_1.pdf").resolve()
    assert booklet == (tmp_path / "yavru_kedi_-_1_A4_booklet.pdf").resolve()


class _FakeLock:
    """Makes ``open(locked, "r+b")`` raise PermissionError like a Windows
    viewer holding the file, until ``release`` is called."""

    def __init__(self, monkeypatch, locked):
        import builtins

        self.locked = locked.resolve()
        self.held = True
        real_open = builtins.open

        def fake_open(file, *args, **kwargs):
            if self.held and Path(file).resolve() == self.locked:
                raise PermissionError(13, "in use")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fake_open)

    def release(self):
        self.held = False


def test_output_paths_close_the_viewer_holding_the_pdf(tmp_path, monkeypatch):
    """Windows viewers lock the open PDF. Closing the viewer keeps the
    stable file name instead of piling up numbered copies."""
    import src.draft as draft_mod

    (tmp_path / "book.pdf").write_bytes(b"%PDF")
    lock = _FakeLock(monkeypatch, tmp_path / "book.pdf")
    closed: list = []

    def fake_close(paths):
        closed.extend(paths)
        lock.release()
        return True

    monkeypatch.setattr(draft_mod, "_close_viewers", fake_close)

    a5, _ = output_paths(tmp_path, "Book")

    assert a5 == (tmp_path / "book.pdf").resolve()
    assert (tmp_path / "book.pdf").resolve() in closed


def test_output_paths_fall_back_when_the_viewer_cannot_be_closed(
    tmp_path, monkeypatch
):
    import src.draft as draft_mod

    (tmp_path / "book.pdf").write_bytes(b"%PDF")
    _FakeLock(monkeypatch, tmp_path / "book.pdf")
    monkeypatch.setattr(draft_mod, "_close_viewers", lambda _paths: False)

    a5, booklet = output_paths(tmp_path, "Book")

    assert a5 == (tmp_path / "book-2.pdf").resolve()
    assert booklet == (tmp_path / "book-2_A4_booklet.pdf").resolve()


def test_output_paths_fall_back_when_the_lock_outlives_the_close(
    tmp_path, monkeypatch
):
    """A viewer that asks "save changes?" keeps the lock; don't hang."""
    import src.draft as draft_mod

    (tmp_path / "book.pdf").write_bytes(b"%PDF")
    _FakeLock(monkeypatch, tmp_path / "book.pdf")
    monkeypatch.setattr(draft_mod, "_close_viewers", lambda _paths: True)
    monkeypatch.setattr(draft_mod, "_LOCK_RELEASE_WAIT_SECONDS", 0.05)

    a5, _ = output_paths(tmp_path, "Book")

    assert a5 == (tmp_path / "book-2.pdf").resolve()


class _FakeUser32:
    """Just enough of user32 for ``_close_viewers``: two visible windows
    and one hidden one, recording WM_CLOSE posts."""

    def __init__(self, titles, pids=None):
        self.titles = titles  # hwnd -> (title, visible)
        self.pids = pids or {}  # hwnd -> owning process id
        self.posted: list = []

    def GetWindowThreadProcessId(self, hwnd, pid_ref):  # noqa: N802
        pid_ref._obj.value = self.pids.get(hwnd, 0)
        return 1

    def EnumWindows(self, callback, _lparam):  # noqa: N802 — Win32 name
        for hwnd in self.titles:
            callback(hwnd, 0)
        return True

    def IsWindowVisible(self, hwnd):  # noqa: N802
        return self.titles[hwnd][1]

    def GetWindowTextLengthW(self, hwnd):  # noqa: N802
        return len(self.titles[hwnd][0])

    def GetWindowTextW(self, hwnd, buf, size):  # noqa: N802
        buf.value = self.titles[hwnd][0][: size - 1]
        return len(buf.value)

    def PostMessageW(self, hwnd, msg, _w, _l):  # noqa: N802
        self.posted.append((hwnd, msg))
        return True


def test_close_viewers_closes_only_the_locking_process_window_showing_the_pdf(
    tmp_path,
):
    """Only windows of the process that holds the lock AND show the PDF
    get closed — not a browser tab or another app with a similar title."""
    from src.draft import _WM_CLOSE, _close_viewers

    user32 = _FakeUser32(
        {
            1: ("book.pdf - Adobe Acrobat Reader", True),
            2: ("Notes.txt - Notepad", True),
            3: ("book.pdf", False),  # hidden helper window
            4: ("book.pdf - Microsoft Edge", True),  # not the lock holder
            5: ("Home - Adobe Acrobat Reader", True),  # holder, other title
        },
        pids={1: 100, 2: 100, 3: 100, 4: 200, 5: 100},
    )

    closed = _close_viewers([tmp_path / "book.pdf"], user32=user32, pids={100})

    assert closed is True
    assert user32.posted == [(1, _WM_CLOSE)]


def test_close_viewers_does_nothing_when_no_process_holds_the_lock(tmp_path):
    from src.draft import _close_viewers

    user32 = _FakeUser32({1: ("book.pdf - Viewer", True)}, pids={1: 100})

    assert _close_viewers([tmp_path / "book.pdf"], user32=user32, pids=set()) is False
    assert user32.posted == []


def test_close_viewers_reports_when_no_window_shows_the_pdf(tmp_path):
    from src.draft import _close_viewers

    user32 = _FakeUser32({2: ("Notes.txt - Notepad", True)}, pids={2: 100})

    assert _close_viewers([tmp_path / "book.pdf"], user32=user32, pids={100}) is False


def test_close_viewers_is_a_no_op_off_windows(tmp_path, monkeypatch):
    import src.draft as draft_mod

    monkeypatch.setattr(draft_mod.sys, "platform", "linux")

    assert draft_mod._close_viewers([tmp_path / "book.pdf"]) is False


def test_close_viewers_uses_the_real_user32_on_windows(tmp_path, monkeypatch):
    import ctypes
    import types

    import src.draft as draft_mod

    user32 = _FakeUser32({1: ("book.pdf - Viewer", True)}, pids={1: 4242})
    rm = _FakeRestartManager(pids=[4242])
    monkeypatch.setattr(draft_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes,
        "windll",
        types.SimpleNamespace(user32=user32, rstrtmgr=rm),
        raising=False,
    )

    assert draft_mod._close_viewers([tmp_path / "book.pdf"]) is True
    assert user32.posted == [(1, draft_mod._WM_CLOSE)]
    assert rm.registered == [str(tmp_path / "book.pdf")]
    assert rm.ended


class _FakeRestartManager:
    """Stand-in for rstrtmgr.dll: reports ``pids`` as lock holders."""

    def __init__(self, pids, fail=None):
        self.pids = pids
        self.fail = fail  # name of the call that should return an error
        self.registered: list = []
        self.ended = False

    def RmStartSession(self, session_ref, _flags, _key):  # noqa: N802
        session_ref._obj.value = 7
        return 5 if self.fail == "start" else 0

    def RmRegisterResources(self, _session, n, files, *_rest):  # noqa: N802
        self.registered = [files[i] for i in range(n)]
        return 5 if self.fail == "register" else 0

    def RmGetList(self, _session, needed, count, infos, _reasons):  # noqa: N802
        if self.fail == "list":
            return 234  # ERROR_MORE_DATA
        for i, pid in enumerate(self.pids):
            infos[i].Process.dwProcessId = pid
        count._obj.value = len(self.pids)
        needed._obj.value = len(self.pids)
        return 0

    def RmEndSession(self, _session):  # noqa: N802
        self.ended = True
        return 0


@pytest.mark.parametrize("fail", ["start", "register", "list"])
def test_locking_pids_is_empty_when_restart_manager_fails(tmp_path, fail):
    from src.draft import _locking_pids

    rm = _FakeRestartManager(pids=[1], fail=fail)

    assert _locking_pids([tmp_path / "book.pdf"], rm=rm) == set()
    assert rm.ended is (fail != "start")
