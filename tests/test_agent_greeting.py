"""The agent greeting hint is what the LLM sees on session start —
it's the single most load-bearing prompt in the project, because
the agent never re-reads it and builds the whole conversation on
top of it. These tests pin the invariants we want the hint to carry
so future tightening / rewording can't silently drop one.
"""

from __future__ import annotations

from src.repl import _AGENT_GREETING_HINT


def test_greeting_mentions_cover_step_and_its_three_options():
    """PR #48 follow-up (P3): a Claude session in the Yavru Dinozor
    run quietly defaulted to "which page's drawing do you want for
    the cover?" without surfacing the AI-generation or poster
    options. The greeting must spell out the three alternatives so
    the agent never forgets to offer them."""
    lowered = _AGENT_GREETING_HINT.lower()

    assert "cover" in lowered
    # Option (a) — page drawing. Check for the exact enumeration
    # label so the test fails if a rewrite deletes the whole bullet
    # (keywords like "page" / "drawing" appear elsewhere too).
    assert "(a)" in lowered
    # Option (b) — AI generation. Check the tool name, not just a
    # vague "generate" keyword.
    assert "(b)" in lowered
    assert "generate_cover_illustration" in lowered
    # Option (c) — poster (type-only).
    assert "(c)" in lowered
    assert "poster" in lowered


def test_greeting_flags_openai_only_gate_for_ai_cover():
    """``generate_cover_illustration`` is registered only on OpenAI
    (PR #41). When the active provider is different, the agent must
    know to direct the user to ``/model`` rather than pretend the
    tool exists. PR #49 review #4: split the substring check — the
    old `"openai" in lowered or "/model" in lowered` passed for any
    rewrite that kept one of the two words. Both must appear."""
    lowered = _AGENT_GREETING_HINT.lower()
    assert "openai" in lowered
    assert "/model" in lowered


def test_greeting_echoes_preserve_child_voice_guard_for_ai_cover():
    """PR #49 review #1 (critical): on non-OpenAI sessions the
    ``generate_cover_illustration`` tool description is invisible
    (the tool isn't registered). The greeting is the only surface
    that names the AI cover path there. Without the guard, an
    agent following the greeting could switch to OpenAI via
    ``/model`` and prompt the image API with paraphrased child
    text — exactly what PR #41's final review round was meant to
    prevent. The guard has to live on every surface that mentions
    the AI cover tool."""
    lowered = _AGENT_GREETING_HINT.lower()
    # A destruction-adjacent phrase — "own words" or
    # "don't paraphrase" or "do not paraphrase".
    assert (
        "own words" in lowered
        or "do not paraphrase" in lowered
        or "don't paraphrase" in lowered
        or "not paraphrase" in lowered
    )
    # And the "child" anchor, so the guard is tied to the child's
    # text specifically (not a generic "prompt carefully" line).
    assert "child" in lowered


def test_greeting_warns_about_openai_key_prompt_on_model_switch():
    """PR #49 review #2: switching from Anthropic / Gemini / Ollama
    to OpenAI triggers an interactive API-key prompt if no key is
    stored. The greeting advertises ``/model`` as a simple switch;
    the user surprise is avoidable if the hint names the prompt."""
    lowered = _AGENT_GREETING_HINT.lower()
    assert "key" in lowered
    # The key-prompt context — "stored", "prompted", "prompt for",
    # "asked", "enter".
    assert (
        "prompt" in lowered
        or "stored" in lowered
        or "asked" in lowered
        or "enter" in lowered
    )


def test_greeting_marks_slash_commands_as_non_translatable():
    """PR #49 review #3: the hint tells the LLM to switch to the
    user's language; on a Turkish session it might translate
    ``/model`` (the REPL slash command) into whatever the user's
    language renders it as, and the REPL won't recognise the
    translated token. Flag slash commands as literal."""
    lowered = _AGENT_GREETING_HINT.lower()
    # Some phrase that names slash commands + a literal/untranslated
    # marker.
    assert "slash" in lowered or "/" in _AGENT_GREETING_HINT
    assert (
        "literal" in lowered
        or "as-is" in lowered
        or "as is" in lowered
        or "untranslat" in lowered
        or "do not translate" in lowered
        or "keep" in lowered
    )


def test_greeting_option_a_points_at_select_cover_template():
    """PR #49 review #5: poster is framed as one of three top-level
    cover paths, but semantically poster is a *template* (alongside
    full-bleed / framed / portrait-frame / title-band-top). Option
    (a) should point at the ``select-cover-template`` skill so an
    LLM biased toward the greeting doesn't skip the middle three
    templates."""
    lowered = _AGENT_GREETING_HINT.lower()
    assert "select-cover-template" in lowered


def test_greeting_still_asks_agent_to_read_draft_first():
    """Pre-PR-#48 invariant that shouldn't regress: the first action
    is ``read_draft``. Pinned so a future rewrite doesn't strip it."""
    lowered = _AGENT_GREETING_HINT.lower()
    assert "read_draft" in lowered


def test_greeting_always_asks_whether_the_book_is_a_series():
    """P4 from the Yavru Dinozor second-run feedback — maintainer's
    call: ask the series question on every book, not only when the
    title parses as a pattern. "Yavru Dinozor - 1" is book 1 of an
    ongoing series; Poyraz plans book 2, 3, … so the first run must
    give the agent a chance to capture that.

    The hint must instruct the agent to ask, every time, without
    peeking at the title first."""
    lowered = _AGENT_GREETING_HINT.lower()
    assert "series" in lowered
    # "Always" / "every" marker so an LLM doesn't infer "only when
    # the title looks like it's part of a series."
    assert (
        "always" in lowered
        or "every book" in lowered
        or "regardless" in lowered
    )


def test_greeting_asks_for_volume_number_when_series_answer_is_yes():
    """The question has two parts: first "is this a series?" and
    then, only if yes, "which volume?". Pin both branches so the
    agent doesn't drop one half."""
    lowered = _AGENT_GREETING_HINT.lower()
    assert "volume" in lowered or "book number" in lowered or "which book" in lowered


def test_greeting_includes_metadata_review_step_before_render():
    """P5 from the Yavru Dinozor second-run feedback — the live run
    went straight from the last metadata step into ``render_book``
    without letting the user catch a typo in the title / author /
    series / cover / blurb. Agent must summarise everything and
    wait for approval *before* rendering. The summary step itself
    comes after layouts (layout decisions are part of what the
    user reviews)."""
    lowered = _AGENT_GREETING_HINT.lower()
    # Some wording that signals a review / confirm pass over the
    # metadata as a whole.
    assert "review" in lowered or "summarise" in lowered or "summarize" in lowered or "recap" in lowered
    # And the explicit "wait for approval" beat so the agent doesn't
    # only print a summary and barrel on.
    assert (
        "approve" in lowered
        or "confirm" in lowered
        or "let them correct" in lowered
        or "ask them" in lowered
    )


def test_greeting_review_step_comes_before_render_and_after_layouts():
    """PR #51 review #5 — the removed PLAN line, the test docstring,
    and the greeting body all gave different orderings for the
    review step. Pick one (after layouts, before render) and pin
    it with a regex so a later rewrite can't silently reorder."""
    hint = _AGENT_GREETING_HINT.lower()
    # "summaris" (covers both -se and -ze spellings) must appear
    # somewhere after the layouts word and before the render_book
    # mention. A regex ordering check catches a rewrite that keeps
    # the keywords but reorders them.
    import re
    match = re.search(r"layout.*summaris.*render", hint, re.DOTALL)
    assert match is not None, (
        "greeting must mention layouts → summarise → render in that "
        "order. Current text:\n" + hint
    )


def test_greeting_summarise_step_demands_verbatim_read_back():
    """PR #51 review #4 — "SUMMARISE the metadata" in a Turkish
    session can drift into loose translation or paraphrase of the
    title / author. Title and author are child-authored
    (preserve-child-voice). The hint must tell the agent to quote
    stored values verbatim, not rephrase them during the summary."""
    lowered = _AGENT_GREETING_HINT.lower()
    # Some verbatim / exact / quote-style wording near the
    # summarise step.
    assert (
        "verbatim" in lowered
        or "quote" in lowered
        or "exactly as" in lowered
        or "do not translate" in lowered
        or "do not paraphrase" in lowered
    )


def test_greeting_asks_for_back_cover_blurb():
    """P5 — the live run skipped back-cover text entirely; older
    versions asked for it. Put the prompt back in the greeting so
    the agent covers the full metadata scope (title / author /
    series / cover / back cover)."""
    lowered = _AGENT_GREETING_HINT.lower()
    assert "back cover" in lowered or "back-cover" in lowered
    # PR #51 review #6 — the old assertion accepted the lone word
    # "short", which appears in any number of unrelated places.
    # Require a multi-word phrase that locks the framing.
    assert (
        "short blurb" in lowered
        or "brief blurb" in lowered
        or "one or two sentence" in lowered
        or "one-or-two sentence" in lowered
    )


def test_greeting_back_cover_bullet_carries_preserve_child_voice_guard():
    """PR #51 review #1 (critical) — back-cover text is explicitly
    child-authored per CLAUDE.md. The greeting's "in the child's
    voice" can be misread by a primed LLM as permission to compose
    a blurb *in the child's style* rather than to transcribe the
    user's exact words. ``set_metadata`` has no confirm gate for
    child-voice fields, so the greeting is the only enforcement
    surface. Mirror the guard from option (b) of the cover step."""
    lowered = _AGENT_GREETING_HINT.lower()

    # The blurb bullet must explicitly forbid the agent from
    # inventing / paraphrasing the text. We check for the bullet
    # location first (back-cover mention) then the guard nearby.
    assert "back cover" in lowered or "back-cover" in lowered

    # Same shape of phrasing the AI-cover guard uses.
    assert (
        "do not invent" in lowered
        or "don't invent" in lowered
        or "do not paraphrase" in lowered
        or "don't paraphrase" in lowered
    )
    # And "preserve-child-voice" named so the link to CLAUDE.md is
    # unambiguous.
    assert "preserve-child-voice" in lowered
