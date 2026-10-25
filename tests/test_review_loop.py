"""End-to-end review flow.

Load a draft → setup questions → the app renders and asks 'which pages
have issues?' → the user replies with a verbatim page-1 text override →
the agent calls apply_text_correction → the app re-renders and asks
again → the user says 'none' → the app closes the review without the
agent. The re-rendered PDF must contain the overridden text.
"""

import io
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import A5
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from rich.console import Console

from src import draft as draft_mod
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


def _write_pdf(tmp_path):
    pdf = tmp_path / "draft.pdf"
    c = rl_canvas.Canvas(str(pdf), pagesize=A5)
    img = tmp_path / "_src.png"
    Image.new("RGB", (80, 60), (255, 0, 0)).save(img)
    c.drawImage(ImageReader(str(img)), 50, 200, width=200, height=150)
    c.setFont("Helvetica", 14)
    c.drawString(50, 400, "the original text")
    c.showPage()
    c.save()
    return pdf


class _ScriptedLLM:
    """Replies with a scripted list of AgentResponse objects, one per
    ``turn`` call. Captures the tool names available each call so tests
    can assert on wiring."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list = []

    def turn(self, messages, tools):
        self.calls.append(
            {"messages": list(messages), "tools": [t.name for t in tools]}
        )
        return self._responses.pop(0)


def _tool_call(name, input_):
    return AgentResponse(
        content=[{"type": "tool_use", "id": f"t-{name}", "name": name, "input": input_}],
        stop_reason="tool_use",
    )


def _text(msg):
    return AgentResponse(content=[{"type": "text", "text": msg}], stop_reason="end_turn")


def _extract_pdf_text(pdf: Path) -> str:
    reader = PdfReader(str(pdf))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def test_review_loop_applies_user_correction_and_re_renders(tmp_path):
    pdf = _write_pdf(tmp_path)
    draft = draft_mod.from_pdf(pdf, tmp_path / ".book-gen" / "images")

    # Scripted agent turns — only the correction needs the agent:
    # apply_text_correction(page=1, text="OVERRIDE"), then a short reply.
    responses = [
        _tool_call(
            "apply_text_correction", {"page": 1, "text": "OVERRIDE"}
        ),
        _text("Page 1 updated — anything else?"),
    ]
    llm = _ScriptedLLM(responses)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    repl = Repl(
        read_line=_scripted(
            [
                # Deterministic metadata prompts before the agent turn.
                "2", "Story", "A", "n", "c", "a",
                "page 1 text: OVERRIDE",  # review turn 1 — correction
                "none",                    # review turn 2 — exit
            ]
        ),
        console=console,
        provider=find("anthropic"),
        session_root=tmp_path,
        llm_factory=lambda _spec, _key: llm,
    )
    repl.set_draft(draft)

    assert repl.run() == 0

    # Re-rendered PDF contains the user's verbatim override.
    rendered = tmp_path / ".book-gen" / "output" / "story.pdf"
    assert rendered.is_file(), "the app should have rendered a stable PDF"
    text = _extract_pdf_text(rendered)
    assert "OVERRIDE" in text, f"expected OVERRIDE in rendered PDF, got:\n{text}"

    # Assert the full apply_text_correction tool was available to the agent.
    names_seen = set()
    for call in llm.calls:
        names_seen.update(call["tools"])
    assert "apply_text_correction" in names_seen
    assert "restore_page" in names_seen
    assert "hide_page" in names_seen


def test_review_done_reply_is_handled_without_the_agent(tmp_path):
    pdf = _write_pdf(tmp_path)
    draft = draft_mod.from_pdf(pdf, tmp_path / ".book-gen" / "images")
    llm = _ScriptedLLM([])
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    repl = Repl(
        read_line=_scripted(["1", "Hikaye", "A", "h", "c", "a", "Tamam."]),
        console=console,
        provider=find("anthropic"),
        session_root=tmp_path,
        llm_factory=lambda _spec, _key: llm,
    )
    repl.set_draft(draft)

    assert repl.run() == 0
    assert llm.calls == []
    assert ".book-gen/output/" in buf.getvalue()


def test_after_the_review_ends_chat_goes_straight_to_the_agent(tmp_path):
    pdf = _write_pdf(tmp_path)
    draft = draft_mod.from_pdf(pdf, tmp_path / ".book-gen" / "images")
    llm = _ScriptedLLM([_text("Sure?")])
    repl = Repl(
        read_line=_scripted(["2", "T", "A", "n", "c", "a", "done", "done"]),
        console=Console(file=io.StringIO(), force_terminal=False),
        provider=find("anthropic"),
        session_root=tmp_path,
        llm_factory=lambda _spec, _key: llm,
    )
    repl.set_draft(draft)

    repl.run()

    # The second "done" is ordinary chat once the review is closed.
    assert len(llm.calls) == 1
