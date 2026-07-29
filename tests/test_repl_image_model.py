"""Multi-provider sessions — the REPL's ``_image_provider`` slot
and the ``/image-model`` slash command.

Reported 2026-04-28: today the active LLM is a single global
setting (``/model``). The maintainer's workflow is Claude as the
chat agent + OpenAI for ``generate_cover_illustration`` —
switching between them required toggling ``/model`` mid-session,
which dropped the chat context. PLAN's option (b): decouple the
image provider from the chat provider with a separate slot, set
by ``/image-model``.

This file pins the behavioural contract of that slot:
``_build_agent`` registers image tools based on the slot, not the
chat provider's name. The existing
``test_generate_cover_illustration_*`` tests in
``tests/test_repl_tools.py`` cover the implicit auto-wire path
(chat=OpenAI + key → image tools register); these tests cover the
explicit path (chat=anything + image-provider slot set → image
tools register) plus the slash-command surface.
"""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from src.providers.image import ImageProvider
from src.providers.llm import find
from src.repl import Repl


class _FakeImageProvider:
    """Minimal ``ImageProvider`` stand-in for tests — never actually
    generates anything; the assertion target is whether the tool was
    registered, not whether the API call succeeded. Other tests in
    ``test_agent_tools.py`` cover the actual generate() path."""

    def generate(
        self,
        prompt: str,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
    ) -> Path:
        raise NotImplementedError("FakeImageProvider.generate is unused")


def _repl(tmp_path, *, provider_name: str | None, api_key: str | None = None):
    """Build a Repl with a pre-selected chat provider — same pattern
    as ``tests/test_repl_tools.py``'s ``_repl`` helper."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)

    def _read():
        raise EOFError

    def _llm_factory(_spec, _key):
        class _Stub:
            pass
        return _Stub()

    provider = find(provider_name) if provider_name else None
    repl = Repl(
        read_line=_read,
        console=console,
        provider=provider,
        session_root=tmp_path,
        llm_factory=_llm_factory,
    )
    if api_key is not None:
        repl._api_key = api_key
        repl._agent = repl._build_agent()
    return repl


def _tool_names(repl: Repl) -> set[str]:
    return {t.name for t in repl._agent._tool_list}


# --- core slot behaviour ----------------------------------------------


def test_image_provider_slot_defaults_to_none(tmp_path):
    """Fresh Repl has no image provider configured — the slot is
    None until /image-model sets it (or chat=OpenAI auto-wires)."""
    repl = _repl(tmp_path, provider_name="anthropic", api_key="sk-ant-test")

    assert repl._image_provider is None


def test_image_tools_register_when_slot_is_set_with_non_openai_chat(tmp_path):
    """Core multi-provider use case: chat is Claude (Anthropic),
    image provider is OpenAI. The image tools must register because
    the SLOT is set — NOT because the chat provider happens to be
    OpenAI. Today's code conflates the two; this test pins the
    decoupled behaviour.

    Setting ``_image_provider_label`` alongside the instance is the
    contract for "explicit configuration" — without the label,
    ``_refresh_image_provider`` would treat the slot as an
    abandoned auto-wire and clear it (the auto-wire keeps the
    slot in sync with the chat-provider state for backwards
    compat). ``/image-model`` (real entry point) sets both."""
    repl = _repl(tmp_path, provider_name="anthropic", api_key="sk-ant-test")
    repl._image_provider = _FakeImageProvider()
    repl._image_provider_label = "openai"  # mark as explicit
    # Rebuild the agent so the slot change is reflected in the tool list.
    repl._agent = repl._build_agent()

    tools = _tool_names(repl)
    assert "generate_cover_illustration" in tools, (
        "image-provider slot set → cover tool must register even when "
        "chat provider is Anthropic"
    )
    assert "generate_page_illustration" in tools, (
        "image-provider slot set → page illustration tool must register "
        "even when chat provider is Anthropic"
    )


def test_image_tools_omitted_when_slot_is_unset(tmp_path):
    """Conversely: no image provider configured → no image tools,
    regardless of chat provider. ``anthropic`` is the simplest case
    (no auto-wire path)."""
    repl = _repl(tmp_path, provider_name="anthropic", api_key="sk-ant-test")
    # Slot left at its default (None) — no /image-model invocation.

    tools = _tool_names(repl)
    assert "generate_cover_illustration" not in tools
    assert "generate_page_illustration" not in tools


def test_openai_chat_auto_wires_image_provider_for_backward_compat(tmp_path):
    """Users on OpenAI chat today get image tools registered
    automatically (no /image-model required). Preserve that: when
    chat=OpenAI with a key, the auto-wire populates the slot so
    image tools light up out of the box. The slot is the source of
    truth in ``_build_agent``; auto-wire is the source of the
    initial slot value."""
    repl = _repl(tmp_path, provider_name="openai", api_key="sk-test")

    # The slot is populated by the auto-wire path, not left at None.
    assert repl._image_provider is not None, (
        "chat=OpenAI + key must auto-populate the image-provider slot "
        "so existing single-provider users don't need to run "
        "/image-model to get image tools back"
    )
    tools = _tool_names(repl)
    assert "generate_cover_illustration" in tools
    assert "generate_page_illustration" in tools


# --- /image-model slash command ---------------------------------------


def _interactive_repl(
    tmp_path,
    *,
    inputs: list[str],
    chat_provider: str = "anthropic",
    chat_key: str = "sk-ant-test",
    validate=None,
):
    """Build a Repl that consumes ``inputs`` line by line — for
    driving the ``/image-model`` slash command's interactive
    key-entry flow."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    it = iter(inputs)

    def _read():
        try:
            return next(it)
        except StopIteration as e:
            raise EOFError from e

    def _llm_factory(_spec, _key):
        class _Stub:
            pass
        return _Stub()

    repl = Repl(
        read_line=_read,
        console=console,
        provider=find(chat_provider),
        session_root=tmp_path,
        llm_factory=_llm_factory,
        validate=validate,
    )
    repl._api_key = chat_key
    repl._agent = repl._build_agent()
    return repl, buf


def test_image_model_command_with_no_arg_shows_current_state(tmp_path):
    """``/image-model`` alone reports the current image-provider
    state. On a fresh Anthropic session the slot is empty — say so
    and explain the usage.

    The "openai" assertion below is tight on purpose: an earlier
    loose form (``"openai" in out_lower``) silently passed because
    the unconditional usage hint contains the literal phrase
    ``/image-model openai`` regardless of current state — so the
    test would still pass even if the current-state line broke.
    Pinning the literal command form ``"/image-model openai"``
    means the assertion only holds when the usage block actually
    rendered, not from any prose mentioning "openai" in passing.
    Same recurring vacuous-assertion pattern as PR #87."""
    repl, buf = _interactive_repl(tmp_path, inputs=["/image-model", "/exit"])
    repl.run()

    out = buf.getvalue()
    # Status line surfaces "not configured" — the empty-state signal.
    out_lower = out.lower()
    assert (
        "no image" in out_lower
        or "not set" in out_lower
        or "none configured" in out_lower
        or "not configured" in out_lower
    ), f"/image-model with no arg must surface an empty-state status: {out!r}"
    # Usage hint surfaces the literal ``/image-model openai`` command —
    # not just the substring "openai" which a stale current-state line
    # could carry without the hint rendering at all.
    assert "/image-model openai" in out, (
        f"/image-model with no arg must print the literal usage hint "
        f"line containing /image-model openai; got {out!r}"
    )


def test_image_model_openai_prompts_for_key_and_sets_slot(tmp_path):
    """``/image-model openai`` walks the user through key entry +
    validation and populates the slot on success. Chat remains
    Anthropic; image-tools register because the slot is now set."""
    validate_calls: list[tuple[str, str]] = []

    def _validate(spec, key):
        # Pretend the key is valid.
        validate_calls.append((spec.name, key))

    repl, _ = _interactive_repl(
        tmp_path,
        inputs=[
            "/image-model openai",
            "sk-openai-image-test",  # API key entry
            "/exit",
        ],
        validate=_validate,
    )
    repl.run()

    assert repl._image_provider is not None, (
        "/image-model openai must populate the image-provider slot"
    )
    assert repl._image_provider_label == "openai", (
        "label must mark the slot as explicit so a later chat switch "
        "doesn't clear it via auto-wire reconciliation"
    )
    # Validator was called for the openai spec with the entered key.
    assert ("openai", "sk-openai-image-test") in validate_calls
    # Image tools now register even though chat is Anthropic.
    tools = _tool_names(repl)
    assert "generate_cover_illustration" in tools
    assert "generate_page_illustration" in tools


def test_image_model_none_clears_the_slot(tmp_path):
    """``/image-model none`` removes the image provider — useful
    when the user wants to turn off accidental image-tool
    invocations or when their key got rotated. The slot, the
    label, and the image tools all go away."""
    repl, _ = _interactive_repl(
        tmp_path,
        inputs=[
            "/image-model none",
            "/exit",
        ],
    )
    # Seed an explicit image provider so the clear has something to
    # remove.
    repl._image_provider = _FakeImageProvider()
    repl._image_provider_label = "openai"
    repl._agent = repl._build_agent()
    assert "generate_cover_illustration" in _tool_names(repl)  # sanity

    repl.run()

    assert repl._image_provider is None
    assert "generate_cover_illustration" not in _tool_names(repl)


def test_image_model_none_actually_clears_when_chat_is_openai(tmp_path):
    """Regression for the #1 bug surfaced by PR #88's reviewer:
    when chat=OpenAI is active, ``/image-model none`` set the
    label back to ``None`` and rebuilt the agent — but
    ``_refresh_image_provider`` saw ``label is None`` + chat=openai
    + key and AUTO-WIRED the slot AGAIN from the chat key, undoing
    the clear. The user got a green "Image provider cleared"
    message but the image tools were still registered.

    The fix must make ``/image-model none`` an EXPLICIT off state
    that auto-wire respects — e.g. a sentinel label ``"none"``
    that ``_refresh_image_provider`` reads as "explicitly off, do
    not auto-wire". Pinned here so the bug can't sneak back in."""
    # Seed with chat=OpenAI + key — the original auto-wire fires.
    repl, _ = _interactive_repl(
        tmp_path,
        inputs=[
            "/image-model none",
            "/exit",
        ],
        chat_provider="openai",
        chat_key="sk-openai-chat-test",
    )
    # Sanity: auto-wire populated the slot at construction time.
    assert repl._image_provider is not None, (
        "fixture precondition: chat=OpenAI auto-wires the slot"
    )
    assert "generate_cover_illustration" in _tool_names(repl)

    repl.run()

    # The clear must STICK even on a chat=OpenAI session — the
    # explicit user choice ("off") wins over the implicit auto-
    # wire.
    assert repl._image_provider is None, (
        "/image-model none must clear the slot even when chat is "
        "OpenAI; auto-wire must respect the explicit off choice"
    )
    assert "generate_cover_illustration" not in _tool_names(repl)
    assert "generate_page_illustration" not in _tool_names(repl)


def test_image_model_setup_persists_to_session_json(tmp_path):
    """The image provider must survive a REPL restart — same shape
    as the chat provider. After ``/image-model openai`` returns,
    ``session.json`` records ``image_provider = "openai"``.
    Without persistence the user has to re-enter the key every
    launch, which defeats the multi-provider workflow."""
    import json

    def _validate(spec, key):
        pass  # accept

    repl, _ = _interactive_repl(
        tmp_path,
        inputs=[
            "/image-model openai",
            "sk-openai-image-test",
            "/exit",
        ],
        validate=_validate,
    )
    repl.run()

    session_file = tmp_path / ".book-gen" / "session.json"
    assert session_file.is_file(), "session.json must exist after /image-model"
    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data.get("image_provider") == "openai", (
        f"session.json must record the image provider; got {data!r}"
    )


def test_image_model_none_persists_cleared_state(tmp_path):
    """``/image-model none`` persists as the SENTINEL ``"none"`` so
    the explicit-off state survives a restart. Persisting as
    ``null`` would let the launch-time auto-wire re-fire on a
    chat=OpenAI session and undo the clear — exactly the bug
    fixed in this round. The sentinel is the tri-state marker
    that distinguishes "never configured" (``null``) from
    "explicitly off" (``"none"``)."""
    import json

    repl, _ = _interactive_repl(
        tmp_path,
        inputs=[
            "/image-model none",
            "/exit",
        ],
    )
    # Seed an explicit slot so /image-model none has something to
    # clear (otherwise the cmd no-ops and might not touch the
    # session file).
    repl._image_provider = _FakeImageProvider()
    repl._image_provider_label = "openai"
    repl._agent = repl._build_agent()

    repl.run()

    session_file = tmp_path / ".book-gen" / "session.json"
    assert session_file.is_file()
    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data.get("image_provider") == "none", (
        f"cleared state must persist as the sentinel 'none' (so the "
        f"next launch's auto-wire skips); got {data!r}"
    )


def test_image_model_restores_from_session_on_launch(tmp_path):
    """On launch, a session.json with ``image_provider = "openai"``
    plus an OpenAI key in the keyring must restore the slot so the
    image tools are available without re-running ``/image-model``.

    Pinned via direct session-file seeding + a keyring stub —
    avoids touching the real OS keychain in tests."""
    import json

    # Seed session.json with a separate chat provider (anthropic)
    # plus image_provider = openai.
    session_dir = tmp_path / ".book-gen"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session.json").write_text(
        json.dumps({"provider": "anthropic", "image_provider": "openai"}),
        encoding="utf-8",
    )

    # Stub the keyring loader so the test doesn't touch the real
    # OS keychain. The Repl uses ``keyring_store.load_key(name)``;
    # patching at module level intercepts it.
    import src.keyring_store as ks
    import src.repl as repl_mod

    saved_load_key = ks.load_key
    saved_repl_load_key = repl_mod.keyring_store.load_key

    def _fake_load_key(name: str):
        return "sk-openai-image-test" if name == "openai" else None

    ks.load_key = _fake_load_key
    repl_mod.keyring_store.load_key = _fake_load_key
    try:
        repl = _repl(tmp_path, provider_name="anthropic", api_key="sk-ant-test")
        # Trigger the restore path explicitly — the implementation
        # exposes this via ``_restore_image_provider_from_session``.
        repl._restore_image_provider_from_session()
        repl._agent = repl._build_agent()
    finally:
        ks.load_key = saved_load_key
        repl_mod.keyring_store.load_key = saved_repl_load_key

    assert repl._image_provider_label == "openai", (
        "session.json + keyring key must restore the image-provider "
        "slot on launch"
    )
    assert repl._image_provider is not None
    tools = _tool_names(repl)
    assert "generate_cover_illustration" in tools
    assert "generate_page_illustration" in tools


def test_run_rebuilds_agent_after_restoring_image_provider_from_session(tmp_path):
    """Regression for the #3 issue surfaced by PR #88's reviewer:
    when ``Repl(...)`` is constructed with a preset provider,
    ``__init__`` builds the agent ONCE with the slot empty. Then
    ``run()`` restores the image-provider label from session.json,
    but if the agent isn't rebuilt after the restore the image
    tools don't register until the next ``_build_agent`` trigger
    (e.g. ``/model``).

    The CLI flow doesn't hit this today (it passes
    ``provider=None`` and ``run()`` rebuilds via ``_activate``),
    but tests and any future direct-construction path do. Pinned
    so ``run()`` always rebuilds after restore."""
    import json

    # Seed session.json with explicit image_provider = openai.
    session_dir = tmp_path / ".book-gen"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session.json").write_text(
        json.dumps({"provider": "anthropic", "image_provider": "openai"}),
        encoding="utf-8",
    )

    import src.keyring_store as ks
    import src.repl as repl_mod

    saved_load_key = ks.load_key
    saved_repl_load_key = repl_mod.keyring_store.load_key

    def _fake_load_key(name: str):
        return "sk-openai-image-test" if name == "openai" else None

    ks.load_key = _fake_load_key
    repl_mod.keyring_store.load_key = _fake_load_key
    try:
        repl, _ = _interactive_repl(tmp_path, inputs=["/exit"])
        # __init__ ran _build_agent with no slot — image tools absent.
        assert "generate_cover_illustration" not in _tool_names(repl), (
            "fixture precondition: agent built without image tools"
        )

        repl.run()
    finally:
        ks.load_key = saved_load_key
        repl_mod.keyring_store.load_key = saved_repl_load_key

    # After run() — restore populated the slot AND the agent was
    # rebuilt so the image tools register.
    assert repl._image_provider is not None
    assert repl._image_provider_label == "openai"
    assert "generate_cover_illustration" in _tool_names(repl), (
        "run() must rebuild the agent after restoring the image "
        "provider — without the rebuild, the tools stay missing "
        "until the next _build_agent trigger"
    )


def test_image_model_openai_aborts_cleanly_on_empty_key(tmp_path):
    """Pasting nothing at the key prompt must abort — no half-
    configured slot. Slot stays whatever it was before."""
    repl, buf = _interactive_repl(
        tmp_path,
        inputs=[
            "/image-model openai",
            "",  # empty key
            "/exit",
        ],
    )

    repl.run()

    out_lower = buf.getvalue().lower()
    # Slot still unconfigured.
    assert repl._image_provider is None
    assert repl._image_provider_label is None
    # Some indication that the call was aborted.
    assert "aborted" in out_lower or "cancel" in out_lower or "no key" in out_lower


# --- session restore edge cases ----------------------------------------


def test_explicit_off_survives_a_restart(tmp_path):
    """``/image-model none`` persists the ``"none"`` sentinel. On the
    next launch the sentinel comes back so the chat=OpenAI auto-wire
    stays skipped — the user's explicit off isn't silently undone."""
    from src import session as session_mod

    session_mod.save(
        tmp_path, session_mod.Session(provider="openai", image_provider="none")
    )
    repl = _repl(tmp_path, provider_name="anthropic", api_key="sk-ant-test")

    repl._restore_image_provider_from_session()

    assert repl._image_provider is None
    assert repl._image_provider_label == "none"


def test_saved_openai_image_provider_without_a_stored_key_stays_empty(tmp_path):
    """The session remembers openai but the key is gone (``/logout``,
    cleared keychain). Leave the slot empty rather than constructing a
    provider that would 401 on the first illustration."""
    from src import session as session_mod

    session_mod.save(
        tmp_path, session_mod.Session(provider="anthropic", image_provider="openai")
    )
    repl = _repl(tmp_path, provider_name="anthropic", api_key="sk-ant-test")

    repl._restore_image_provider_from_session()

    assert repl._image_provider is None


# --- /image-model status + error surfaces -------------------------------


def test_image_model_rejects_an_unknown_provider_name(tmp_path):
    repl, buf = _interactive_repl(tmp_path, inputs=["/image-model dall-e", "/exit"])

    repl.run()

    out = buf.getvalue()
    assert "dall-e" in out
    assert "/image-model openai" in out
    assert repl._image_provider is None


def test_image_model_status_reports_explicit_off(tmp_path):
    repl, buf = _interactive_repl(
        tmp_path, inputs=["/image-model none", "/image-model", "/exit"]
    )

    repl.run()

    assert "off" in buf.getvalue().lower()


def test_image_model_status_reports_the_explicit_provider(tmp_path):
    repl, buf = _interactive_repl(tmp_path, inputs=["/image-model", "/exit"])
    repl._image_provider = _FakeImageProvider()
    repl._image_provider_label = "openai"

    repl.run()

    out = buf.getvalue()
    assert "openai" in out and "explicit" in out.lower()


def test_image_model_status_reports_the_auto_wired_provider(tmp_path):
    """chat=OpenAI auto-wires the image slot without a label. The
    status line must say so instead of claiming nothing is set."""
    repl, buf = _interactive_repl(
        tmp_path,
        inputs=["/image-model", "/exit"],
        chat_provider="openai",
        chat_key="sk-openai-test",
    )

    repl.run()

    assert repl._image_provider_label is None
    assert "auto-derived" in buf.getvalue().lower()


def test_image_model_none_on_an_empty_slot_says_nothing_was_set(tmp_path):
    repl, buf = _interactive_repl(tmp_path, inputs=["/image-model none", "/exit"])

    repl.run()

    assert "no image provider was set" in buf.getvalue().lower()
    assert repl._image_provider_label == "none"


def test_image_model_openai_reports_a_build_without_the_openai_provider(
    tmp_path, monkeypatch
):
    """Defensive: if the openai spec ever disappears from the provider
    registry, say so instead of crashing on ``None.display_name``."""
    from src import repl as repl_mod

    repl, buf = _interactive_repl(tmp_path, inputs=["/image-model openai", "/exit"])
    monkeypatch.setattr(repl_mod, "find", lambda _name: None)

    repl.run()

    assert "isn't available" in buf.getvalue()
    assert repl._image_provider is None
