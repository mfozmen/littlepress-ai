"""The agent's instructions for the review turn. Setup, rendering and the
review question are code (``src/setup_flow.py``); the agent only turns the
user's free-form corrections into tool calls."""

from src.repl import _build_review_context


def test_review_context_names_the_reply_language():
    assert "Reply in Turkish" in _build_review_context("tr")
    assert "Reply in English" in _build_review_context("en")


def test_review_context_maps_corrections_to_tools():
    context = _build_review_context("en")

    for tool in ("apply_text_correction", "restore_page", "hide_page", "choose_layout"):
        assert tool in context


def test_review_context_keeps_corrections_verbatim():
    assert "do NOT paraphrase" in _build_review_context("en")


def test_review_context_leaves_rendering_to_the_app():
    context = _build_review_context("en")

    assert "Do NOT render" in context
    assert "render_book" not in context


def test_review_context_ends_where_the_users_reply_is_appended():
    assert _build_review_context("en").endswith("The user says:\n")


def test_review_context_has_no_turkish_tokens():
    # CLAUDE.md: Turkish lives only in src/metadata_i18n.py.
    context = _build_review_context("tr").lower()

    for token in ("tamam", " yok", "sayfa", "evet"):
        assert token not in context
