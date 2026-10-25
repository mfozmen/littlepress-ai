"""Deterministic metadata prompts — Sub-project 2 of the
"AI-only-for-judgment" refactor.

Before this module existed, the agent greeting told the LLM to walk
the user through a block of upfront questions (title, author, series,
cover choice, back-cover blurb). That worked, but burned an LLM round
trip per answer and gave the model latitude to restructure the flow,
skip questions, or add prose the user had to read and dismiss. None
of those questions actually need AI — they are pure data collection.

These helpers are pure functions that run in the REPL between
ingestion and the agent's first turn. The LLM is invoked only when
the user explicitly opts into an AI branch (AI cover generation in
``collect_cover_choice``, AI back-cover draft in ``collect_back_cover``
— both still live in the agent tool surface; these helpers only
decide *whether* to take that path based on the user's menu choice).

Design notes:

- Every session is treated as fresh. Prompts do NOT check whether the
  draft already has a value and skip — they ask unconditionally. See
  memory feedback ``fresh_session_per_book``: the user's mental model
  is "create a book, finish, forget"; memory-restore UX adds cognitive
  load without solving a real problem for this workflow.

- preserve-child-voice applies. User-typed strings are written
  verbatim to the draft. ``strip()`` on outer whitespace only (paste
  trails); no smart-casing, no Unicode normalisation, no spellcheck.

- Series membership lives INSIDE the title (e.g. ``My Book - 1``) so
  the cover renderer picks it up naturally — no separate data field.

- Prompts localise via ``src/metadata_i18n.py``. Each ``collect_*``
  takes an optional ``lang`` arg; ``None`` resolves to the detected
  language at call time. ``collect_metadata`` resolves once and
  passes the same language down so the user doesn't see English and
  Turkish prompts mixed in one session.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console
from rich.markup import escape

from src.draft import Draft
from src.metadata_i18n import _SUPPORTED, AUTHOR_LABELS, detect_lang, t
from src.title_strip import strip_title_header_from_first_page

ReadLine = Callable[[], str]


@dataclass(frozen=True)
class MetadataChoices:
    """The two AI-branch tags returned by ``collect_metadata``.

    Deterministic branches (page-drawing cover, poster, none-blurb,
    self-written blurb) mutate the draft directly — the REPL doesn't
    need to act on them. The AI branches (``cover == "ai"`` and
    ``back_cover == "ai-draft"``) leave the draft fields untouched
    and defer the work to the agent's first turn.
    """

    cover: str
    back_cover: str
    #: Language the questions were asked in; the agent replies in it.
    lang: str = "en"


# Per-language y/n token sets. English-only mode keeps the strict
# English shape (PR #69 review #1: no Turkish leaks in en flows);
# Turkish mode adds the native ``evet`` / ``hayır`` shortcuts on
# top of the English pair so a Turkish-typing user gets natural
# acceptance without having to reach for ``y``/``n``.
_YES_TOKENS: dict[str, frozenset[str]] = {
    "en": frozenset({"y", "yes"}),
    "tr": frozenset({"y", "yes", "e", "evet"}),
}
_NO_TOKENS: dict[str, frozenset[str]] = {
    "en": frozenset({"n", "no"}),
    "tr": frozenset({"n", "no", "h", "hayır", "hayir"}),
}


def _resolve_lang(lang: str | None) -> str:
    """Normalise ``lang`` to a supported code or fall back to
    ``detect_lang()``. Symmetric with ``detect_lang()``'s own
    "unknown locale → English" contract: a caller passing
    ``lang="fr"`` (or any other unsupported value) gets the
    detected default rather than a string that would force every
    ``t()`` lookup down the English fallback path with a quietly
    inconsistent locale tag floating through. PR #76 review
    finding #3."""
    if lang is None:
        return detect_lang()
    if lang in _SUPPORTED:
        return lang
    return detect_lang()


def _prompt_nonempty(prompt: str, read_line: ReadLine, console: Console) -> str:
    """Prompt the user with ``prompt`` and re-prompt until they type
    a non-empty (post-strip) string. Returns the stripped value."""
    while True:
        console.print(prompt)
        value = read_line().strip()
        if value:
            return value


_LANGUAGE_ANSWERS = {
    "1": "tr", "tr": "tr", "türkçe": "tr", "turkce": "tr", "turkish": "tr",
    "2": "en", "en": "en", "english": "en", "ingilizce": "en", "i̇ngilizce": "en",
}


def collect_language(read_line: ReadLine, console: Console) -> str:
    """First question of a session: which language to talk in. Enter
    keeps the detected default."""
    default = detect_lang()
    while True:
        console.print(t("language.menu", default))
        answer = read_line().strip().lower()
        if not answer:
            return default
        if answer in _LANGUAGE_ANSWERS:
            return _LANGUAGE_ANSWERS[answer]


def collect_title(
    draft: Draft,
    read_line: ReadLine,
    console: Console,
    lang: str | None = None,
) -> None:
    lang = _resolve_lang(lang)
    draft.title = _prompt_nonempty(t("title.prompt", lang), read_line, console)


def collect_author(
    draft: Draft,
    read_line: ReadLine,
    console: Console,
    lang: str | None = None,
) -> None:
    lang = _resolve_lang(lang)
    draft.author = _prompt_nonempty(
        t("author.prompt", lang), read_line, console
    )


def collect_series(
    draft: Draft,
    read_line: ReadLine,
    console: Console,
    lang: str | None = None,
) -> None:
    """Ask whether the book is part of a series; on a yes, append the
    volume number to ``draft.title`` as ``<title> - <n>``. No-op on
    a no."""
    lang = _resolve_lang(lang)
    if _confirm(t("series.prompt", lang), read_line, console, lang):
        volume = _prompt_volume(read_line, console, lang)
        draft.title = f"{draft.title} - {volume}"


def _confirm(prompt: str, read_line: ReadLine, console: Console, lang: str) -> bool:
    """Ask a y/n question until the answer is one of the language's tokens."""
    yes_tokens = _YES_TOKENS.get(lang, _YES_TOKENS["en"])
    no_tokens = _NO_TOKENS.get(lang, _NO_TOKENS["en"])
    while True:
        console.print(prompt)
        answer = read_line().strip().lower()
        if answer in yes_tokens:
            return True
        if answer in no_tokens:
            return False


def _prompt_volume(read_line: ReadLine, console: Console, lang: str) -> int:
    while True:
        console.print(t("series.volume_prompt", lang))
        raw = read_line().strip()
        try:
            n = int(raw)
        except ValueError:
            continue
        if n > 0:
            return n


def collect_cover_choice(
    draft: Draft,
    read_line: ReadLine,
    console: Console,
    lang: str | None = None,
) -> str:
    """3-way menu: ``"page-drawing"``, ``"ai"``, or ``"poster"``.

    Deterministic branches mutate the draft directly. The AI branch
    leaves the draft untouched and returns the ``"ai"`` tag so the
    caller can hand off to the agent's first turn (drafting a prompt
    from the story content is the judgment part that warrants the
    LLM).

    The ``"page-drawing"`` branch auto-picks the first page with an
    attached drawing that isn't hidden. If no such page exists
    (e.g. a 100%-text Samsung Notes export) it falls back to poster
    and prints a localised warning so the user isn't surprised by
    the rendered PDF — the menu stays simple and they can change
    the cover via slash commands post-render if they want something
    different.
    """
    lang = _resolve_lang(lang)
    while True:
        console.print(t("cover.menu", lang))
        answer = read_line().strip().lower()
        if answer == "a":
            return _apply_page_drawing_cover(draft, console, lang)
        if answer == "b":
            return "ai"
        if answer == "c":
            draft.cover_image = None
            draft.cover_style = "poster"
            return "poster"


def _apply_page_drawing_cover(
    draft: Draft, console: Console, lang: str
) -> str:
    first_drawing = next(
        (p.image for p in draft.pages if not p.hidden and p.image is not None),
        None,
    )
    if first_drawing is None:
        # Surface the fallback loudly — the user asked for a drawing
        # cover and would otherwise discover the poster result only
        # when the rendered PDF opens.
        console.print(t("cover.no_drawing_fallback", lang))
        draft.cover_image = None
        draft.cover_style = "poster"
        return "poster"
    draft.cover_image = first_drawing
    draft.cover_style = "full-bleed"
    return "page-drawing"


def collect_back_cover(
    draft: Draft,
    read_line: ReadLine,
    console: Console,
    lang: str | None = None,
) -> str:
    """3-way menu: ``"none"``, ``"self-written"``, or ``"ai-draft"``.

    ``"none"`` clears ``draft.back_cover_text``. ``"self-written"``
    prompts for a blurb and writes it verbatim (preserve-child-voice
    applies — the user is typing on the child's behalf). ``"ai-draft"``
    leaves the draft untouched; the caller hands off to the agent's
    first turn so the LLM can draft a one-line blurb grounded on the
    story's actual page text."""
    lang = _resolve_lang(lang)
    while True:
        console.print(t("back_cover.menu", lang))
        answer = read_line().strip().lower()
        if answer == "a":
            draft.back_cover_text = ""
            return "none"
        if answer == "b":
            draft.back_cover_text = _prompt_nonempty(
                t("back_cover.self_written_prompt", lang),
                read_line,
                console,
            )
            return "self-written"
        if answer == "c":
            return "ai-draft"


def collect_metadata(
    draft: Draft,
    read_line: ReadLine,
    console: Console,
    lang: str | None = None,
) -> MetadataChoices:
    """Run the five deterministic prompts in order: title → author →
    series → cover → back-cover.

    Returns a ``MetadataChoices`` with the two AI-branch tags.
    Deterministic branches mutate the draft in place; AI branches
    leave the corresponding draft fields untouched so the agent's
    first turn can fill them in via the existing tool surface
    (``generate_cover_illustration`` for cover AI,
    ``set_metadata(field="back_cover_text", value=…)`` for back-
    cover AI after the user accepts the draft).

    Resolves the language once at the orchestrator level and passes
    the same value down to each prompt — the user shouldn't see
    English and Turkish strings mixed in one session.
    """
    lang = collect_language(read_line, console) if lang is None else _resolve_lang(lang)
    # Explicit override that detect_lang() honours, so labels shown later
    # (e.g. the agent's progress indicator) follow the user's choice.
    os.environ["LITTLEPRESS_LANG"] = lang
    has_volume = _confirm_suggested_title(draft, read_line, console, lang)
    if has_volume is None:
        collect_title(draft, read_line, console, lang=lang)
    author = suggest_author(draft)
    if author and _confirm(
        t("author.detected", lang).format(author=escape(author)), read_line, console, lang
    ):
        draft.author = author
    else:
        collect_author(draft, read_line, console, lang=lang)
    if not has_volume:
        collect_series(draft, read_line, console, lang=lang)
    cover = collect_cover_choice(draft, read_line, console, lang=lang)
    back_cover = collect_back_cover(draft, read_line, console, lang=lang)
    # The child often writes the book title at the top of the first
    # physical page; OCR captures it and the rendered book ends up
    # with the title twice (cover + first interior page). Now that
    # ``draft.title`` is final (series-volume suffix applied), drop
    # the duplicate header line if the first non-hidden page starts
    # with one. Idempotent — re-running ``/metadata`` is safe.
    strip_title_header_from_first_page(draft)
    return MetadataChoices(cover=cover, back_cover=back_cover, lang=lang)


def _confirm_suggested_title(
    draft: Draft, read_line: ReadLine, console: Console, lang: str
) -> bool | None:
    """Offer the title found on the first page. Returns ``None`` when
    there was none or the user said no, otherwise whether the accepted
    title carried a series volume."""
    suggestion = suggest_title(draft)
    if suggestion is None:
        return None
    title, volume = suggestion
    if volume is None:
        prompt = t("title.detected", lang).format(title=escape(title))
    else:
        prompt = t("title.detected_series", lang).format(
            line=escape(f"{title} {volume}"), title=escape(title), volume=volume
        )
    if not _confirm(prompt, read_line, console, lang):
        return None
    draft.title = title if volume is None else f"{title} - {volume}"
    return volume is not None


_TITLE_MAX_WORDS = 6
_TITLE_MAX_CHARS = 40


def suggest_title(draft: Draft) -> tuple[str, int | None] | None:
    """The child often writes the title as the first line of page one
    (``YAVRU KEDİ 1🐱``). Returns ``(title, volume)`` when that line is
    short and the story continues below it; a trailing number is read
    as the series volume. Emoji and punctuation at the edges are
    dropped — the cover font can't draw emoji."""
    page = next((p for p in draft.pages if not p.hidden), None)
    if page is None:
        return None
    lines = [line.strip() for line in page.text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    alnum = [i for i, ch in enumerate(lines[0]) if ch.isalnum()]
    line = lines[0][alnum[0] : alnum[-1] + 1] if alnum else ""
    words = line.split()
    if (
        not any(ch.isalpha() for ch in line)
        or len(line) > _TITLE_MAX_CHARS
        or len(words) > _TITLE_MAX_WORDS
    ):
        return None
    if len(words) > 1 and words[-1].isdecimal():
        return " ".join(words[:-1]).rstrip(" -–—"), int(words[-1])
    return line, None


_AUTHOR_LINE = re.compile(
    r"^\s*(?:"
    + "|".join(re.escape(label) for labels in AUTHOR_LABELS.values() for label in labels)
    + r")\s*:\s*(\S.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def suggest_author(draft: Draft) -> str | None:
    """Find an ``Author: …`` / ``Yazar: …`` line anywhere in the draft,
    hidden colophon pages included."""
    for page in draft.pages:
        match = _AUTHOR_LINE.search(page.text)
        if match:
            return match.group(1)
    return None
