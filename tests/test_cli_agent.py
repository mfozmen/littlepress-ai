"""CLI + agent integration: PDF arg + real provider → setup, render, review."""

import io

from PIL import Image
from reportlab.lib.pagesizes import A5
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from rich.console import Console

from src.agent import AgentResponse
from src.providers.llm import find
from src.repl import Repl


def _scripted(lines):
    it = iter(lines)

    def read():
        try:
            return next(it)
        except StopIteration as e:
            raise EOFError from e

    return read


# Minimum accepted answers to the deterministic metadata prompts
# that run after a pre-loaded draft (title / author / series / cover /
# back-cover). Tests exercising the run()-with-set_draft path need
# these scripted before any post-greeting user inputs.
# Language (English) first, then title, author, series, cover, back cover.
_METADATA_ANSWERS = ("2", "T", "A", "n", "c", "a")


def _write_pdf(tmp_path):
    pdf = tmp_path / "draft.pdf"
    c = rl_canvas.Canvas(str(pdf), pagesize=A5)
    src = tmp_path / "_src.png"
    Image.new("RGB", (80, 60), (255, 0, 0)).save(src)
    c.drawImage(ImageReader(str(src)), 50, 200, width=200, height=150)
    c.setFont("Helvetica", 14)
    c.drawString(50, 400, "once upon a time")
    c.showPage()
    c.save()
    return pdf


class _StubLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list = []

    def turn(self, messages, tools):
        self.calls.append(
            {"messages": [m for m in messages], "tools": [t.name for t in tools]}
        )
        if not self._responses:  # the agent's one-off "keep going" nudge
            return AgentResponse(content=[], stop_reason="end_turn")
        return self._responses.pop(0)


def test_preloaded_draft_is_rendered_and_reviewed_without_the_agent(tmp_path):
    """Setup, render and the review question are code: a live agent once
    said "I'll render now" and stopped. No agent turn runs before the
    user's first review reply."""
    from src import draft as draft_mod

    pdf = _write_pdf(tmp_path)
    draft = draft_mod.from_pdf(pdf, tmp_path / ".book-gen" / "images")
    llm = _StubLLM([])

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    repl = Repl(
        read_line=_scripted(list(_METADATA_ANSWERS)),
        console=console,
        provider=find("anthropic"),
        session_root=tmp_path,
        llm_factory=lambda _spec, _key: llm,
    )
    repl.set_draft(draft)

    assert repl.run() == 0
    assert llm.calls == []
    assert (tmp_path / ".book-gen" / "output" / "t.pdf").is_file()
    assert "Which pages have issues?" in buf.getvalue()


def test_agent_does_not_greet_on_offline_provider(tmp_path):
    """NullProvider means no LLM — the REPL opens quietly, no tool calls."""
    from src import draft as draft_mod

    pdf = _write_pdf(tmp_path)
    draft = draft_mod.from_pdf(pdf, tmp_path / ".book-gen" / "images")

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    repl = Repl(
        read_line=_scripted([]),
        console=console,
        provider=find("none"),
        session_root=tmp_path,
    )
    repl.set_draft(draft)

    assert repl.run() == 0
    # Nothing agent-shaped should have printed.
    assert "I see" not in buf.getvalue()


def test_agent_failure_in_the_review_turn_does_not_crash_repl(tmp_path):
    from src import draft as draft_mod

    pdf = _write_pdf(tmp_path)
    draft = draft_mod.from_pdf(pdf, tmp_path / ".book-gen" / "images")

    class _Broken:
        def turn(self, _messages, _tools):
            raise RuntimeError("rate limited")

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    repl = Repl(
        read_line=_scripted([*_METADATA_ANSWERS, "page 1 text: hi"]),
        console=console,
        provider=find("anthropic"),
        session_root=tmp_path,
        llm_factory=lambda _spec, _key: _Broken(),
    )
    repl.set_draft(draft)

    assert repl.run() == 0
    assert "rate limited" in buf.getvalue()


def test_agent_typo_fix_auto_applies(tmp_path):
    """End-to-end: agent calls propose_typo_fix, the fix is applied
    immediately without a y/n gate."""
    from src import draft as draft_mod

    pdf = _write_pdf(tmp_path)
    draft = draft_mod.from_pdf(pdf, tmp_path / ".book-gen" / "images")
    draft.pages[0].text = "the dragn was sad"

    llm = _StubLLM(
        [
            AgentResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "propose_typo_fix",
                        "input": {
                            "page": 1,
                            "before": "dragn",
                            "after": "dragon",
                            "reason": "spelling",
                        },
                    }
                ],
                stop_reason="tool_use",
            ),
            AgentResponse(
                content=[{"type": "text", "text": "Great, fixed!"}],
                stop_reason="end_turn",
            ),
        ]
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    repl = Repl(
        read_line=_scripted([*_METADATA_ANSWERS, "page 1 says dragn", "/exit"]),
        console=console,
        provider=find("anthropic"),
        session_root=tmp_path,
        llm_factory=lambda _spec, _key: llm,
    )
    repl.set_draft(draft)

    assert repl.run() == 0
    # Fix was auto-applied — no y/n required — and the app re-rendered
    # and asked again without the agent calling render_book.
    assert draft.pages[0].text == "the dragon was sad"
    assert buf.getvalue().count("Which pages have issues?") == 2


def test_setup_renders_the_pdf_and_booklet(tmp_path):
    """The app renders both PDFs itself right after the setup questions."""
    from src import draft as draft_mod

    pdf = _write_pdf(tmp_path)
    draft = draft_mod.from_pdf(pdf, tmp_path / ".book-gen" / "images")

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    repl = Repl(
        # "My Book" as title + page-drawing cover (option "a") so the
        # rendered filename ends up ``my_book.pdf`` and the cover
        # renderer has the page drawing to lay out.
        read_line=_scripted(["2", "My Book", "A", "n", "a", "a"]),
        console=console,
        provider=find("anthropic"),
        session_root=tmp_path,
        llm_factory=lambda _spec, _key: _StubLLM([]),
    )
    repl.set_draft(draft)

    assert repl.run() == 0
    a5 = tmp_path / ".book-gen" / "output" / "my_book.pdf"
    booklet = tmp_path / ".book-gen" / "output" / "my_book_A4_booklet.pdf"
    assert a5.stat().st_size > 0
    assert booklet.stat().st_size > 0


def test_ai_cover_is_drafted_confirmed_and_generated_by_the_app(tmp_path):
    """AI cover: the LLM only drafts the description; showing it, the
    cost confirm and the generation are the app's."""
    from src import draft as draft_mod

    pdf = _write_pdf(tmp_path)
    draft = draft_mod.from_pdf(pdf, tmp_path / ".book-gen" / "images")

    class _ChatLLM(_StubLLM):
        def chat(self, _messages):
            return "A red kite over a hill"

    class _Images:
        LABEL = "Fake"
        COST_USD = {"low": 0.01, "medium": 0.05, "high": 0.17}

        def __init__(self):
            self.prompts = []

        def generate(self, prompt, output_path, size, quality):
            self.prompts.append(prompt)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (60, 80), (0, 0, 255)).save(output_path)

    images = _Images()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    repl = Repl(
        # English, title, author, no series, AI cover, empty back cover,
        # keep the drafted description, then yes to the cost confirm.
        read_line=_scripted(["2", "T", "A", "n", "b", "a", "", "y"]),
        console=console,
        provider=find("anthropic"),
        session_root=tmp_path,
        llm_factory=lambda _spec, _key: _ChatLLM([]),
    )
    repl.set_draft(draft)
    repl._image_provider = images
    repl._refresh_image_provider = lambda: None  # keep the fake across run()

    assert repl.run() == 0
    assert images.prompts == ["A red kite over a hill"]
    assert draft.cover_image is not None and draft.cover_image.is_file()
    assert "Which pages have issues?" in buf.getvalue()
