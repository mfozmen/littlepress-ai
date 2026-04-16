"""Unit tests for the LLMProvider implementations in src/providers/llm.py."""

import sys
import types

import pytest

from src.providers.llm import (
    AnthropicProvider,
    GoogleProvider,
    LLMProvider,
    NullProvider,
    create_provider,
    find,
)


def _fake_anthropic_module(reply_text="hello from claude"):
    class Messages:
        def __init__(self):
            self.last_kwargs: dict = {}

        def create(self, **kwargs):
            self.last_kwargs = kwargs
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text=reply_text)]
            )

    class Client:
        last_client: "Client | None" = None

        def __init__(self, *, api_key, timeout=None):
            self.api_key = api_key
            self.timeout = timeout
            self.messages = Messages()
            Client.last_client = self

    module = types.ModuleType("anthropic")
    module.Anthropic = Client
    return module


def test_null_provider_chat_raises():
    with pytest.raises(NotImplementedError):
        NullProvider().chat([{"role": "user", "content": "hi"}])


def test_anthropic_provider_returns_reply_text(monkeypatch):
    fake = _fake_anthropic_module(reply_text="the dragon is fine")
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    provider = AnthropicProvider(api_key="sk-test")
    reply = provider.chat([{"role": "user", "content": "how is the dragon?"}])

    assert reply == "the dragon is fine"


def test_anthropic_provider_forwards_messages(monkeypatch):
    fake = _fake_anthropic_module()
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    provider = AnthropicProvider(api_key="sk-test")
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ]
    provider.chat(msgs)

    assert fake.Anthropic.last_client.messages.last_kwargs["messages"] == msgs


def test_anthropic_provider_passes_bounded_timeout_to_sdk(monkeypatch):
    """Regression guard: the SDK default timeout (~600 s) would freeze
    the REPL on a flaky network. The chat client must be constructed with
    a short, finite timeout — see PR #6 for the validator's equivalent."""
    fake = _fake_anthropic_module()
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    AnthropicProvider(api_key="sk").chat([{"role": "user", "content": "hi"}])

    timeout = fake.Anthropic.last_client.api_key  # sanity — client was built
    assert timeout == "sk"
    # The keyword actually forwarded to Anthropic():
    last = fake.Anthropic.last_client
    # Stored on the fake in the last_kwargs of the builder — we need to
    # inspect the constructor arg directly.
    # Rebuild assertion: the fake client stores timeout alongside api_key.
    assert hasattr(last, "timeout")
    assert last.timeout is not None and 0 < last.timeout <= 300


def test_anthropic_provider_without_sdk_raises_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)

    provider = AnthropicProvider(api_key="sk-test")
    with pytest.raises(ImportError):
        provider.chat([{"role": "user", "content": "hi"}])


def test_create_provider_returns_null_for_offline_spec():
    spec = find("none")
    provider = create_provider(spec, api_key=None)
    assert isinstance(provider, NullProvider)


def test_create_provider_returns_anthropic_with_key():
    spec = find("anthropic")
    provider = create_provider(spec, api_key="sk-test")
    assert isinstance(provider, AnthropicProvider)


def test_create_provider_falls_back_to_null_for_unwired_providers():
    # OpenAI / Ollama haven't shipped chat() yet. The factory hands
    # back a NullProvider so the REPL keeps working with the
    # "(no model selected)" placeholder until they land.
    for name in ("openai", "ollama"):
        spec = find(name)
        provider = create_provider(spec, api_key="x")
        assert isinstance(provider, NullProvider)


def test_create_provider_returns_google_with_key():
    spec = find("google")
    provider = create_provider(spec, api_key="AIzaFake")
    assert isinstance(provider, GoogleProvider)


def test_llm_provider_is_usable_as_type_hint():
    # Both implementations satisfy the protocol — a sanity check that
    # future code can type-hint LLMProvider without importing either
    # implementation.
    p: LLMProvider = NullProvider()
    q: LLMProvider = AnthropicProvider(api_key="x")
    assert p is not None and q is not None


# --- turn() (tool-use) ----------------------------------------------------


def _fake_anthropic_with_turn(response_blocks, stop_reason="end_turn"):
    """Fake SDK whose messages.create returns the given content blocks."""
    class Block:
        """Small shim that supports both attribute and dict access."""

        def __init__(self, data):
            self._data = data

        def __getattr__(self, name):
            if name in self._data:
                return self._data[name]
            raise AttributeError(name)

        def model_dump(self):
            return dict(self._data)

    class Messages:
        def __init__(self):
            self.last_kwargs: dict = {}

        def create(self, **kwargs):
            self.last_kwargs = kwargs
            return types.SimpleNamespace(
                content=[Block(b) for b in response_blocks],
                stop_reason=stop_reason,
            )

    class Client:
        last_client = None

        def __init__(self, *, api_key, timeout=None):
            self.api_key = api_key
            self.timeout = timeout
            self.messages = Messages()
            Client.last_client = self

    module = types.ModuleType("anthropic")
    module.Anthropic = Client
    return module


def test_null_provider_turn_raises():
    from src.agent import Tool

    tool = Tool(
        name="noop",
        description="",
        input_schema={"type": "object", "properties": {}},
        handler=lambda _i: "ok",
    )
    with pytest.raises(NotImplementedError):
        NullProvider().turn([], [tool])


def test_anthropic_provider_turn_returns_text_response(monkeypatch):
    fake = _fake_anthropic_with_turn(
        [{"type": "text", "text": "hi there"}],
        stop_reason="end_turn",
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    response = AnthropicProvider(api_key="sk").turn(
        [{"role": "user", "content": "hello"}], tools=[]
    )

    assert response.stop_reason == "end_turn"
    assert response.content == [{"type": "text", "text": "hi there"}]


def test_anthropic_provider_turn_returns_tool_use_response(monkeypatch):
    fake = _fake_anthropic_with_turn(
        [
            {"type": "text", "text": "let me check"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "read_draft",
                "input": {},
            },
        ],
        stop_reason="tool_use",
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    response = AnthropicProvider(api_key="sk").turn(
        [{"role": "user", "content": "what's in the draft?"}],
        tools=[],
    )

    assert response.stop_reason == "tool_use"
    names = [b.get("name") for b in response.content if b.get("type") == "tool_use"]
    assert names == ["read_draft"]


def test_anthropic_provider_turn_forwards_tool_schemas_to_sdk(monkeypatch):
    from src.agent import Tool

    fake = _fake_anthropic_with_turn([{"type": "text", "text": "ok"}])
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    tool = Tool(
        name="read_draft",
        description="Read the loaded PDF draft",
        input_schema={"type": "object", "properties": {}},
        handler=lambda _i: "",
    )
    AnthropicProvider(api_key="sk").turn(
        [{"role": "user", "content": "hi"}],
        tools=[tool],
    )

    last = fake.Anthropic.last_client.messages.last_kwargs
    assert "tools" in last
    assert last["tools"] == [
        {
            "name": "read_draft",
            "description": "Read the loaded PDF draft",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_anthropic_provider_turn_uses_bounded_timeout(monkeypatch):
    fake = _fake_anthropic_with_turn([{"type": "text", "text": "ok"}])
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    AnthropicProvider(api_key="sk").turn(
        [{"role": "user", "content": "hi"}],
        tools=[],
    )
    timeout = fake.Anthropic.last_client.timeout
    assert timeout is not None and 0 < timeout <= 300


def test_block_to_dict_handles_blocks_without_model_dump(monkeypatch):
    """Older SDK versions return attribute-only objects (no model_dump).
    The converter must still produce correct dicts for text and tool_use."""
    from src.providers.llm import _block_to_dict

    class PlainText:
        type = "text"
        text = "hi"

    class PlainToolUse:
        type = "tool_use"
        id = "t1"
        name = "read_draft"
        input = {"k": "v"}

    class Mystery:
        type = "unknown_block_type"

    assert _block_to_dict(PlainText()) == {"type": "text", "text": "hi"}
    assert _block_to_dict(PlainToolUse()) == {
        "type": "tool_use",
        "id": "t1",
        "name": "read_draft",
        "input": {"k": "v"},
    }
    # Unknown blocks at least carry their type — the agent can ignore them.
    assert _block_to_dict(Mystery()) == {"type": "unknown_block_type"}


# --- GoogleProvider (Gemini) -------------------------------------------


def _install_fake_google_genai(monkeypatch, *, response_parts=None, reply_text=None):
    """Install a minimal fake of ``google.genai`` so GoogleProvider
    can be exercised without network access.

    ``response_parts`` — list of Part-like objects to hand back from
    ``generate_content``. If None, a plain text response is produced.
    ``reply_text`` — the ``response.text`` value when using the chat
    happy path (the SDK exposes ``.text`` as a convenience).
    """

    class Content:
        def __init__(self, role=None, parts=None):
            self.role = role
            self.parts = list(parts or [])

    class Part:
        def __init__(self, text=None, function_call=None, function_response=None):
            self.text = text
            self.function_call = function_call
            self.function_response = function_response

        @classmethod
        def from_text(cls, text):
            return cls(text=text)

        @classmethod
        def from_function_response(cls, name, response):
            return cls(function_response={"name": name, "response": response})

    class FunctionCall:
        def __init__(self, name, args=None, id=None):
            self.name = name
            self.args = args or {}
            self.id = id

    class FunctionDeclaration:
        def __init__(self, name, description=None, parameters=None):
            self.name = name
            self.description = description
            self.parameters = parameters

    class Tool:
        def __init__(self, function_declarations=None):
            self.function_declarations = function_declarations or []

    class GenerateContentConfig:
        def __init__(self, tools=None):
            self.tools = tools or []

    types_mod = types.ModuleType("google.genai.types")
    types_mod.Content = Content
    types_mod.Part = Part
    types_mod.FunctionCall = FunctionCall
    types_mod.FunctionDeclaration = FunctionDeclaration
    types_mod.Tool = Tool
    types_mod.GenerateContentConfig = GenerateContentConfig

    class Candidate:
        def __init__(self, content):
            self.content = content

    class Response:
        def __init__(self):
            self.text = reply_text or ""
            if response_parts is not None:
                self.candidates = [
                    Candidate(Content(role="model", parts=response_parts))
                ]
            else:
                self.candidates = []

    class Models:
        def __init__(self):
            self.last_kwargs: dict = {}

        def generate_content(self, **kwargs):
            self.last_kwargs = kwargs
            return Response()

    class Client:
        last_client: "Client | None" = None

        def __init__(self, *, api_key, **kw):
            self.api_key = api_key
            self.kwargs = kw
            self.models = Models()
            Client.last_client = self

    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = Client
    genai_mod.types = types_mod

    google_mod = types.ModuleType("google")
    google_mod.genai = genai_mod

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)
    return genai_mod, types_mod


def test_google_provider_chat_returns_reply_text(monkeypatch):
    _install_fake_google_genai(monkeypatch, reply_text="hello from gemini")

    reply = GoogleProvider(api_key="AIzaFake").chat(
        [{"role": "user", "content": "hi"}]
    )

    assert reply == "hello from gemini"


def test_google_provider_chat_translates_user_messages_to_gemini_contents(monkeypatch):
    genai_mod, types_mod = _install_fake_google_genai(
        monkeypatch, reply_text="ok"
    )

    GoogleProvider(api_key="k").chat(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
    )

    sent = genai_mod.Client.last_client.models.last_kwargs
    contents = sent["contents"]
    # Three contents with alternating roles (user/model/user) and text parts.
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].text == "first"
    assert contents[1].parts[0].text == "reply"
    assert contents[2].parts[0].text == "second"


def test_google_provider_without_sdk_raises_import_error(monkeypatch):
    # Simulate the SDK being unavailable.
    monkeypatch.setitem(sys.modules, "google", None)
    monkeypatch.setitem(sys.modules, "google.genai", None)

    with pytest.raises(ImportError):
        GoogleProvider(api_key="k").chat([{"role": "user", "content": "hi"}])


def test_google_provider_turn_returns_text_response(monkeypatch):
    genai_mod, types_mod = _install_fake_google_genai(
        monkeypatch,
        response_parts=[types.ModuleType("placeholder")],  # replaced below
    )
    # Build a real text Part using the fake types module.
    text_part = types_mod.Part.from_text(text="hello there")
    # Replace the response_parts by reinstalling with the right Part.
    _install_fake_google_genai(monkeypatch, response_parts=[text_part])

    response = GoogleProvider(api_key="k").turn(
        [{"role": "user", "content": "hi"}], tools=[]
    )

    assert response.stop_reason == "end_turn"
    assert response.content == [{"type": "text", "text": "hello there"}]


def test_google_provider_turn_returns_tool_use_when_model_calls_a_function(monkeypatch):
    _, types_mod = _install_fake_google_genai(monkeypatch)
    parts = [
        types_mod.Part.from_text(text="let me check"),
        types_mod.Part(
            function_call=types_mod.FunctionCall(
                name="read_draft", args={}
            ),
        ),
    ]
    _install_fake_google_genai(monkeypatch, response_parts=parts)

    response = GoogleProvider(api_key="k").turn(
        [{"role": "user", "content": "what's in the draft?"}], tools=[]
    )

    assert response.stop_reason == "tool_use"
    kinds = [b["type"] for b in response.content]
    assert kinds == ["text", "tool_use"]
    tool_block = response.content[1]
    assert tool_block["name"] == "read_draft"
    # An id is synthesised so the agent can match tool_use → tool_result
    # even when Gemini didn't return one.
    assert tool_block["id"]
    assert tool_block["input"] == {}


def test_google_provider_turn_forwards_tool_schemas_to_sdk(monkeypatch):
    from src.agent import Tool as AgentTool

    genai_mod, types_mod = _install_fake_google_genai(
        monkeypatch,
        response_parts=[types.ModuleType("placeholder")],
    )
    text_part = types_mod.Part.from_text(text="ok")
    _install_fake_google_genai(monkeypatch, response_parts=[text_part])
    # Re-resolve — the above reinstalls the module, refetch types.
    import importlib
    genai_mod = sys.modules["google.genai"]
    types_mod = sys.modules["google.genai.types"]

    tool = AgentTool(
        name="read_draft",
        description="Read the loaded PDF draft",
        input_schema={"type": "object", "properties": {}},
        handler=lambda _i: "",
    )
    GoogleProvider(api_key="k").turn(
        [{"role": "user", "content": "hi"}], tools=[tool]
    )

    sent = genai_mod.Client.last_client.models.last_kwargs
    config = sent["config"]
    assert len(config.tools) == 1
    fn_decls = config.tools[0].function_declarations
    assert [fd.name for fd in fn_decls] == ["read_draft"]
    assert fn_decls[0].description == "Read the loaded PDF draft"
    assert fn_decls[0].parameters == {"type": "object", "properties": {}}


def test_google_provider_turn_translates_tool_result_messages_back_to_gemini(monkeypatch):
    """A full turn — the agent sends back a tool_result from a prior
    tool_use. Translation must surface it as a ``function_response``
    Part on a ``tool``-role Content so Gemini can continue the
    conversation."""
    _, types_mod = _install_fake_google_genai(monkeypatch)
    text_part = types_mod.Part.from_text(text="done")
    genai_mod, types_mod = _install_fake_google_genai(
        monkeypatch, response_parts=[text_part]
    )

    messages = [
        {"role": "user", "content": "read the draft"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "sure"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read_draft",
                    "input": {},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "Title: Book\n1 pages.",
                }
            ],
        },
    ]

    GoogleProvider(api_key="k").turn(messages, tools=[])

    sent = genai_mod.Client.last_client.models.last_kwargs
    contents = sent["contents"]
    roles = [c.role for c in contents]
    # user → model → tool
    assert roles == ["user", "model", "tool"]
    # The function_response part carries the tool name (looked up from
    # the prior tool_use) and the content verbatim.
    fr_part = contents[2].parts[0]
    assert fr_part.function_response["name"] == "read_draft"
    assert fr_part.function_response["response"] == {
        "result": "Title: Book\n1 pages."
    }


def test_google_provider_turn_handles_empty_response(monkeypatch):
    """A response with no candidates (e.g. safety blocking) must not
    crash — the turn returns end_turn with no content."""
    _install_fake_google_genai(monkeypatch, response_parts=None)

    response = GoogleProvider(api_key="k").turn(
        [{"role": "user", "content": "hi"}], tools=[]
    )

    assert response.stop_reason == "end_turn"
    assert response.content == []
