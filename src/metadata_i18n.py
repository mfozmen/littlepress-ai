"""Translations + language detection for the deterministic
metadata prompts.

Sub-project 2 (PR #69) replaced the agent-driven upfront question
block with plain Python prompts but shipped them as terse English-
only labels. The maintainer reported the gap during the 2026-04-25
live render — they were typing Turkish to the REPL and got cold
single-word English questions back. The "AI-only-for-judgment"
principle is a TOKEN rule, not a UX rule: deterministic prompts
must still localise. See memory feedback
``determinism_is_not_english_only``.

This module ships English + Turkish translations for every
metadata-prompt string, plus a ``detect_lang`` helper that picks
between them. Other locales fall back to English; the system stays
coherent for global users who don't have a translation for their
locale yet. Adding a new language is a dict addition — call sites
don't change.

CLAUDE.md compliance: the Turkish strings here are NOT scattered
Turkish-token leaks (the kind of regression flagged repeatedly in
PRs #60–#67). They live in a structured translations dict, gated
by a recognised locale, exactly the way a localised CLI is
supposed to work. The maintainer's English-only rule was about
preventing bilingual-typist drift in places that should be one
language; explicit i18n in a single dedicated module is the
opposite shape.
"""

from __future__ import annotations

import locale
import os

# Each translation key maps to a per-language string. Every key
# MUST have both ``en`` and ``tr`` entries — the parity test in
# ``tests/test_metadata_i18n.py`` enforces this so a locale-tr
# user can't silently fall back to English mid-flow.
_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- language (asked first, so both languages are shown) ---------
    "language.menu": {
        "en": (
            "[bold]Dil / Language?[/bold]\n  (1) Türkçe\n  (2) English\n"
            "(press Enter for English)"
        ),
        "tr": (
            "[bold]Dil / Language?[/bold]\n  (1) Türkçe\n  (2) English\n"
            "(Enter = Türkçe)"
        ),
    },
    # --- title -----------------------------------------------------
    "title.prompt": {
        "en": "[bold]What's the title of the book?[/bold]",
        "tr": "[bold]Kitabın adı ne?[/bold]",
    },
    "title.detected": {
        "en": "[bold]The first page starts with “{title}”. Is that the book's title?[/bold] (y/n)",
        "tr": "[bold]İlk sayfa “{title}” diye başlıyor. Kitabın adı bu mu?[/bold] (e/h)",
    },
    "title.detected_series": {
        "en": (
            "[bold]The first page starts with “{line}”. Is the book "
            "“{title}”, volume {volume} of a series?[/bold] (y/n)"
        ),
        "tr": (
            "[bold]İlk sayfa “{line}” diye başlıyor. Kitabın adı "
            "“{title}”, serinin {volume}. kitabı mı?[/bold] (e/h)"
        ),
    },
    # --- author ----------------------------------------------------
    "author.detected": {
        "en": "[bold]The draft names “{author}” as the author. Is that right?[/bold] (y/n)",
        "tr": "[bold]Taslakta yazar olarak “{author}” yazıyor. Doğru mu?[/bold] (e/h)",
    },
    "author.prompt": {
        "en": "[bold]Who's the author?[/bold]",
        "tr": "[bold]Yazar kim?[/bold]",
    },
    # --- series ----------------------------------------------------
    "series.prompt": {
        "en": "[bold]Is this book part of a series?[/bold] (y/n)",
        # Match the hint to the tokens the tr y/n set actually
        # accepts — ``evet`` / ``e`` / ``hayır`` / ``h`` (plus
        # ``y`` / ``yes`` / ``n`` / ``no`` for forgiveness, but
        # those don't need to be advertised in tr mode). The
        # English ``(y/n)`` would tell a Turkish-typing user they
        # have to switch to English keys.
        "tr": "[bold]Bu kitap bir serinin parçası mı?[/bold] (e/h)",
    },
    "series.volume_prompt": {
        "en": "[bold]Which volume is this? (positive integer)[/bold]",
        "tr": "[bold]Serinin kaçıncı kitabı? (pozitif bir tam sayı)[/bold]",
    },
    # --- cover menu ------------------------------------------------
    "cover.menu": {
        "en": (
            "[bold]How would you like the cover?[/bold]\n"
            "  (a) use a page drawing from the story\n"
            "  (b) generate one with AI\n"
            "  (c) poster — typography only, no image"
        ),
        "tr": (
            "[bold]Kapak nasıl olsun?[/bold]\n"
            "  (a) hikâyedeki sayfa çiziminden birini kullan\n"
            "  (b) yapay zekayla oluştur\n"
            "  (c) afiş — sadece yazı, görsel yok"
        ),
    },
    # --- back cover menu ------------------------------------------
    "back_cover.menu": {
        "en": (
            "[bold]Back-cover blurb?[/bold]\n"
            "  (a) leave it empty\n"
            "  (b) I'll write it myself\n"
            "  (c) draft one with AI"
        ),
        "tr": (
            "[bold]Arka kapak yazısı?[/bold]\n"
            "  (a) boş bıraksın\n"
            "  (b) kendim yazacağım\n"
            "  (c) yapay zekayla taslak hazırla"
        ),
    },
    "back_cover.self_written_prompt": {
        "en": (
            "[bold]Type the back-cover blurb (one or two sentences):"
            "[/bold]"
        ),
        "tr": (
            "[bold]Arka kapak yazısını yaz (bir-iki cümle):[/bold]"
        ),
    },
    # --- shared menus / fallback warnings -------------------------
    "cover.no_drawing_fallback": {
        "en": (
            "[yellow]No page drawing available — falling back to "
            "poster.[/yellow]"
        ),
        "tr": (
            "[yellow]Kullanılabilir bir sayfa çizimi yok — afiş "
            "kapağa düşülüyor.[/yellow]"
        ),
    },
    # --- AI drafts after the menus (src/setup_flow.py) -------------
    "cover.ai_review": {
        "en": (
            "[bold]Here's a description for the cover picture:[/bold]\n"
            "  {text}\n"
            "Press Enter to use it, type your own description, or n to "
            "skip (poster cover)."
        ),
        "tr": (
            "[bold]Kapak resmi için şu tarifi hazırladım:[/bold]\n"
            "  {text}\n"
            "Kullanmak için Enter'a bas, kendi tarifini yaz ya da "
            "vazgeçmek için h yaz (afiş kapak)."
        ),
    },
    "cover.ai_unavailable": {
        "en": (
            "[yellow]No image provider is set up for an AI cover, so the "
            "book gets a poster cover for now. Add one with /image-model "
            "openai or /image-model google, then ask for a new cover "
            "during the review.[/yellow]"
        ),
        "tr": (
            "[yellow]Yapay zekâ kapağı için bir görsel sağlayıcı yok, bu "
            "yüzden şimdilik afiş kapak kullanılıyor. /image-model openai "
            "ya da /image-model google ile ekleyip incelemede yeni kapak "
            "isteyebilirsin.[/yellow]"
        ),
    },
    "cover.ai_failed": {
        "en": (
            "[yellow]The cover picture couldn't be made ({error}). Using a "
            "poster cover; you can ask for a new one during the review."
            "[/yellow]"
        ),
        "tr": (
            "[yellow]Kapak resmi üretilemedi ({error}). Afiş kapak "
            "kullanılıyor; incelemede yeniden isteyebilirsin.[/yellow]"
        ),
    },
    "cover.ai_declined": {
        "en": "[yellow]No cover picture generated; using a poster cover.[/yellow]",
        "tr": "[yellow]Kapak resmi üretilmedi; afiş kapak kullanılıyor.[/yellow]",
    },
    "back_cover.ai_review": {
        "en": (
            "[bold]Here's a back-cover blurb:[/bold]\n"
            "  {text}\n"
            "Press Enter to use it, type your own, or n to leave it empty."
        ),
        "tr": (
            "[bold]Arka kapak için şu yazıyı hazırladım:[/bold]\n"
            "  {text}\n"
            "Kullanmak için Enter'a bas, kendin yaz ya da boş bırakmak "
            "için h yaz."
        ),
    },
    "back_cover.ai_failed": {
        "en": "[yellow]The blurb couldn't be drafted ({error}); the back cover stays empty.[/yellow]",
        "tr": "[yellow]Arka kapak yazısı hazırlanamadı ({error}); boş bırakıldı.[/yellow]",
    },
    # --- review turn (src/repl.py) --------------------------------
    "review.prompt": {
        "en": (
            "[bold]The PDF is ready. Which pages have issues?[/bold] "
            "Write the page number and what's wrong (e.g. \"page 3 text: "
            "...\", \"hide page 5\"), or done to finish."
        ),
        "tr": (
            "[bold]PDF hazır. Hangi sayfalarda sorun var?[/bold] Sayfa "
            "numarasını ve sorunu yaz (ör. \"sayfa 3 metin: ...\", "
            "\"sayfa 5'i gizle\"), bitirmek için tamam yaz."
        ),
    },
    "review.done": {
        "en": "[green]Done — your book is in .book-gen/output/.[/green]",
        "tr": "[green]Tamamdır — kitabın .book-gen/output/ klasöründe.[/green]",
    },
    # --- agent progress indicator ------------------------------------
    # ``progress.<tool name>`` labels the long-running tools; any other
    # tool falls back to ``progress.working``.
    "progress.thinking": {"en": "Thinking…", "tr": "Düşünüyorum…"},
    "progress.working": {"en": "Working…", "tr": "Çalışıyorum…"},
    "progress.drafting": {"en": "Drafting…", "tr": "Taslak hazırlanıyor…"},
    "progress.generate_cover_illustration": {
        "en": "Generating the cover illustration…",
        "tr": "Kapak resmi üretiliyor…",
    },
    "progress.generate_page_illustration": {
        "en": "Generating the page illustration…",
        "tr": "Sayfa resmi üretiliyor…",
    },
    "progress.transcribe_page": {
        "en": "Reading the page…",
        "tr": "Sayfa okunuyor…",
    },
    "progress.render_book": {
        "en": "Building the PDF…",
        "tr": "PDF hazırlanıyor…",
    },
}


# Labels a draft uses to name its author ("Yazar: Poyraz"). All languages
# are matched: the draft's language can differ from the UI's.
AUTHOR_LABELS: dict[str, tuple[str, ...]] = {
    "en": ("author", "written by"),
    "tr": ("yazar", "yazan"),
}


# Replies that end the review turn ("nothing to fix"). All languages
# count: the user may answer in either.
REVIEW_DONE_ANSWERS: frozenset[str] = frozenset({
    "done", "ok", "okay", "none", "ship", "ship it", "nothing", "no",
    "tamam", "bitti", "yok", "sorun yok", "hayır", "hayir", "tamamdır",
})


def t(key: str, lang: str) -> str:
    """Look up ``key`` in ``lang``. Falls back to English if either
    the key is missing for the requested language or the language
    itself is unrecognised. Tests pin the parity so a missing key
    for a known language is treated as a bug, not a silent fallback."""
    bundle = _TRANSLATIONS.get(key, {})
    if lang in bundle:
        return bundle[lang]
    return bundle.get("en", "")


_SUPPORTED = ("tr", "en")


def detect_lang() -> str:
    """Pick the language for metadata prompts.

    Order of preference:

    1. ``LITTLEPRESS_LANG`` env var (explicit user override —
       handy for tests and shell-level pinning).
    2. ``locale.getlocale()`` (system default).
    3. English (safe baseline).

    Anything that prefix-matches a supported language wins
    (``tr_TR.UTF-8`` and ``Turkish_Türkiye`` both map to ``tr``).
    Locale-detection exceptions are swallowed — a startup crash
    here would break ``littlepress draft.pdf`` before the user
    sees anything."""
    override = os.environ.get("LITTLEPRESS_LANG", "")
    if override:
        match = _match_lang(override)
        if match:
            return match
    try:
        loc, _ = locale.getlocale()
    except Exception:  # noqa: BLE001 — locale call failures are non-fatal
        loc = None
    if loc:
        match = _match_lang(loc)
        if match:
            return match
    return "en"


def _match_lang(value: str) -> str | None:
    """Return the supported language code that prefix-matches
    ``value`` (case-insensitive on the leading ``tr``/``en``
    fragment, plus a special case for Windows' ``Turkish_*``
    locale shape). ``None`` if nothing matches."""
    lowered = value.lower()
    for code in _SUPPORTED:
        if lowered.startswith(code):
            return code
    if lowered.startswith("turkish"):
        return "tr"
    return None
