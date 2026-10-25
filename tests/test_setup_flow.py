"""The run-up from the metadata questions to the first review prompt is
code, not agent instructions: a live session stalled when the agent said
"I'll render now" and stopped. The LLM is only asked to draft text."""

import io
from pathlib import Path

from rich.console import Console

from src.draft import Draft, DraftPage
from src.setup_flow import finish_setup


class _LLM:
    def __init__(self, reply="A kitten waves at a new friend", error=None):
        self.reply = reply
        self.error = error
        self.requests: list[str] = []

    def chat(self, messages):
        self.requests.append(messages[-1]["content"])
        if self.error:
            raise self.error
        return self.reply


def _draft(tmp_path):
    return Draft(
        source_pdf=tmp_path / "x.pdf",
        title="Yavru Kedi",
        pages=[
            DraftPage(text="Bir gün zeytin adlı bir kedi varmış."),
            DraftPage(text="Yazar: Poyraz", hidden=True),
        ],
    )


class _Run:
    """Drives ``finish_setup`` with scripted answers and fakes."""

    def __init__(self, tmp_path, answers=(), llm=None, cover="poster",
                 back_cover="none", image=True, generate_result=None,
                 render_result="A5 book written to out.pdf"):
        self.draft = _draft(tmp_path)
        self.llm = llm or _LLM()
        self.generated: list[str] = []
        self.renders = 0
        self.buf = io.StringIO()
        answers = iter(answers)

        def generate(prompt):
            self.generated.append(prompt)
            if generate_result is not None:
                return generate_result
            self.draft.cover_image = Path("cover-abc.png")
            return "Cover illustration generated."

        def render():
            self.renders += 1
            return render_result

        self.ok = finish_setup(
            draft=self.draft,
            llm=self.llm,
            read_line=lambda: next(answers),
            console=Console(file=self.buf, force_terminal=False, width=200, no_color=True),
            lang="en",
            cover_choice=cover,
            back_cover_choice=back_cover,
            generate_cover=generate if image else None,
            render=render,
        )

    @property
    def out(self):
        return self.buf.getvalue()


def test_deterministic_choices_render_and_ask_for_review_without_the_llm(tmp_path):
    run = _Run(tmp_path)

    assert run.ok
    assert run.llm.requests == []
    assert run.renders == 1
    assert "Which pages have issues?" in run.out


def test_ai_cover_uses_the_drafted_prompt_on_enter(tmp_path):
    run = _Run(tmp_path, answers=[""], cover="ai")

    assert run.generated == ["A kitten waves at a new friend"]
    assert run.draft.cover_image == Path("cover-abc.png")
    assert run.renders == 1


def test_ai_cover_prompt_request_names_the_story_and_language(tmp_path):
    run = _Run(tmp_path, answers=["y"], cover="ai")

    request = run.llm.requests[0]
    assert "Bir gün zeytin adlı bir kedi varmış." in request
    assert "Yazar: Poyraz" not in request
    assert "English" in request


def test_ai_cover_takes_the_users_own_description_verbatim(tmp_path):
    run = _Run(tmp_path, answers=["  three kittens on a sofa "], cover="ai")

    assert run.generated == ["three kittens on a sofa"]


def test_ai_cover_skipped_on_no_falls_back_to_poster(tmp_path):
    run = _Run(tmp_path, answers=["n"], cover="ai")

    assert run.generated == []
    assert run.draft.cover_style == "poster"
    assert run.draft.cover_image is None


def test_ai_cover_without_an_image_provider_explains_and_uses_poster(tmp_path):
    run = _Run(tmp_path, cover="ai", image=False)

    assert run.llm.requests == []
    assert run.draft.cover_style == "poster"
    assert "/image-model" in run.out
    assert run.renders == 1


def test_ai_cover_draft_failure_uses_poster(tmp_path):
    run = _Run(tmp_path, cover="ai", llm=_LLM(error=RuntimeError("quota")))

    assert run.draft.cover_style == "poster"
    assert "quota" in run.out
    assert run.renders == 1


def test_failed_generation_uses_poster_and_says_why(tmp_path):
    run = _Run(tmp_path, answers=[""], cover="ai",
               generate_result="Cover generation failed: timeout. Tell the user.")

    assert run.draft.cover_style == "poster"
    assert "timeout" in run.out


def test_declined_cost_confirm_uses_poster(tmp_path):
    run = _Run(tmp_path, answers=[""], cover="ai",
               generate_result="User declined the cover generation.")

    assert run.draft.cover_style == "poster"
    assert "poster" in run.out


def test_drafted_reply_is_cleaned_to_one_unquoted_line(tmp_path):
    run = _Run(tmp_path, answers=[""], cover="ai",
               llm=_LLM(reply='\n"A kitten on a sofa"\nExtra notes'))

    assert run.generated == ["A kitten on a sofa"]


def test_ai_blurb_accepted_on_enter(tmp_path):
    run = _Run(tmp_path, answers=[""], back_cover="ai-draft",
               llm=_LLM(reply="Zeytin finds a friend."))

    assert run.draft.back_cover_text == "Zeytin finds a friend."
    assert "Bir gün zeytin" in run.llm.requests[0]


def test_ai_blurb_replaced_by_the_users_text(tmp_path):
    run = _Run(tmp_path, answers=["Kendi yazım."], back_cover="ai-draft")

    assert run.draft.back_cover_text == "Kendi yazım."


def test_ai_blurb_declined_leaves_it_empty(tmp_path):
    run = _Run(tmp_path, answers=["no"], back_cover="ai-draft")

    assert run.draft.back_cover_text == ""


def test_ai_blurb_failure_leaves_it_empty(tmp_path):
    run = _Run(tmp_path, back_cover="ai-draft", llm=_LLM(error=RuntimeError("down")))

    assert run.draft.back_cover_text == ""
    assert "down" in run.out


def test_render_failure_is_reported_and_skips_the_review_prompt(tmp_path):
    run = _Run(tmp_path, render_result="Render failed: disk full")

    assert not run.ok
    assert "disk full" in run.out
    assert "Which pages have issues?" not in run.out
