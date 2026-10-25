"""From the metadata answers to the first review prompt, in code.

Leaving these steps to the agent's instructions failed live: the model
said "I'll render the PDF now" and stopped, so the user sat at an idle
prompt. The LLM is only asked to draft text (an AI cover description,
an AI back-cover blurb); showing the draft, generating, rendering and
asking for the review are deterministic.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from src.draft import Draft
from src.metadata_i18n import t
from src.metadata_prompts import _NO_TOKENS, _YES_TOKENS

_LANGUAGE_NAMES = {"en": "English", "tr": "Turkish"}

_COVER_REQUEST = (
    "Write a one-line prompt, in {language}, for an image model that will "
    "paint the cover of this children's picture book. Describe a scene in "
    "your own words from the story's characters and themes; do not quote "
    "or paraphrase the child's sentences. No text or lettering in the "
    "picture. Reply with the prompt only.\n\nStory pages:\n{pages}"
)

_BLURB_REQUEST = (
    "Write a one- or two-sentence back-cover blurb, in {language}, for this "
    "children's picture book. Ground it only in what happens in the pages; "
    "no clichés about childhood or imagination. Reply with the blurb only."
    "\n\nStory pages:\n{pages}"
)


def finish_setup(
    *,
    draft: Draft,
    llm,
    read_line: Callable[[], str],
    console: Console,
    lang: str,
    cover_choice: str,
    back_cover_choice: str,
    generate_cover: Callable[[str], str] | None,
    render: Callable[[], str],
) -> bool:
    """Run any AI branches, render, and ask the review question.
    Returns whether the render succeeded."""
    if cover_choice == "ai":
        _ai_cover(draft, llm, read_line, console, lang, generate_cover)
    if back_cover_choice == "ai-draft":
        _ai_back_cover(draft, llm, read_line, console, lang)
    return render_and_ask(console, lang, render)


def render_and_ask(console: Console, lang: str, render: Callable[[], str]) -> bool:
    """Render, then ask which pages need fixing. Returns whether the
    render succeeded."""
    message = render()
    if not message.startswith("A5 book written"):
        console.print(f"[red]{escape(message)}[/red]")
        return False
    console.print(f"[dim]{escape(message)}[/dim]")
    console.print(t("review.prompt", lang))
    return True


def _ai_cover(draft, llm, read_line, console, lang, generate_cover) -> None:
    if generate_cover is None:
        console.print(t("cover.ai_unavailable", lang))
        _use_poster(draft)
        return
    try:
        prompt = _draft_text(_COVER_REQUEST, draft, llm, console, lang)
    except Exception as e:  # noqa: BLE001 — any provider failure falls back
        console.print(t("cover.ai_failed", lang).format(error=escape(str(e))))
        _use_poster(draft)
        return
    prompt = _review(t("cover.ai_review", lang), prompt, read_line, console, lang)
    if prompt is None:
        _use_poster(draft)
        return
    before = draft.cover_image
    result = generate_cover(prompt)
    if draft.cover_image != before:
        return
    if result.startswith("User declined"):
        console.print(t("cover.ai_declined", lang))
    else:
        console.print(t("cover.ai_failed", lang).format(error=escape(result)))
    _use_poster(draft)


def _ai_back_cover(draft, llm, read_line, console, lang) -> None:
    try:
        blurb = _draft_text(_BLURB_REQUEST, draft, llm, console, lang)
    except Exception as e:  # noqa: BLE001 — any provider failure falls back
        console.print(t("back_cover.ai_failed", lang).format(error=escape(str(e))))
        draft.back_cover_text = ""
        return
    blurb = _review(t("back_cover.ai_review", lang), blurb, read_line, console, lang)
    draft.back_cover_text = blurb or ""


def _draft_text(request: str, draft, llm, console, lang) -> str:
    pages = "\n".join(p.text.strip() for p in draft.pages if not p.hidden and p.text.strip())
    content = request.format(language=_LANGUAGE_NAMES.get(lang, "English"), pages=pages)
    spinner = "dots" if console.encoding.lower().startswith("utf") else "line"
    with console.status(t("progress.drafting", lang), spinner=spinner):
        reply = llm.chat([{"role": "user", "content": content}])
    lines = [line.strip().strip("\"“”'") for line in reply.splitlines()]
    return next((line for line in lines if line), "")


def _review(template: str, text: str, read_line, console, lang: str) -> str | None:
    """Show a drafted text. Enter / yes keeps it, no drops it (``None``),
    anything else is the user's own replacement, used verbatim."""
    console.print(template.format(text=escape(text)))
    answer = read_line().strip()
    if not answer or answer.lower() in _YES_TOKENS.get(lang, _YES_TOKENS["en"]):
        return text
    if answer.lower() in _NO_TOKENS.get(lang, _NO_TOKENS["en"]):
        return None
    return answer


def _use_poster(draft: Draft) -> None:
    draft.cover_image = None
    draft.cover_style = "poster"
