"""Agent loop unit tests.

The Agent drives a tool-use conversation with an LLM. These tests use a
scriptable fake LLM so the loop's behaviour is pinned without a network.
"""

import io

from rich.console import Console

from src.agent import _NUDGE, Agent, AgentResponse, Tool


class _ScriptedLLM:
    """Returns a queued list of AgentResponses in order.

    Tests script what the LLM 'would say' and then assert on the
    resulting tool calls / conversation.
    """

    def __init__(self, responses: list[AgentResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []  # (messages, tool_names) each call

    def turn(self, messages, tools):
        self.calls.append(
            {"messages": list(messages), "tool_names": [t.name for t in tools]}
        )
        if not self._responses and messages[-1]["content"] == _NUDGE:
            return _text("")
        if not self._responses:
            raise AssertionError("scripted LLM ran out of responses")
        return self._responses.pop(0)


def _text(*chunks: str) -> AgentResponse:
    return AgentResponse(
        content=[{"type": "text", "text": t} for t in chunks],
        stop_reason="end_turn",
    )


def _tool_call(tool_id: str, name: str, input_: dict) -> AgentResponse:
    return AgentResponse(
        content=[{"type": "tool_use", "id": tool_id, "name": name, "input": input_}],
        stop_reason="tool_use",
    )


def _make_agent(llm, tools=None):
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    return Agent(llm=llm, tools=tools or [], console=console), buf


def test_agent_say_prints_plain_text_response():
    llm = _ScriptedLLM([_text("hello there")])
    agent, buf = _make_agent(llm)

    agent.say("hi")

    assert "hello there" in buf.getvalue()
    assert len(llm.calls) == 1


def test_agent_loops_on_tool_use_until_text_response():
    def handler(_input):
        return "42"

    tool = Tool(
        name="answer",
        description="Returns the answer",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )
    llm = _ScriptedLLM(
        [
            _tool_call("call-1", "answer", {}),
            _text("the answer is 42 — anything else?"),
        ]
    )
    agent, buf = _make_agent(llm, tools=[tool])

    agent.say("what's the answer?")

    assert "the answer is 42" in buf.getvalue()
    # Two LLM round-trips: one asked for the tool, one replied after the result.
    assert len(llm.calls) == 2
    # Second call's messages include the tool_use and the tool_result.
    second = llm.calls[1]["messages"]
    # tool_use block present in assistant turn
    assistant_blocks = [m for m in second if m["role"] == "assistant"]
    assert any(
        b.get("type") == "tool_use"
        for m in assistant_blocks
        for b in (m["content"] if isinstance(m["content"], list) else [])
    )
    # tool_result present in user turn
    user_blocks = [m for m in second if m["role"] == "user"]
    assert any(
        isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" for b in m["content"])
        for m in user_blocks
    )


def test_agent_captures_tool_result_text_verbatim():
    def handler(input_):
        return f"echoed: {input_['msg']}"

    tool = Tool(
        name="echo",
        description="Echo back the input",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        handler=handler,
    )
    llm = _ScriptedLLM(
        [
            _tool_call("c1", "echo", {"msg": "preserve this verbatim"}),
            _text("done"),
        ]
    )
    agent, _ = _make_agent(llm, tools=[tool])

    agent.say("echo something")

    # The tool result block carries the exact string the handler returned,
    # with no reformatting / rewriting by the agent layer.
    second = llm.calls[1]["messages"]
    for msg in second:
        if msg["role"] == "user" and isinstance(msg["content"], list):
            for block in msg["content"]:
                if block.get("type") == "tool_result":
                    assert block["content"] == "echoed: preserve this verbatim"
                    return
    raise AssertionError("tool_result block not found")


def test_unknown_tool_name_reports_error_without_crashing():
    llm = _ScriptedLLM(
        [
            _tool_call("c1", "does_not_exist", {}),
            _text("sorry"),
        ]
    )
    agent, _ = _make_agent(llm)

    # Must not raise — the agent should inject an error tool_result and
    # let the LLM recover.
    agent.say("hi")

    second = llm.calls[1]["messages"]
    for msg in second:
        if msg["role"] == "user" and isinstance(msg["content"], list):
            for block in msg["content"]:
                if block.get("type") == "tool_result":
                    assert "error" in block["content"].lower()
                    return
    raise AssertionError("error tool_result block not found")


def test_tool_handler_exception_becomes_error_tool_result():
    def boom(_input):
        raise RuntimeError("disk full")

    tool = Tool(
        name="boom",
        description="",
        input_schema={"type": "object", "properties": {}},
        handler=boom,
    )
    llm = _ScriptedLLM(
        [
            _tool_call("c1", "boom", {}),
            _text("recovered"),
        ]
    )
    agent, _ = _make_agent(llm, tools=[tool])

    agent.say("try it")

    # The exception text surfaces in the tool_result so the LLM can react.
    second = llm.calls[1]["messages"]
    for msg in second:
        if msg["role"] == "user" and isinstance(msg["content"], list):
            for block in msg["content"]:
                if block.get("type") == "tool_result":
                    assert "disk full" in block["content"]
                    return
    raise AssertionError("error tool_result block not found")


def test_agent_remembers_previous_turns():
    llm = _ScriptedLLM([_text("one"), _text("two")])
    agent, _ = _make_agent(llm)

    agent.say("first")
    agent.say("second")

    # Second call's messages include the first user turn and assistant reply.
    second_call = llm.calls[1]["messages"]
    contents = [m for m in second_call]
    assert contents[0] == {"role": "user", "content": "first"}
    # Assistant reply from first turn is preserved.
    assert contents[1]["role"] == "assistant"


def test_assistant_response_with_text_and_tool_use_mixed():
    """Claude can emit text *and* a tool_use in the same response
    ("let me check..." → call tool). Both must be handled: the text
    block prints, the tool_use fires, and non-tool_use blocks are
    skipped when building tool_results."""
    results = []

    def handler(_input):
        results.append("ran")
        return "ok"

    tool = Tool(
        name="collect",
        description="",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )
    mixed = AgentResponse(
        content=[
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "id": "a", "name": "collect", "input": {}},
        ],
        stop_reason="tool_use",
    )
    llm = _ScriptedLLM([mixed, _text("done")])
    agent, buf = _make_agent(llm, tools=[tool])

    agent.say("do it")

    assert results == ["ran"]
    assert "let me check" in buf.getvalue()


def test_multiple_tool_calls_in_one_turn_all_executed():
    """Some LLM responses contain several tool_use blocks at once."""
    results = []

    def handler(input_):
        results.append(input_["x"])
        return "ok"

    tool = Tool(
        name="collect",
        description="",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
        handler=handler,
    )
    multi = AgentResponse(
        content=[
            {"type": "tool_use", "id": "a", "name": "collect", "input": {"x": "one"}},
            {"type": "tool_use", "id": "b", "name": "collect", "input": {"x": "two"}},
        ],
        stop_reason="tool_use",
    )
    llm = _ScriptedLLM([multi, _text("done")])
    agent, _ = _make_agent(llm, tools=[tool])

    agent.say("do both")

    assert results == ["one", "two"]


# --- progress indicator --------------------------------------------------


class _ProbeLLM(_ScriptedLLM):
    """Records what the progress indicator showed while ``turn`` ran."""

    agent: Agent

    def __init__(self, responses):
        super().__init__(responses)
        self.seen: list[tuple[str, bool]] = []

    def turn(self, messages, tools):
        progress = self.agent._progress
        task = progress.tasks[0]
        self.seen.append((task.description, progress.live.is_started))
        return super().turn(messages, tools)


def test_agent_shows_a_live_indicator_while_the_llm_thinks(monkeypatch):
    """A slow LLM turn used to leave the terminal silent, so users
    thought the app had hung."""
    monkeypatch.setenv("LITTLEPRESS_LANG", "en")
    llm = _ProbeLLM([_text("done")])
    agent, _ = _make_agent(llm)
    llm.agent = agent

    agent.say("hi")

    assert llm.seen == [("Thinking…", True)]
    assert agent._progress is None


def test_agent_labels_the_indicator_with_the_running_tool(monkeypatch):
    monkeypatch.setenv("LITTLEPRESS_LANG", "tr")
    seen: list[str] = []
    agent_ref: list[Agent] = []

    def handler(_input):
        seen.append(agent_ref[0]._progress.tasks[0].description)
        return "ok"

    tool = Tool("generate_cover_illustration", "", {"type": "object"}, handler)
    agent, _ = _make_agent(
        _ScriptedLLM([_tool_call("t1", tool.name, {}), _text("done")]), [tool]
    )
    agent_ref.append(agent)

    agent.say("make a cover")

    assert seen == ["Kapak resmi üretiliyor…"]


def test_unknown_tools_get_a_generic_working_label(monkeypatch):
    monkeypatch.setenv("LITTLEPRESS_LANG", "en")
    seen: list[str] = []
    agent_ref: list[Agent] = []

    def handler(_input):
        seen.append(agent_ref[0]._progress.tasks[0].description)
        return "ok"

    tool = Tool("set_metadata", "", {"type": "object"}, handler)
    agent, _ = _make_agent(
        _ScriptedLLM([_tool_call("t1", tool.name, {}), _text("done")]), [tool]
    )
    agent_ref.append(agent)

    agent.say("x")

    assert seen == ["Working…"]


def test_paused_hides_the_indicator_and_resumes_it(monkeypatch):
    """Tools that ask the user y/n must not type into a live spinner."""
    states: list[bool] = []
    agent_ref: list[Agent] = []

    def handler(_input):
        progress = agent_ref[0]._progress
        with agent_ref[0].paused():
            states.append(progress.live.is_started)
        states.append(progress.live.is_started)
        return "ok"

    tool = Tool("t", "", {"type": "object"}, handler)
    agent, _ = _make_agent(
        _ScriptedLLM([_tool_call("t1", "t", {}), _text("done")]), [tool]
    )
    agent_ref.append(agent)

    agent.say("x")

    assert states == [False, True]


def test_paused_is_a_no_op_when_nothing_is_running():
    agent, _ = _make_agent(_ScriptedLLM([]))

    with agent.paused():
        pass

    assert agent._progress is None


def test_indicator_falls_back_to_ascii_on_a_non_utf8_console():
    """A cp1254 / cp1252 stream can't encode the braille spinner; the
    indicator must not crash the turn it is decorating."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1254", errors="strict")
    console = Console(file=stream, force_terminal=True, width=80)
    agent = Agent(llm=None, tools=[], console=console)

    with agent._working("Kapak resmi üretiliyor…"):
        agent._progress.refresh()

    stream.flush()


# --- stalled turns -----------------------------------------------------
# Live session 2026-10-25: after read_draft the model wrote "I'll now
# finish the cover and render the PDF." and stopped, so the REPL sat at
# its prompt while the user waited for work that never started.


def test_agent_nudges_once_when_it_stops_after_tools_without_asking():
    llm = _ScriptedLLM(
        [
            _tool_call("t1", "read_draft", {}),
            _text("Now I'll render the PDF."),
            _tool_call("t2", "render_book", {}),
            _text("Done. Which pages have issues?"),
        ]
    )
    calls = []
    tools = [
        Tool(name=n, description="", input_schema={"type": "object"},
             handler=lambda _i, n=n: calls.append(n) or "ok")
        for n in ("read_draft", "render_book")
    ]
    agent, _ = _make_agent(llm, tools)

    agent.say("go")

    assert calls == ["read_draft", "render_book"]
    nudge = llm.calls[2]["messages"][-1]
    assert nudge["role"] == "user"
    assert "do it now" in nudge["content"]


def test_agent_nudges_at_most_once_per_user_message():
    llm = _ScriptedLLM(
        [
            _tool_call("t1", "read_draft", {}),
            _text("I'll render now."),
            _text("Rendering soon."),
        ]
    )
    tool = Tool(name="read_draft", description="", input_schema={"type": "object"},
                handler=lambda _i: "ok")
    agent, buf = _make_agent(llm, [tool])

    agent.say("go")

    assert "Rendering soon." in buf.getvalue()


def test_agent_does_not_nudge_a_question_or_a_tool_free_reply():
    llm = _ScriptedLLM(
        [
            _tool_call("t1", "read_draft", {}),
            _text("Shall I use this cover prompt?"),
            _text("Thanks, enjoy the book."),
        ]
    )
    tool = Tool(name="read_draft", description="", input_schema={"type": "object"},
                handler=lambda _i: "ok")
    agent, _ = _make_agent(llm, [tool])

    agent.say("go")
    agent.say("yes, done")

    assert len(llm.calls) == 3


def test_an_empty_reply_to_the_nudge_prints_nothing():
    llm = _ScriptedLLM(
        [
            _tool_call("t1", "read_draft", {}),
            _text("All set."),
            _text(""),
        ]
    )
    tool = Tool(name="read_draft", description="", input_schema={"type": "object"},
                handler=lambda _i: "ok")
    agent, buf = _make_agent(llm, [tool])

    agent.say("go")

    assert buf.getvalue().strip() == "All set."
